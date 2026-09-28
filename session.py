"""One session, start to finish: practice, qualifying or a race, live or from a tape.

run_session reads his car frame by frame, feeds the detectors, the race model and the seats,
lets the governor decide what goes on air, answers him when he speaks, and writes everything to
the database. When the session ends (his own flag, the session changing, or Ctrl+C) it saves the
result, and the corner map it learned if the track was new.

Every frame goes through the same steps, in this order (Session.run):
    count his laps -> settle the track (first race snapshot only) -> note the result so far
    -> learn the corners (new tracks) -> corner stats -> at the line: lap, reminders
    -> detect events -> feed the race model and build the Moment -> the seats raise calls
    -> hear him -> take the coach's answers -> the governor puts one call on air
    -> write the tape (live) -> has the session ended?"""

from sharedmemory import MMapControl
from lmu_data import LMUObjectOut, LMUConstants
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
from radio.governor import Governor
from radio.calls import Call, RACE_CONTROL
from coach.llm import Budget
from seats.settle import RaceSettle
from seats.track_awareness import TrackAwareness
from seats.qualifying import QualifyingEngineer
from orders import StandingOrders
from persona import Persona
from radio.voice import Voice
from radio.desk import RadioDesk
from seats.performance import PerformanceEngineer
from seats import Moment
from seats.spotter import Spotter
from seats.race_engineer import RaceEngineer
from seats.strategist import Strategist
from seats.racecraft import Racecraft
from seats.memory_recall import MemoryRecall
from talk.answers import Answers
from talk.hearing import needs_agent, intent_of, fix_mishearing, garbled, is_mark
from coach.agent import RaceAgent
from coach.snapshot import Snapshot
from race_model import RaceModel
import ptt as push_to_talk
from team_memory import facts as memory_facts
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
from game.live_source import LiveSource
from game.tape import Recorder, ReplaySource, TAPE_PATH
from talk.scripted_talk import ScriptedTalk


# Gourav's cap: Rs 5 (23 Sep 2026), raised to Rs 10 (25 Sep, "increase the budget a bit").
# Past it, template lines only. Push-to-talk is NOT capped (his call, 25 Sep: "I don't want it
# to stop"): its spend is still charged here and logged, it just never refuses a question.
BUDGET_PER_SESSION_RS = 10.0

# SESSION_OVER (phase 8) comes when the LEADER takes the flag. On 23 Sep Apex stopped right
# there, with Gourav still 400 m from his own finish line: every non-leader lost the end of the
# race. So Apex waits for my own car to finish, then leaves the engineer time to call the result.
FINISHED_GRACE_S = 10.0
# a car that never takes the flag (parked, crashed out): stop anyway
FLAG_TIMEOUT_S = 420.0

# what team memory knows about him, for the coach's my_habits tool
HABIT_KINDS = ("lap_one", "corner_habit", "pass_attempts", "clean_race", "rival")
# a coach answer that is no answer: the quick lane speaks instead, if it knows the question
COACH_GAVE_UP = ("No clean answer", "Radio's lagging, mate. Ask me again")


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
    session = Session(
        replay,
        replay_speed,
        tape_path,
        out_loud,
        clean,
        persona,
        launch_id,
        voice,
        script,
    )
    return session.run()


class SessionResult:
    """How the session went, read from the race snapshots as they arrive; saved at the end."""

    def __init__(self):
        self.grid = None
        self.final_place = None
        self.car_settings = (
            None  # (tc, abs, rear bias, motor map) he last ran under green
        )
        self.first_limit_steps = None
        self.last_limit_steps = None
        self.last_opponents = []

    def see(self, race):
        me = race.me
        if self.grid is None:
            self.grid = me.grid
        if self.first_limit_steps is None:
            self.first_limit_steps = me.track_limit_steps
        self.last_limit_steps = me.track_limit_steps
        self.final_place = me.place
        self.last_opponents = race.opponents
        if race.session.game_phase == GREEN_FLAG:
            self.car_settings = (me.tc, me.abs, me.brake_bias_rear, me.motor_map)

    def save(self, conn, session_id):
        if self.final_place is not None:
            strikes = None
            if self.first_limit_steps is not None:
                strikes = self.last_limit_steps - self.first_limit_steps
            save_session_result(conn, session_id, self.grid, self.final_place, strikes)
            save_rivals(conn, session_id, self.last_opponents)
        if self.car_settings is not None:
            save_car_settings(conn, session_id, *self.car_settings)


class SessionEnd:
    """The session ends itself: no Ctrl+C needed at the chequered flag. Only a session Apex saw
    running: started on a results screen, it would end, restart and end again."""

    def __init__(self):
        self.saw_running = False
        self.flag_seen_at = None
        self.finished_at = None

    def reason(self, race, now, session_type):
        """ "session_over", "session_changed", or None while it goes on."""
        if race is None:
            return None
        if race.session.game_phase < SESSION_OVER:
            self.saw_running = True
        flag_out = race.session.game_phase == SESSION_OVER and self.saw_running
        if flag_out and self.flag_is_done(race.me, now):
            return "session_over"
        if session_type is not None and race.session.session != session_type:
            return "session_changed"
        return None

    def flag_is_done(self, me, now):
        """The flag is out: done once his own race is over and the grace has passed, or at
        the timeout."""
        if self.flag_seen_at is None:
            self.flag_seen_at = now
        my_race_done = me is None or me.finish_status != 0 or me.in_pits
        if my_race_done and self.finished_at is None:
            self.finished_at = now
        grace_over = (
            self.finished_at is not None and now - self.finished_at >= FINISHED_GRACE_S
        )
        return grace_over or now - self.flag_seen_at >= FLAG_TIMEOUT_S


class Session:
    """Everything one session keeps from frame to frame, and one method per step of a frame."""

    def __init__(  # the options of run_session, passed straight on
        self,
        replay,
        replay_speed,
        tape_path,
        out_loud,
        clean,
        persona,
        launch_id,
        voice,
        script,
    ):
        self.replay = replay
        self.replay_speed = replay_speed
        self.launch_id = launch_id
        if out_loud is None:
            # a replay at max speed prints its lines, like v1 did
            out_loud = not (replay and not replay_speed)
        self.open_source(tape_path)
        self.build_driving()
        self.build_team(clean)
        self.build_radio(clean, out_loud, voice, persona, script)
        self.conn = None
        self.session_id = None
        self.result = SessionResult()
        self.end = SessionEnd()
        self.end_reason = "tape_end" if replay else "stopped_by_driver"

    # ---- setting up -------------------------------------------------------------------------
    def open_source(self, tape_path):
        """Where the frames come from: a tape, or the game (and then a tape of it is written)."""
        self.recorder = None
        if self.replay:
            self.source = ReplaySource(self.replay_speed, tape_path)
            self.tape_out = tape_path
            return
        info = MMapControl(LMUConstants.LMU_SHARED_MEMORY_FILE, LMUObjectOut)
        info.create(0)
        self.source = LiveSource(info)
        # wall-clock is correct HERE: this names a file for a human, it is not
        # telemetry timing. All event timing still comes from mElapsedTime.
        self.tape_out = f"tape_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl.gz"
        self.recorder = Recorder(self.tape_out)
        print(f"Recording to {self.tape_out}")

    def build_driving(self):
        """What watches his own car: laps, corners, detectors."""
        # the corners of this track, for everything that names one. Old tapes carry no track
        # name, so they keep the Monza map they were driven on
        corner_map = CornerMap()
        self.corner_map = corner_map
        self.spin_detector = SpinDetector(corner_map)
        self.detectors = [
            HardBrakingDetector(corner_map),
            LockUpDetector(corner_map),
            CornerEntryDetection(corner_map),
            ThrottleLift(corner_map),
            OffTrackDetector(corner_map),
            self.spin_detector,
            SlideCaughtDetector(corner_map),
            RearSnapDetector(corner_map),
            WheelspinDetector(corner_map),
        ]
        self.lap_counter = LapCounter()
        self.lap_distance = LapDistance()
        self.corner_stats = CornerStats(corner_map)
        self.track = None
        self.session_type = None
        self.learning_track = False
        self.track_learner = TrackMapLearner()
        self.contacts = ContactDetection(corner_map)
        self.validity = 1  # 1 = valid, 0 = invalidated

    def build_team(self, clean):
        """The seats, the race model they all read, and the governor that picks what is said."""
        # the seats that watch the whole race (the performance seat rides on the detectors)
        self.performance = PerformanceEngineer()
        self.racecraft = Racecraft(self.performance)
        # the one picture of the race (25 Sep): fed here, before any seat, and only read by them
        self.model = RaceModel()
        self.racecraft.share(self.model)
        self.recall = MemoryRecall()
        self.engineer = RaceEngineer()
        self.strategist = Strategist()
        self.seats = [
            Spotter(),
            self.engineer,
            self.strategist,
            self.performance,
            self.racecraft,
            self.recall,
            TrackAwareness(),
            QualifyingEngineer(),
        ]
        self.settle = RaceSettle()
        self.governor = Governor()
        # push-to-talk (M9): only live, and Apex races on without it if it is not set up
        self.answers = Answers(
            self.governor, self.engineer, self.strategist, self.performance, clean
        )
        # his standing orders: kept for the race, honoured by every seat and the coach (26 Sep)
        self.orders = StandingOrders()
        self.governor.orders = self.orders
        self.engineer.orders = self.orders
        self.answers.model = (
            self.model
        )  # the fixed answers read the same gaps as every seat
        self.reminders = []  # {"remind_lap", "what"}: set by the agent, said at the line
        self.heard_confidence = {}  # question -> Whisper's confidence, until the coach answers

    def build_radio(self, clean, out_loud, voice, persona, script):
        """His push-to-talk (or a script), the coach, the voice and the desk that cooks lines."""
        self.talk = None
        self.agent = None
        if not self.replay:
            self.talk = push_to_talk.start_if_set_up()
        elif script:
            self.talk = ScriptedTalk(self.source, script)
        self.budget = Budget(cap_rs=BUDGET_PER_SESSION_RS)
        if self.talk is not None and not self.replay:
            # after the budget: it spends from it (24 Sep crash)
            self.agent = RaceAgent(self.budget, clean)
            self.agent.orders = self.orders
        # one voice for the whole launch when apex.py passes it in: its banks load once
        if voice is None:
            voice = Voice(out_loud)
        self.voice = voice
        if persona is None:
            persona = Persona(clean=clean)
        self.desk = RadioDesk(
            voice,
            persona,
            self.budget,
            clean,
            synchronous=self.replay and not self.replay_speed,
        )

    def start(self):
        """The session's row in the database, and what earlier races taught."""
        self.conn = connect_db()
        session_started = datetime.now().isoformat(timespec="seconds")
        self.session_id = start_session(
            self.conn, session_started, self.tape_out, self.replay_speed, self.launch_id
        )
        # what he said "for good" in earlier races ("never tell me the gaps")
        self.orders.load(self.conn)
        # what team memory knows about every rival, for the racecraft plans
        lap_one_facts = memory_facts(self.conn, "lap_one")
        if lap_one_facts:
            self.recall.lap_one = lap_one_facts[0]
        for rival_fact in memory_facts(self.conn, "rival"):
            self.racecraft.rivals[rival_fact["subject"]] = rival_fact["summary"]
        # everything team memory knows about him, for the agent's my_habits tool
        self.team_habits = []
        for kind in HABIT_KINDS:
            for fact in memory_facts(self.conn, kind):
                self.team_habits.append(fact["summary"])
        self.voice.play_urgent("RADIO_CHECK", "Radio check. I'm with you.")

    # ---- the session ------------------------------------------------------------------------
    def run(self):
        """Every frame through every step, until the session ends. Returns the session's id."""
        try:
            self.start()
            for frame in self.source:
                if self.one_frame(frame):
                    break
        except KeyboardInterrupt:
            self.end_reason = "stopped_by_driver"
        finally:
            self.finish()
        return self.session_id

    def one_frame(self, frame):
        """The steps of one frame, in order. True when the session has ended."""
        self.count_laps(frame)
        race = self.source.race
        # the track decides where the corners are, so it must be settled before
        # anything below tags a corner onto a stat or an event
        if self.track is None and race is not None:
            self.settle_track(race)
        if self.source.new_race and race.me is not None:
            self.result.see(race)
        self.learn_corners(frame)
        stat = self.corner_stat(frame)
        self.at_the_line(frame)
        # the seats raise calls ...
        events = self.detect(frame)
        moment = self.moment_of(frame, stat, events)
        self.seats_speak(moment)
        # ... and the radio decides what goes on air, on sim time only
        # quiet while actually braking or cornering hard. The map windows were the rule
        # before, and at Le Mans they are long (Porsche Curves 1.4 km): 14 calls expired
        # waiting for a straight in the first live race (23 Sep 2026)
        in_corner = frame.brake > 0.2 or abs(frame.accel_lat) >= TURNING
        self.governor.lap = self.lap_count
        self.hear_him(frame)
        if self.agent is not None:
            for result in self.agent.finished():
                self.take_coach_answer(result, frame)
        self.put_on_air(frame, in_corner)
        self.record(frame)
        reason = self.end.reason(race, frame.elapsed_time, self.session_type)
        if reason is not None:
            self.end_reason = reason
            return True
        return False

    def offer(self, call):
        """A call for the governor; if it takes it, the desk starts cooking it at once."""
        if self.governor.offer(call):
            self.desk.prepare(call)

    # ---- his car ----------------------------------------------------------------------------
    def count_laps(self, frame):
        # his own line crossings: where laps begin and end
        self.own_laps = self.lap_counter.update(frame)
        self.lap_count = self.own_laps
        race = self.source.race
        if (
            self.session_type in RACE_SESSIONS
            and race is not None
            and race.me is not None
        ):
            green = race.session.game_phase == GREEN_FLAG
            self.lap_count = self.lap_counter.race_lap(
                race.me.laps, frame.elapsed_time, green
            )
        self.real_lap_distance = self.lap_distance.update(frame)

    def settle_track(self, race):
        """The first race snapshot: the track and session go in the database, the corner map is
        loaded (or learning starts), and push-to-talk and team memory learn the corner names."""
        self.track = race.session.track
        self.session_type = race.session.session
        me = race.me
        set_session_track(
            self.conn,
            self.session_id,
            self.track,
            self.session_type,
            race.session.game_phase,
            me.car_class if me else None,
            me.car_model if me else None,
        )
        self.corner_map.corners = corners_for_track(self.track)
        if self.talk is not None and self.corner_map.corners:
            self.talk.set_track_words([c["name"] for c in self.corner_map.corners])
        # team memory for this track: the habits worth a reminder
        habits = memory_facts(self.conn, "corner_habit", self.track)
        habits += memory_facts(self.conn, "contact_corner", self.track)
        for habit in habits:
            self.recall.corner_habits.setdefault(habit["subject"], habit)
        self.learning_track = self.corner_map.corners is None
        if self.learning_track:
            print(
                f"[track: {self.track} - new track, learning its corners from your laps]"
            )
        else:
            print(f"[track: {self.track} - corner map loaded]")

    def learn_corners(self, frame):
        self.track_learner.add(
            self.own_laps, self.real_lap_distance, frame.brake, frame.accel_lat
        )
        if self.learning_track and self.lap_counter.wrapped:
            learned = self.track_learner.corners(self.own_laps)
            if learned is not None:
                self.corner_map.corners = learned

    def corner_stat(self, frame):
        stat = self.corner_stats.update(frame, self.lap_count, self.real_lap_distance)
        if stat:
            print(stat)
            save_corner_stat(self.conn, self.session_id, stat)
        return stat

    def at_the_line(self, frame):
        """A lap done: save it, say the reminders due on this lap. And note an invalid lap."""
        if self.lap_counter.wrapped:
            save_lap(self.conn, self.session_id, self.own_laps - 1, self.validity)
            self.validity = 1
            for reminder in due_reminders(
                self.reminders, self.lap_count, frame.elapsed_time
            ):
                self.offer(reminder)
        # GUESSED - mLapInvalidated never observed True (n=33485)
        if frame.lap_invalidated:
            self.validity = 0

    def detect(self, frame):
        """This frame's events (contacts, offs, spins...): saved, and the performance seat's
        calls on them offered."""
        events = []
        contact = self.contacts.update(frame, self.source.near, self.source.race)
        if contact is not None:
            contact.lap_count = self.lap_count
            print(contact)
            save_event(self.conn, self.session_id, contact)
            events.append(contact)
            if contact.kind == "CONTACT":
                self.spin_detector.last_car_contact = contact.sim_time
            if contact.kind in ("CONTACT", "IMPACT"):
                self.performance.saw_hit(contact.sim_time)
        for detector in self.detectors:
            event = detector.update(frame)
            if event:
                event.lap_count = self.lap_count
                print(event)
                event_id = save_event(self.conn, self.session_id, event)
                events.append(event)
                call = self.performance.call_for_event(event, event_id)
                if call is not None:
                    self.offer(call)
        return events

    # ---- the race and the seats -------------------------------------------------------------
    def moment_of(self, frame, stat, events):
        """The race model fed first, then the Moment every seat looks at."""
        corner_now = self.corner_map.at(self.real_lap_distance)
        if self.source.new_race and self.source.race is not None:
            self.model.see_race(self.source.race, frame.elapsed_time)
        self.model.see_me(frame.lap_dist, frame.elapsed_time)
        return Moment(
            frame=frame,
            race=self.source.race,
            new_race=self.source.new_race,
            near=self.source.near,
            lap_count=self.lap_count,
            lap_wrapped=self.lap_counter.wrapped,
            corner=corner_now,
            corner_stat=stat,
            session_type=self.session_type,
            corners=self.corner_map.corners,
            events=events,
            model=self.model,
        )

    def seats_speak(self, moment):
        # first: is the start still chaos? Then nothing but the spotter, flags and answers
        for call in self.settle.update(moment):
            self.governor.settled = self.settle.settled
            self.offer(call)
        self.governor.settled = self.settle.settled
        for seat in self.seats:
            for call in seat.update(moment):
                self.offer(call)

    # ---- him --------------------------------------------------------------------------------
    def hear_him(self, frame):
        """What he said since the last frame, each answered in turn."""
        if self.talk is None:
            return
        try:
            heard_now = self.talk.poll()
        except Exception as error:
            # push-to-talk must never end a race: it switches itself off instead
            print(
                f"[push-to-talk off for this session: {error.__class__.__name__}: {error}]"
            )
            self.talk = None
            return
        for heard in heard_now:
            self.answer(heard, frame)

    def answer(self, heard, frame):
        """One thing he said: "say again" if garbled, else his order, a question for the
        coach, or a question the code answers."""
        heard.text = fix_mishearing(heard.text)
        if (
            garbled(heard.text, getattr(heard, "confidence", None))
            and intent_of(heard.text) is None
        ):
            self.say_again(heard, frame)
            return
        heard_order = None
        if not is_mark(heard.text):
            heard_order = self.orders.hear(
                heard.text, self.lap_count, frame.elapsed_time
            )
        if heard_order is not None:
            self.take_order(heard, heard_order, frame)
            return
        race = self.source.race
        if (
            self.agent is not None
            and needs_agent(heard.text)
            and race is not None
            and race.me is not None
        ):
            self.ask_the_coach(heard, frame)
            return
        answer = self.answers.answer(
            heard.text, race, self.lap_count, frame.elapsed_time
        )
        answer.facts["transcribe_ms"] = heard.transcribe_ms
        answer.facts["confidence"] = getattr(heard, "confidence", None)
        print(f"[asked: {heard.text!r} -> {answer.kind}: {answer.template}]")
        self.offer(answer)

    def say_again(self, heard, frame):
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
        print(f"[garbled ({heard.confidence}): {heard.text!r} -> say again]")
        self.offer(say_again)

    def take_order(self, heard, heard_order, frame):
        """An order: kept for the race and said back, no model needed."""
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
        self.orders.save(self.conn)
        print(f"[order: {heard.text!r} -> {ack.facts['orders'] or 'back to normal'}]")
        self.offer(ack)

    def ask_the_coach(self, heard, frame):
        """A real question: the agent looks at a still picture of the race."""
        snapshot = Snapshot(
            self.source.race,
            self.lap_count,
            self.real_lap_distance,
            self.corner_map.corners,
            self.engineer,
            self.strategist,
            self.performance,
            self.racecraft,
            self.governor,
            self.team_habits,
            contacts_by_car(self.conn, self.session_id),
            db_path=database_file(self.conn),
            session_id=self.session_id,
            model=self.model,
        )
        self.agent.ask(heard.text, snapshot, frame.elapsed_time)
        # logged with the answer: the "say again" threshold is tuned from these
        self.heard_confidence[heard.text] = getattr(heard, "confidence", None)
        self.voice.play_bank_if_free("STAND_BY", "Copy. Stand by.")
        print(f"[asked the agent: {heard.text!r}]")

    def take_coach_answer(self, result, frame):
        """One answer back from the coach: its cost logged, its reminders and orders kept, and
        the quick lane standing in when the coach gave up on a question it knows."""
        now = frame.elapsed_time
        for spent in result["costs"]:
            save_llm_call(self.conn, self.session_id, "agent", spent)
        heard_text = result["call"].facts.get("heard", "")
        result["call"].facts["confidence"] = self.heard_confidence.pop(heard_text, None)
        gave_up = result["call"].template.startswith(COACH_GAVE_UP)
        race = self.source.race
        if gave_up and intent_of(heard_text) is not None and race is not None:
            # live 24 Sep: "what times do I need to catch the car ahead?" got "no clean
            # answer" while the quick lane had the lap time. It speaks instead.
            quick = self.answers.answer(heard_text, race, self.lap_count, now)
            quick.facts["agent_gave_up"] = result["call"].template
            result["call"] = quick
        for action in result.get("actions", []):
            self.reminders.append(action)  # "remind me to box on lap 12"
        for topic, stance in result.get("orders", []):
            try:
                self.orders.set(topic, stance, heard_text, self.lap_count, now, "coach")
                print(f"[order from the coach: {topic}={stance}]")
            except ValueError:
                print(f"[coach gave an order that does not exist: {topic}={stance}]")
        if result.get("orders"):
            self.orders.save(self.conn)
        # .get: when the coach gave up, the quick answer stands in and has no timing
        # (live 25 Sep: KeyError 'seconds' ended the session)
        call = result["call"]
        print(f"[agent: {call.template}  ({call.facts.get('seconds', '?')} s)]")
        self.offer(call)

    # ---- on air -----------------------------------------------------------------------------
    def put_on_air(self, frame, in_corner):
        on_air = self.governor.step(frame.elapsed_time, in_corner)
        if on_air is not None:
            if on_air.urgent:
                played = self.voice.play_urgent(on_air.kind, on_air.template)
                save_radio(
                    self.conn,
                    self.session_id,
                    on_air,
                    "spoken" if played else "no_bank_line",
                    on_air.template,
                    latency_ms=0,
                )
            elif not self.desk.submit(on_air):
                save_radio(self.conn, self.session_id, on_air, "queue_full")
        self.desk.latest_sim_time = frame.elapsed_time
        self.log_dropped_calls()
        self.log_finished_lines()

    def log_finished_lines(self):
        for result in self.desk.drain():
            call = result["call"]
            if result.get("llm_only"):
                save_llm_call(self.conn, self.session_id, call.seat, result["llm"])
                continue
            save_radio(
                self.conn,
                self.session_id,
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
                # "say again" repeats the engineer, not "Clear"
                self.answers.last_line = result["line"]

    def log_dropped_calls(self):
        for call, reason in self.governor.dropped:
            self.desk.forget(call)
            save_radio(self.conn, self.session_id, call, reason)
        self.governor.dropped = []

    def record(self, frame):
        """Live only: race lines go first, because on replay each one belongs to the car
        frame after it."""
        if self.recorder is None:
            return
        if self.source.new_race:
            self.recorder.record(self.source.race)
        if self.source.near is not None:
            self.recorder.record(self.source.near)
        self.recorder.record(frame)

    # ---- the end ----------------------------------------------------------------------------
    def finish(self):
        """Everything closed, the learned corner map and the session saved, whatever ended it."""
        if self.talk is not None:
            self.talk.close()
        self.desk.stop()
        if self.recorder is not None:
            self.recorder.stop()
        if self.learning_track and self.track:
            learned = self.track_learner.corners(self.lap_counter.lap_count)
            if learned is not None:
                laps_used = len(
                    self.track_learner.complete_laps(self.lap_counter.lap_count)
                )
                save_map(self.track, learned, laps_used)
                print(
                    f"[saved the corner map for {self.track}, learned from {laps_used} laps]"
                )
        decision_hash = self.governor.decision_hash()
        if self.conn is not None and self.session_id:
            self.save_session(decision_hash)
        if self.conn is not None:
            self.conn.close()
        print(decision_hash)

    def save_session(self, decision_hash):
        self.log_dropped_calls()
        self.log_finished_lines()
        ended_at = datetime.now().isoformat(timespec="seconds")
        finish_session(
            self.conn, self.session_id, decision_hash, self.end_reason, ended_at
        )
        self.result.save(self.conn, self.session_id)
        save_opponent_corners(
            self.conn, self.session_id, self.performance.opponents.rows
        )
        save_pass_attempts(self.conn, self.session_id, self.racecraft.attempts)
        print_corner_report(self.conn, self.session_id)
        print(
            f"session {self.session_id} ended: {self.end_reason}   LLM spend ~Rs {self.budget.spent_rs:.2f}"
        )
