import time
import threading
import json, gzip, zlib


from sharedmemory import MMapControl
from lmu_data import LMUObjectOut, LMUConstants
from dataclasses import asdict
from queue import Queue
from datetime import datetime
from memory import (
    connect_db,
    start_session,
    save_event,
    finish_session,
    save_lap,
    save_corner_stat,
    print_corner_report,
    set_session_track,
    save_radio,
    save_llm_call,
    save_session_result,
    save_car_settings,
    save_rivals,
    save_opponent_corners,
    save_pass_attempts,
)
from radio import Governor, Budget, Call, RACE_CONTROL
from seats.settle import RaceSettle
from seats.track_awareness import TrackAwareness
from seats.qualifying import QualifyingEngineer
from orders import StandingOrders
from persona import Persona
from voice import Voice, RadioDesk
from seats.performance import PerformanceEngineer
from seats import Moment
from seats.spotter import Spotter
from seats.race_engineer import RaceEngineer
from seats.strategist import Strategist
from seats.racecraft import Racecraft
from seats.memory_recall import MemoryRecall
from answers import Answers, needs_agent, intent_of, fix_mishearing, garbled, is_mark
from agent import RaceAgent, Snapshot
from race_model import RaceModel
import ptt as push_to_talk
from team_memory import facts as memory_facts
from race_state import (
    race_snapshot_from_dict,
    near_cars_from_dict,
)
from track_map import (
    CornerMap,
    corners_for_track,
    TrackMapLearner,
    save_map,
    TURNING,
)
from game.constants import GREEN_FLAG, RACE_SESSIONS, SESSION_OVER
from driving.laps import LapCounter, LapDistance
from driving.corner_stats import CornerStats
from driving.detectors import (
    ContactDetection,
    CornerEntryDetection,
    HardBrakingDetector,
    LockUpDetector,
    OffTrackDetector,
    RearSnapDetector,
    SlideCaughtDetector,
    SpinDetector,
    ThrottleLift,
    WheelspinDetector,
)
from game.car_frame import CarState
from game.live_source import LiveSource


TAPE_PATH = "tape_60hz_clean.jsonl.gz"
# Gourav's cap: Rs 5 (23 Sep 2026), raised to Rs 10 (25 Sep, "increase the budget a bit").
# Past it, template lines only. Push-to-talk is NOT capped (his call, 25 Sep: "I don't want it
# to stop"): its spend is still charged here and logged, it just never refuses a question.
BUDGET_PER_SESSION_RS = 10.0


class ScriptedTalk:
    """His voice for a replay: what he says and when, so orders can be tested on a real tape without
    a race (26 Sep). script = [(laps_done, seconds_into_that_lap, words)], said once each, in order,
    at the first race snapshot past that point. The game's lap count, the same one his follows.
    laps_done = None: seconds is the sim time itself (tools/replay_orders.py aims at a known call)."""

    def __init__(self, source, script):
        self.source = source
        self.script = list(script)  # said in the order written
        self.lap_started = {}  # laps done -> sim time the game first showed it

    def poll(self):
        race = self.source.race
        if race is None or race.me is None or not self.source.new_race:
            return []
        self.lap_started.setdefault(race.me.laps, race.sim_time)
        heard = []
        while self.script:
            laps, into, words = self.script[0]
            if laps is None:
                if race.sim_time < into:
                    break
            else:
                start = self.lap_started.get(laps)
                if race.me.laps < laps or start is None or race.sim_time < start + into:
                    break
            self.script.pop(0)
            heard.append(push_to_talk.Heard(words, 1.5, 0, confidence=-0.2))
        return heard

    def set_track_words(self, corner_names):
        pass  # no Whisper to prime: the words are already written

    def close(self):
        pass


class ReplaySource:
    def __init__(self, speed, tape_path=TAPE_PATH):
        self.speed = speed
        self.tape_path = tape_path
        self.race = None
        self.new_race = False
        self.near = None
        print("Connected.")
        print("Press Ctrl+C to stop.\n")

    def __iter__(self):
        try:
            with gzip.open(self.tape_path, "rt") as f:
                start_wall = time.perf_counter()
                start_sim = None
                for line in f:
                    as_dict = json.loads(line)
                    # v2 tapes interleave race lines with the car frames; each one belongs
                    # to the car frame written right after it. Old tapes have only car frames.
                    kind = as_dict.get("t")
                    if kind == "race":
                        self.race = race_snapshot_from_dict(as_dict)
                        self.new_race = True
                        continue
                    if kind == "near":
                        self.near = near_cars_from_dict(as_dict)
                        continue
                    as_data = CarState(**as_dict)
                    if start_sim is None:
                        start_sim = as_data.elapsed_time
                    if self.speed:
                        target = (
                            start_wall + (as_data.elapsed_time - start_sim) / self.speed
                        )
                        delay = target - time.perf_counter()
                        # running late: never skip the frame, just don't wait for it
                        if delay > 0:
                            time.sleep(delay)
                    yield as_data
                    # a snapshot is "new" for one frame only, and near cars belong to one frame
                    self.new_race = False
                    self.near = None

        except (EOFError, zlib.error):
            # a tape cut off when Apex was killed (24 Sep): everything up to the cut is real
            print("[the tape ends early - it was cut off; replayed up to the cut]")
        except KeyboardInterrupt:
            print("\nStopping...")
            print("Closed connection.")


class Recorder:
    def __init__(self, path):
        # path is unique per session - a hardcoded name silently overwrote the
        # previous session's tape every run.
        self.path = path
        self.q = Queue()
        self.writer_thread = threading.Thread(target=self._writer_loop)
        self.writer_thread.start()

    def record(self, frame):
        self.q.put(frame)

    def _writer_loop(self):
        written = 0
        with gzip.open(self.path, "wt") as f:
            while True:
                item = self.q.get()
                if item is None:
                    break

                as_dict = asdict(item)
                as_text = json.dumps(as_dict)
                f.write(as_text + "\n")
                written += 1
                # every few seconds, a sync point: if Apex is killed, everything up to here
                # still reads back (24 Sep: a force-stopped run left a tape cut mid-write)
                if written % 600 == 0:
                    f.flush()

    def stop(self):
        self.q.put(None)
        self.writer_thread.join()


# SESSION_OVER (phase 8) comes when the LEADER takes the flag. On 23 Sep Apex stopped right there, with
# Gourav still 400 m from his own finish line: every non-leader lost the end of the race.
# So Apex waits for my own car to finish, then leaves the engineer time to call the result.
FINISHED_GRACE_S = 10.0
FLAG_TIMEOUT_S = (
    420.0  # a car that never takes the flag (parked, crashed out): stop anyway
)


def database_file(conn):
    """The file behind this connection, so the agent can open its own read-only one."""
    return conn.execute("PRAGMA database_list").fetchone()[2] or None


def due_reminders(reminders, lap, now):
    """The reminders he asked for (through the agent) that fall on this lap: said at the line."""
    due = [r for r in reminders if r["remind_lap"] <= lap]
    for reminder in due:
        reminders.remove(reminder)
    return [
        Call(
            seat="race_engineer",
            kind="REMINDER",
            sim_time=now,
            priority=RACE_CONTROL,
            ttl=20.0,
            conclusion=f"Reminder: {r['what']}.",
            template=f"Reminder: {r['what']}.",
            asked=True,
        )
        for r in due
    ]


def contacts_by_car(conn, session_id):
    """Contacts this session, per other car, for the agent's driver tool."""
    counts = {}
    for other_car, count in conn.execute(
        "SELECT other_car, COUNT(*) FROM events WHERE session_id = ? AND kind = 'CONTACT' GROUP BY other_car",
        (session_id,),
    ):
        counts[other_car] = count
    return counts


def run_session(
    replay,
    replay_speed,
    tape_path=TAPE_PATH,
    out_loud=None,
    clean=False,
    persona=None,
    launch_id=None,
    voice=None,
    script=None,
):
    """One LMU session, start to finish. Returns the database id of the session.

    script:   replays only: what he says and when (see ScriptedTalk), no microphone, no coach.
    out_loud: speak through the speakers (default) or print lines (fast replays, tests).
    clean:    no swearing, for recordings other people will hear.
    persona:  who phrases the lines; tests pass a fake so no LLM call is ever made."""
    REPLAY = replay
    REPLAY_SPEED = replay_speed
    if out_loud is None:
        # a replay at max speed prints its lines, like v1 did
        out_loud = not (REPLAY and not REPLAY_SPEED)

    if REPLAY:
        source = ReplaySource(REPLAY_SPEED, tape_path)
        tape_out = tape_path
    else:
        info = MMapControl(LMUConstants.LMU_SHARED_MEMORY_FILE, LMUObjectOut)
        info.create(0)
        source = LiveSource(info)
        # wall-clock is correct HERE: this names a file for a human, it is not
        # telemetry timing. All event timing still comes from mElapsedTime.
        tape_out = f"tape_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl.gz"
        tele_recorder = Recorder(tape_out)
        print(f"Recording to {tape_out}")

    # the corners of this track, for everything that names one. Old tapes carry no track
    # name, so they keep the Monza map they were driven on
    corner_map = CornerMap()
    spin_detector = SpinDetector(corner_map)
    detectors = [
        HardBrakingDetector(corner_map),
        LockUpDetector(corner_map),
        CornerEntryDetection(corner_map),
        ThrottleLift(corner_map),
        OffTrackDetector(corner_map),
        spin_detector,
        SlideCaughtDetector(corner_map),
        RearSnapDetector(corner_map),
        WheelspinDetector(corner_map),
    ]

    lap_counter = LapCounter()
    lap_distance = LapDistance()
    corner_stats = CornerStats(corner_map)

    track = None
    session_type = None
    learning_track = False
    track_learner = TrackMapLearner()

    contacts = ContactDetection(corner_map)
    # the result of the session, read from the race snapshots as they arrive
    grid = None
    final_place = None
    car_settings = None  # (tc, abs, rear bias, motor map) he last ran under green
    first_limit_steps = None
    last_limit_steps = None
    last_opponents = []

    # the seats that watch the whole race (the performance seat rides on the detectors)
    performance = PerformanceEngineer()
    racecraft = Racecraft(performance)
    # the one picture of the race (25 Sep): fed here, before any seat, and only read by them
    model = RaceModel()
    racecraft.share(model)
    recall = MemoryRecall()
    engineer = RaceEngineer()
    strategist = Strategist()
    seats = [
        Spotter(),
        engineer,
        strategist,
        performance,
        racecraft,
        recall,
        TrackAwareness(),
        QualifyingEngineer(),
    ]
    settle = RaceSettle()

    governor = Governor()
    # push-to-talk (M9): only live, and Apex races on without it if it is not set up
    answers = Answers(governor, engineer, strategist, performance, clean)
    # his standing orders: kept for the race, honoured by every seat and the coach (26 Sep)
    orders = StandingOrders()
    governor.orders = orders
    engineer.orders = orders
    answers.model = model  # the fixed answers read the same gaps as every seat
    reminders = []  # {"remind_lap", "what"}: set by the agent, said at the line
    heard_confidence = {}  # question -> Whisper's confidence, until the coach answers it
    talk = None
    agent = None
    if not REPLAY:
        talk = push_to_talk.start_if_set_up()
    elif script:
        talk = ScriptedTalk(source, script)
    budget = Budget(cap_rs=BUDGET_PER_SESSION_RS)
    if talk is not None and not REPLAY:
        agent = RaceAgent(
            budget, clean
        )  # after the budget: it spends from it (24 Sep crash)
        agent.orders = orders
    # one voice for the whole launch when apex.py passes it in: its banks load once
    if voice is None:
        voice = Voice(out_loud)
    if persona is None:
        persona = Persona(clean=clean)
    desk = RadioDesk(
        voice, persona, budget, clean, synchronous=REPLAY and not REPLAY_SPEED
    )

    conn = None
    session_id = None
    saw_running = False
    flag_seen_at = None
    finished_at = None
    end_reason = "tape_end" if REPLAY else "stopped_by_driver"

    def log_finished_lines():
        for result in desk.drain():
            call = result["call"]
            if result.get("llm_only"):
                save_llm_call(conn, session_id, call.seat, result["llm"])
                continue
            save_radio(
                conn,
                session_id,
                call,
                result["status"],
                result["line"],
                result["reason"],
                result["latency_ms"],
            )
            if (
                result["status"] == "spoken"
                and call.seat != "spotter"
                and call.kind != "ANSWER_REPEAT"
            ):
                answers.last_line = result[
                    "line"
                ]  # "say again" repeats the engineer, not "Clear"

    def log_dropped_calls():
        for call, reason in governor.dropped:
            desk.forget(call)
            save_radio(conn, session_id, call, reason)
        governor.dropped = []

    try:
        conn = connect_db()
        session_started = datetime.now().isoformat(timespec="seconds")
        session_id = start_session(
            conn, session_started, tape_out, REPLAY_SPEED, launch_id
        )
        orders.load(
            conn
        )  # what he said "for good" in earlier races ("never tell me the gaps")
        # what team memory knows about every rival, for the racecraft plans
        lap_one_facts = memory_facts(conn, "lap_one")
        if lap_one_facts:
            recall.lap_one = lap_one_facts[0]
        for rival_fact in memory_facts(conn, "rival"):
            racecraft.rivals[rival_fact["subject"]] = rival_fact["summary"]
        # everything team memory knows about him, for the agent's my_habits tool
        team_habits = []
        for kind in ("lap_one", "corner_habit", "pass_attempts", "clean_race", "rival"):
            for fact in memory_facts(conn, kind):
                team_habits.append(fact["summary"])
        voice.play_urgent("RADIO_CHECK", "Radio check. I'm with you.")

        # GUESSED — mLapInvalidated never observed True (n=33485)
        validity = 1  # 1 = valid, 0 = invalidated
        for frame in source:
            own_laps = lap_counter.update(
                frame
            )  # his own line crossings: where laps begin and end
            lap_count = own_laps
            if (
                session_type in RACE_SESSIONS
                and source.race is not None
                and source.race.me is not None
            ):
                green = source.race.session.game_phase == GREEN_FLAG
                lap_count = lap_counter.race_lap(
                    source.race.me.laps, frame.elapsed_time, green
                )
            real_lap_distance = lap_distance.update(frame)

            # the track decides where the corners are, so it must be settled before
            # anything below tags a corner onto a stat or an event
            if track is None and source.race is not None:
                track = source.race.session.track
                session_type = source.race.session.session
                me = source.race.me
                set_session_track(
                    conn,
                    session_id,
                    track,
                    session_type,
                    source.race.session.game_phase,
                    me.car_class if me else None,
                    me.car_model if me else None,
                )
                corner_map.corners = corners_for_track(track)
                if talk is not None and corner_map.corners:
                    talk.set_track_words([c["name"] for c in corner_map.corners])
                # team memory for this track: the habits worth a reminder
                for habit in memory_facts(conn, "corner_habit", track) + memory_facts(
                    conn, "contact_corner", track
                ):
                    recall.corner_habits.setdefault(habit["subject"], habit)
                learning_track = corner_map.corners is None
                if learning_track:
                    print(
                        f"[track: {track} - new track, learning its corners from your laps]"
                    )
                else:
                    print(f"[track: {track} - corner map loaded]")
            if source.new_race and source.race.me is not None:
                me = source.race.me
                if grid is None:
                    grid = me.grid
                if first_limit_steps is None:
                    first_limit_steps = me.track_limit_steps
                last_limit_steps = me.track_limit_steps
                final_place = me.place
                last_opponents = source.race.opponents
                if source.race.session.game_phase == GREEN_FLAG:
                    car_settings = (me.tc, me.abs, me.brake_bias_rear, me.motor_map)
            track_learner.add(own_laps, real_lap_distance, frame.brake, frame.accel_lat)
            if learning_track and lap_counter.wrapped:
                learned = track_learner.corners(own_laps)
                if learned is not None:
                    corner_map.corners = learned

            stat = corner_stats.update(frame, lap_count, real_lap_distance)
            if stat:
                print(stat)
                save_corner_stat(conn, session_id, stat)

            if lap_counter.wrapped:
                save_lap(conn, session_id, own_laps - 1, validity)
                validity = 1
                for reminder in due_reminders(reminders, lap_count, frame.elapsed_time):
                    if governor.offer(reminder):
                        desk.prepare(reminder)

            if frame.lap_invalidated:
                validity = 0

            # the seats raise calls ...
            frame_events = []
            contact = contacts.update(frame, source.near, source.race)
            if contact is not None:
                contact.lap_count = lap_count
                print(contact)
                save_event(conn, session_id, contact)
                frame_events.append(contact)
                if contact.kind == "CONTACT":
                    spin_detector.last_car_contact = contact.sim_time
                if contact.kind in ("CONTACT", "IMPACT"):
                    performance.saw_hit(contact.sim_time)
            for detector in detectors:
                event = detector.update(frame)
                if event:
                    event.lap_count = lap_count
                    print(event)
                    event_id = save_event(conn, session_id, event)
                    frame_events.append(event)
                    call = performance.call_for_event(event, event_id)
                    if call is not None and governor.offer(call):
                        desk.prepare(call)

            corner_now = corner_map.at(real_lap_distance)
            if source.new_race and source.race is not None:
                model.see_race(source.race, frame.elapsed_time)
            model.see_me(frame.lap_dist, frame.elapsed_time)
            moment = Moment(
                frame=frame,
                race=source.race,
                new_race=source.new_race,
                near=source.near,
                lap_count=lap_count,
                lap_wrapped=lap_counter.wrapped,
                corner=corner_now,
                corner_stat=stat,
                session_type=session_type,
                corners=corner_map.corners,
                events=frame_events,
                model=model,
            )
            # first: is the start still chaos? Then nothing but the spotter, flags and answers
            for call in settle.update(moment):
                governor.settled = settle.settled
                if governor.offer(call):
                    desk.prepare(call)
            governor.settled = settle.settled
            for seat in seats:
                for call in seat.update(moment):
                    if governor.offer(call):
                        desk.prepare(call)

            # ... and the radio decides what goes on air, on sim time only
            # quiet while actually braking or cornering hard. The map windows were the rule
            # before, and at Le Mans they are long (Porsche Curves 1.4 km): 14 calls expired
            # waiting for a straight in the first live race (23 Sep 2026)
            in_corner = frame.brake > 0.2 or abs(frame.accel_lat) >= TURNING
            governor.lap = lap_count
            heard_now = []
            if talk is not None:
                try:
                    heard_now = talk.poll()
                except Exception as error:
                    # push-to-talk must never end a race: it switches itself off instead
                    print(
                        f"[push-to-talk off for this session: {error.__class__.__name__}: {error}]"
                    )
                    talk = None
            if talk is not None or heard_now:
                for heard in heard_now:
                    heard.text = fix_mishearing(heard.text)
                    if (
                        garbled(heard.text, getattr(heard, "confidence", None))
                        and intent_of(heard.text) is None
                    ):
                        say_again = Call(
                            seat="race_engineer",
                            kind="ANSWER_UNHEARD",
                            sim_time=frame.elapsed_time,
                            priority=RACE_CONTROL,
                            ttl=10.0,
                            conclusion="Didn't catch that, mate. Say again.",
                            template="Didn't catch that, mate. Say again.",
                            asked=True,
                            facts={"heard": heard.text, "confidence": heard.confidence},
                        )
                        print(
                            f"[garbled ({heard.confidence}): {heard.text!r} -> say again]"
                        )
                        if governor.offer(say_again):
                            desk.prepare(say_again)
                        continue
                    heard_order = (
                        None
                        if is_mark(heard.text)
                        else orders.hear(heard.text, lap_count, frame.elapsed_time)
                    )
                    if heard_order is not None:
                        # an order: kept for the race and said back, no model needed
                        words, given = heard_order
                        ack = Call(
                            seat="race_engineer",
                            kind="ANSWER_ORDER",
                            sim_time=frame.elapsed_time,
                            priority=RACE_CONTROL,
                            ttl=10.0,
                            conclusion=words,
                            template=words,
                            asked=True,
                            facts={
                                "heard": heard.text,
                                "orders": [f"{o.topic}={o.stance}" for o in given],
                                "confidence": getattr(heard, "confidence", None),
                            },
                        )
                        orders.save(conn)
                        print(
                            f"[order: {heard.text!r} -> {ack.facts['orders'] or 'back to normal'}]"
                        )
                        if governor.offer(ack):
                            desk.prepare(ack)
                        continue
                    if (
                        agent is not None
                        and needs_agent(heard.text)
                        and source.race is not None
                        and source.race.me is not None
                    ):
                        # a real question: the agent looks at a still picture of the race
                        snapshot = Snapshot(
                            source.race,
                            lap_count,
                            real_lap_distance,
                            corner_map.corners,
                            engineer,
                            strategist,
                            performance,
                            racecraft,
                            governor,
                            team_habits,
                            contacts_by_car(conn, session_id),
                            db_path=database_file(conn),
                            session_id=session_id,
                            model=model,
                        )
                        agent.ask(heard.text, snapshot, frame.elapsed_time)
                        # logged with the answer: the "say again" threshold is tuned from these
                        heard_confidence[heard.text] = getattr(
                            heard, "confidence", None
                        )
                        voice.play_bank_if_free("STAND_BY", "Copy. Stand by.")
                        print(f"[asked the agent: {heard.text!r}]")
                        continue
                    answer = answers.answer(
                        heard.text, source.race, lap_count, frame.elapsed_time
                    )
                    answer.facts["transcribe_ms"] = heard.transcribe_ms
                    answer.facts["confidence"] = getattr(heard, "confidence", None)
                    print(
                        f"[asked: {heard.text!r} -> {answer.kind}: {answer.template}]"
                    )
                    if governor.offer(answer):
                        desk.prepare(answer)
            if agent is not None:
                for result in agent.finished():
                    for spent in result["costs"]:
                        save_llm_call(conn, session_id, "agent", spent)
                    heard_text = result["call"].facts.get("heard", "")
                    result["call"].facts["confidence"] = heard_confidence.pop(
                        heard_text, None
                    )
                    gave_up = result["call"].template.startswith(
                        ("No clean answer", "Radio's lagging, mate. Ask me again")
                    )
                    if (
                        gave_up
                        and intent_of(heard_text) is not None
                        and source.race is not None
                    ):
                        # live 24 Sep: "what times do I need to catch the car ahead?" got "no clean
                        # answer" while the quick lane had the lap time. It speaks instead.
                        quick = answers.answer(
                            heard_text, source.race, lap_count, frame.elapsed_time
                        )
                        quick.facts["agent_gave_up"] = result["call"].template
                        result["call"] = quick
                    for action in result.get("actions", []):
                        reminders.append(action)  # "remind me to box on lap 12"
                    for topic, stance in result.get("orders", []):
                        try:
                            orders.set(
                                topic,
                                stance,
                                heard_text,
                                lap_count,
                                frame.elapsed_time,
                                "coach",
                            )
                            print(f"[order from the coach: {topic}={stance}]")
                        except ValueError:
                            print(
                                f"[coach gave an order that does not exist: {topic}={stance}]"
                            )
                    if result.get("orders"):
                        orders.save(conn)
                    # .get: when the coach gave up, the quick answer stands in and has no timing
                    # (live 25 Sep: KeyError 'seconds' ended the session)
                    print(
                        f"[agent: {result['call'].template}  ({result['call'].facts.get('seconds', '?')} s)]"
                    )
                    if governor.offer(result["call"]):
                        desk.prepare(result["call"])
            on_air = governor.step(frame.elapsed_time, in_corner)
            if on_air is not None:
                if on_air.urgent:
                    played = voice.play_urgent(on_air.kind, on_air.template)
                    save_radio(
                        conn,
                        session_id,
                        on_air,
                        "spoken" if played else "no_bank_line",
                        on_air.template,
                        latency_ms=0,
                    )
                elif not desk.submit(on_air):
                    save_radio(conn, session_id, on_air, "queue_full")
            desk.latest_sim_time = frame.elapsed_time
            log_dropped_calls()
            log_finished_lines()

            if not REPLAY:
                # race lines go first: on replay each one belongs to the car frame after it
                if source.new_race:
                    tele_recorder.record(source.race)
                if source.near is not None:
                    tele_recorder.record(source.near)
                tele_recorder.record(frame)

            # the session ends itself: no Ctrl+C needed at the chequered flag. Only a session
            # Apex saw running: started on a results screen, it would end, restart and end again
            if source.race is not None:
                if source.race.session.game_phase < SESSION_OVER:
                    saw_running = True
                if source.race.session.game_phase == SESSION_OVER and saw_running:
                    if flag_seen_at is None:
                        flag_seen_at = frame.elapsed_time
                    me = source.race.me
                    my_race_done = me is None or me.finish_status != 0 or me.in_pits
                    if my_race_done and finished_at is None:
                        finished_at = frame.elapsed_time
                    grace_over = (
                        finished_at is not None
                        and frame.elapsed_time - finished_at >= FINISHED_GRACE_S
                    )
                    if (
                        grace_over
                        or frame.elapsed_time - flag_seen_at >= FLAG_TIMEOUT_S
                    ):
                        end_reason = "session_over"
                        break
                if (
                    session_type is not None
                    and source.race.session.session != session_type
                ):
                    end_reason = "session_changed"
                    break

    except KeyboardInterrupt:
        end_reason = "stopped_by_driver"

    finally:
        if talk is not None:
            talk.close()
        desk.stop()
        if not REPLAY:
            tele_recorder.stop()
        if learning_track and track:
            learned = track_learner.corners(lap_counter.lap_count)
            if learned is not None:
                laps_used = len(track_learner.complete_laps(lap_counter.lap_count))
                save_map(track, learned, laps_used)
                print(
                    f"[saved the corner map for {track}, learned from {laps_used} laps]"
                )
        decision_hash = governor.decision_hash()
        if conn is not None and session_id:
            log_dropped_calls()
            log_finished_lines()
            ended_at = datetime.now().isoformat(timespec="seconds")
            finish_session(conn, session_id, decision_hash, end_reason, ended_at)
            if final_place is not None:
                strikes = None
                if first_limit_steps is not None:
                    strikes = last_limit_steps - first_limit_steps
                save_session_result(conn, session_id, grid, final_place, strikes)
                save_rivals(conn, session_id, last_opponents)
            if car_settings is not None:
                save_car_settings(conn, session_id, *car_settings)
            save_opponent_corners(conn, session_id, performance.opponents.rows)
            save_pass_attempts(conn, session_id, racecraft.attempts)
            print_corner_report(conn, session_id)
            print(
                f"session {session_id} ended: {end_reason}   LLM spend ~Rs {budget.spent_rs:.2f}"
            )
        if conn is not None:
            conn.close()
        print(decision_hash)
    return session_id


if __name__ == "__main__":
    run_session(True, 1)
