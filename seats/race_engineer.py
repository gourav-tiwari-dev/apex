"""Race engineer: the main voice. The start, flags, penalties, track limits, damage, and
where you are in the race every few laps.

Everything comes from the race snapshots. Urgent things (lights out, safety car, blue flag,
yellow) come from the voice bank; the rest is phrased by the persona on the next straight.
"""

import math

from radio.calls import Call, RACE_CONTROL, ENGINEER
from race_state import (
    identity,
    same_class_neighbours,
    laps_to_go,
    said_place,
    class_place,
)
from game.constants import (
    BLUE_FLAG,
    FORMATION_LAP,
    GREEN_FLAG,
    RACE_SESSIONS,
    SAFETY_CAR,
    SECTOR_YELLOW,
    SESSION_OVER,
)
from words import lap_time_parts, tenths_words
from race_model import CATCH_UPPER

# mSectorFlag: 1 is a local yellow, nothing else is (game.constants.SECTOR_YELLOW, measured on all 5
# race tapes 26 Sep). The 23 Sep note "1 (and sometimes 3)" was wrong about 3: it shows before
# the race and when the clock runs out, and said "Yellow flag" at the end of the 25 Sep night
# race. Yellows last 10-20 s. Only the yellow in my sector or the next matters.
# Below this he is the slow car or crawling past it: no yellow for the incident he is in (25 Sep
# night: punted at the Porsche Curves, the game's yellow came 3.4 s before the spin detector saw
# it) and no blue flags (25 Sep, 58-car race: "Blue flag. Let him by" 3 times in 6 s at 5-9 km/h)
CRAWLING_KMH = 60.0
# After a crash (his mark, 25 Sep: "it doesn't know that I crashed and spun, my race is over"):
# 214 -> 6 km/h at Indianapolis, stopped a minute, and nobody asked. A pit wall's first question
# after a crash is "Are you OK?" (27 Sep, RACE_MODEL.md)
INCIDENT_KINDS = ("SPIN", "CONTACT", "IMPACT")
# Track limits: a step is said once it matters for a penalty. 24 Sep: "Track limits at X. Keep it
# inside." at steps 3, 5, 6 and 7 of 20, never near one; Crew Chief users' commonest complaint is
# off-track warnings every time "since they already know" (27 Sep, RACE_MODEL.md)
LIMITS_SAY_FROM_SHARE = 0.5  # when he crosses half way to a penalty
LIMITS_EVERY_STEP_LAST = 3  # and every step in the last three before it
STOPPED_KMH = 20.0
STOPPED_FOR_S = 3.0
AFTER_INCIDENT_S = 30.0  # a stop this soon after a spin or a hit is that incident
# GUESSED: the flag list uses the game's own sector numbering (index 0 = sector 3), like mSector.
NEXT_SECTOR = {1: 2, 2: 0, 0: 1}
# The game numbers MY sector 1, 2, 0 (0 = sector 3); the sector flags are a list in track order
# (slot 0 = sector 1). Comparing the two directly was off by one: live 25 Sep, all 4 yellows
# called were in a sector he was neither in nor heading into ("wrong yellow flag calls", marked
# twice). Checked on the tape: with this mapping none of the 4 fire.
FLAG_SLOT = {1: 0, 2: 1, 0: 2}
REPORT_EVERY_LAPS = 3
REPORT_TTL_S = 20.0  # a gap report can wait for a straight, but not for ever

# the race picture at the line (24 Sep: "I got no data of other cars, like the car behind is
# closing in, or I'm closing on the next car, keep going" and "the lap times I need to catch
# the next guy", "at this pace the next battle is on lap 5 or 6")
WATCH_GAP_S = 8.0  # further than this, a car is not a target yet
FIGHT_GAP_S = 0.8  # closer than this, the spotter and racecraft have it
TREND_S_PER_LAP = 0.1  # a tenth a lap is a trend; less is noise
# needing more than this share of a lap, every lap, is not a target: 0.66 s at Monza, 1.4 s at
# Le Mans (a fixed 1 s hid the call at Le Mans, where 1.1 s is 0.5 % of a lap)
MAX_GAIN_NEEDED_SHARE = 0.006
PACE_TARGET_EVERY_LAPS = 2
GROWING_EVERY_LAPS = 3


def urgent(kind, text, now, priority=RACE_CONTROL):
    return Call(
        seat="race_engineer",
        kind=kind,
        sim_time=now,
        priority=priority,
        ttl=3.0,
        conclusion=text,
        template=text,
        urgent=True,
    )


def spoken(
    kind,
    conclusion,
    now,
    facts,
    template=None,
    priority=ENGINEER,
    ttl=REPORT_TTL_S,
    evidence=None,
):
    return Call(
        seat="race_engineer",
        kind=kind,
        sim_time=now,
        priority=priority,
        ttl=ttl,
        conclusion=conclusion,
        facts=facts,
        template=template,
        evidence=evidence or {},
    )


OWN_SPIN_YELLOW_S = 20.0  # the yellow he causes himself is not news to him
YELLOW_AGAIN_S = 30.0
PITS_AHEAD_PLACES = 2  # a car this many places ahead pitting is worth a call
HELD_UP_COST_S = 0.7  # stuck behind a car costing this much a lap vs clean air


ALONE_S = 5.0  # nobody of his class this close either side: he is on his own
REPORT_MAX_GAP_S = (
    30.0  # a gap bigger than this is "nobody close", not a number to chase
)


def sure_closing(model, front, back):
    """The race model's road trend: seconds a lap the back car takes out of the gap, but only
    when it is SURE (2 laps of trend). Between fighting cars the gap moves ~1.2 s a lap for
    reasons that are not pace (field study, 25 Sep), so one lap - the old line-to-line game gap -
    is not a trend. None = say nothing about it."""
    t = model.trend(front, back)
    if t is None or not t["sure"]:
        return None
    return t["closing_per_lap"]


class RaceEngineer:
    def __init__(self):
        self.orders = None  # his standing orders: "gaps every lap"
        self.phase = None
        self.formation_called = False
        self.blue_flag = False
        self.yellow = False
        self.limit_steps = None
        self.penalties = None
        self.dents = None
        self.last_report_lap = 0
        self.gaps_at_line = {}  # "ahead" / "behind" -> (identity, gap) at the last line
        self.pits_said = set()  # (time, car) pit entries already called
        self.own_spin_at = None
        self.incident_at = None  # sim time of his last spin or hit
        self.stopped_since = None
        self.asked_if_ok = False
        self.yellow_said_at = None
        self.clean_laps = []  # my road laps with nobody within a second ahead all lap
        self.held_up_said = set()
        self.to_go_at_line = None  # laps to go as counted at the last line
        self.flag_called = False
        self.line_time = None  # sim time of the last line crossing
        self.my_lap = None  # his last lap, the game's or measured by Apex
        self.finish_called = False
        self.last_said_lap = {}  # kind -> lap it was last said

    def after_a_crash(self, moment, me, phase, now):
        """ "You OK? Car's stopped." once, when he is stopped on track just after a spin or a hit."""
        if moment.frame is None:
            return []
        speed = moment.frame.speed_kmh
        if speed > CRAWLING_KMH:
            self.asked_if_ok = False  # racing again: the next incident may ask again
        stopped = speed < STOPPED_KMH and not me.in_pits and phase == GREEN_FLAG
        after_incident = (
            self.incident_at is not None and now - self.incident_at < AFTER_INCIDENT_S
        )
        if not (stopped and after_incident):
            self.stopped_since = None
            return []
        if self.stopped_since is None:
            self.stopped_since = now
        if now - self.stopped_since < STOPPED_FOR_S or self.asked_if_ok:
            return []
        self.asked_if_ok = True
        words = "You OK? Car's stopped."
        ask = spoken(
            "ARE_YOU_OK",
            words,
            now,
            {"speed_kmh": round(speed)},
            template=words,
            priority=RACE_CONTROL,
        )
        ask.immediate = True
        return [ask]

    def update(self, moment):
        # incidents on every frame: they come with the car frames, and the race snapshots (5 a
        # second) missed most of them (27 Sep: the own-spin yellow rule rarely saw its spin)
        for event in moment.events:
            if event.kind == "SPIN":
                self.own_spin_at = event.sim_time
            if event.kind in INCIDENT_KINDS:
                self.incident_at = event.sim_time
        race = moment.race
        if race is None or race.me is None or not moment.new_race:
            return []
        me = race.me
        session = race.session
        now = moment.now
        calls = []

        # the start (E1)
        phase = session.game_phase
        if phase == FORMATION_LAP and not self.formation_called:
            self.formation_called = True
            calls.append(
                spoken(
                    "FORMATION",
                    "Formation lap: weave to put heat in the tyres, and brake hard a few times to warm the brakes.",
                    now,
                    {},
                    template="Formation lap. Heat in the tyres, heat in the brakes.",
                )
            )
        if phase == GREEN_FLAG and self.phase is not None and self.phase < GREEN_FLAG:
            calls.append(urgent("LIGHTS_OUT", "Lights out. Go.", now))
        if phase == SAFETY_CAR and self.phase != SAFETY_CAR:
            calls.append(urgent("SAFETY_CAR", "Safety car. Safety car.", now))
        if phase == GREEN_FLAG and self.phase == SAFETY_CAR:
            calls.append(urgent("GREEN", "Green, green, green.", now))
        # the end: the leader's flag makes this my last lap, unless the lap count already said so
        in_race = moment.session_type in RACE_SESSIONS
        if (
            in_race
            and phase == SESSION_OVER
            and me.finish_status == 0
            and not self.flag_called
        ):
            self.flag_called = True
            already_told = self.to_go_at_line is not None and self.to_go_at_line <= 1
            if not already_told:
                calls.append(
                    spoken(
                        "FLAG_LAST_LAP",
                        "The leader has the flag. This is your last lap: bring it home.",
                        now,
                        {},
                        template="Flag's out. Last lap. Bring it home.",
                    )
                )
        if in_race and me.finish_status == 1 and not self.finish_called:
            self.finish_called = True
            place = said_place(race)
            calls.append(
                spoken(
                    "FINISH",
                    f"Chequered flag. {place}. Tell him where he finished, like a team would.",
                    now,
                    {
                        "place": me.place,
                        "class_place": class_place(race, me.place, me.car_class),
                    },
                    priority=RACE_CONTROL,
                    template=f"Chequered flag. {place}.",
                )
            )
        self.phase = phase

        # flags (E2)
        crawling = moment.frame is not None and moment.frame.speed_kmh < CRAWLING_KMH
        if me.flag == BLUE_FLAG and not self.blue_flag and not crawling:
            calls.append(urgent("BLUE_FLAG", "Blue flag. Let him by on the exit.", now))
        self.blue_flag = me.flag == BLUE_FLAG
        calls.extend(self.after_a_crash(moment, me, phase, now))
        yellow_sectors = [
            i for i, flag in enumerate(session.sector_flags) if flag == SECTOR_YELLOW
        ]
        here, next_one = (
            FLAG_SLOT.get(me.sector),
            FLAG_SLOT.get(NEXT_SECTOR.get(me.sector)),
        )
        yellow_now = here in yellow_sectors or next_one in yellow_sectors
        # not for the yellow his own spin causes, and not twice in 30 s (live 25 Sep: "yellow" twice
        # right after he spun at Indianapolis)
        own_yellow = (
            self.own_spin_at is not None and now - self.own_spin_at < OWN_SPIN_YELLOW_S
        )
        if crawling:
            own_yellow = True
        recent = (
            self.yellow_said_at is not None
            and now - self.yellow_said_at < YELLOW_AGAIN_S
        )
        if (
            yellow_now
            and not self.yellow
            and phase == GREEN_FLAG
            and not own_yellow
            and not recent
        ):
            calls.append(urgent("YELLOW", "Yellow flag. Yellow.", now))
            self.yellow_said_at = now
        self.yellow = yellow_now

        # penalties
        if self.penalties is not None and me.penalties > self.penalties:
            calls.append(
                spoken(
                    "PENALTY",
                    f"Penalty: {me.penalties} outstanding. Serve it.",
                    now,
                    {"penalties": me.penalties},
                    priority=RACE_CONTROL,
                    template="Penalty. Serve it.",
                )
            )
        self.penalties = me.penalties

        # track limits (E3): the game counts limit "steps" and gives a penalty at
        # limit_steps_per_penalty (seen on the tapes: 12 or 20)
        stepped = (
            self.limit_steps is not None and me.track_limit_steps > self.limit_steps
        )
        penalty_at = session.limit_steps_per_penalty
        worth_a_word = True
        if stepped and penalty_at:
            half_way = penalty_at * LIMITS_SAY_FROM_SHARE
            crossed_half = self.limit_steps < half_way <= me.track_limit_steps
            near = me.track_limit_steps >= penalty_at - LIMITS_EVERY_STEP_LAST
            worth_a_word = crossed_half or near
        if stepped and worth_a_word:
            where = moment.corner or "that corner"
            facts = {
                "corner": where,
                "steps": me.track_limit_steps,
                "penalty_at": session.limit_steps_per_penalty,
            }
            calls.append(
                spoken(
                    "TRACK_LIMITS",
                    f"Track limits at {where}. {me.track_limit_steps} steps now, a penalty at {session.limit_steps_per_penalty}. Keep it inside.",
                    now,
                    facts,
                    priority=RACE_CONTROL,
                    template=f"Track limits at {where}. Keep it inside.",
                )
            )
        self.limit_steps = me.track_limit_steps

        # damage (E4)
        dent_total = sum(me.dents)
        if self.dents is not None and dent_total > self.dents:
            calls.append(
                spoken(
                    "DAMAGE",
                    "The car has picked up damage. Tell him, and say we are watching the lap times.",
                    now,
                    {},
                    template="Damage on the car. Watching your times.",
                )
            )
        self.dents = dent_total

        # who is catching whom, and the lap times that matter, at every line in a race
        racing = (
            moment.session_type in RACE_SESSIONS
            and phase == GREEN_FLAG
            and not me.in_pits
        )
        if moment.lap_wrapped:
            from seats.strategist import measured_lap

            if me.laps >= 1:  # at lights out the "lap" is the formation (2:23, 25 Sep)
                self.my_lap = (
                    measured_lap(me.last_lap, self.line_time, now) or self.my_lap
                )
            self.line_time = now
        if moment.lap_wrapped and racing and moment.lap_count >= 1:
            calls.extend(self.race_picture(race, moment.lap_count, now, moment.model))
            if moment.model is not None:
                calls.extend(self.held_up(race, moment.model, moment.corners, now))
        if racing and moment.new_race and moment.model is not None:
            calls.extend(self.pits_ahead(race, moment.model, now))

        # where you are in the race: every few laps, and EVERY lap when he is on his own (live 25 Sep:
        # he joined 3 minutes late, nobody was within two minutes, and the radio went quiet for the
        # race - a real engineer talks a lone driver through his laps)
        alone = racing and self.alone(race, moment.model)
        every_lap = alone or (
            self.orders is not None and self.orders.gaps_every_lap()
        )  # his order
        due = moment.lap_count >= self.last_report_lap + (
            1 if every_lap else REPORT_EVERY_LAPS
        )
        if moment.lap_wrapped and due and moment.lap_count > 1:
            self.last_report_lap = moment.lap_count
            calls.append(self.gap_report(race, now, moment.model))
        return calls

    def race_picture(self, race, lap, now, model=None):
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race, model)
        to_go = laps_to_go(race, self.my_lap, model)
        self.to_go_at_line = to_go
        final_lap = None
        if to_go is not None:
            # lap is the lap he is ON and to_go counts it (checked on the 23 Sep tape): the
            # last lap is lap + to_go - 1, and a car caught within n laps is caught on lap + n - 1
            final_lap = lap + to_go - 1
        calls = []
        if ahead is not None and gap_ahead is not None:
            gained = self.gain_since_last_line("ahead", ahead, gap_ahead)
            if model is not None:
                gained = sure_closing(model, ahead.id, "me")
            chase = self.chase_call(
                ahead, gap_ahead, gained, to_go, lap, final_lap, now
            )
            if chase is not None:
                calls.append(chase)
        if behind is not None and gap_behind is not None:
            his_gain = self.gain_since_last_line("behind", behind, gap_behind)
            if model is not None:
                his_gain = sure_closing(model, "me", behind.id)
            defence = self.defence_call(
                behind, gap_behind, his_gain, lap, final_lap, now
            )
            if defence is not None:
                calls.append(defence)
        self.gaps_at_line = {}
        if ahead is not None and gap_ahead is not None:
            self.gaps_at_line["ahead"] = (identity(ahead), gap_ahead)
        if behind is not None and gap_behind is not None:
            self.gaps_at_line["behind"] = (identity(behind), gap_behind)
        return calls

    # ---- race awareness from the race model (25 Sep) ------------------------------------------
    def pits_ahead(self, race, model, now):
        """The car just ahead in my class went into the pit lane: that place is coming to me."""
        calls = []
        for when, key, what in model.pit_events:
            if what != "in" or (when, key) in self.pits_said:
                continue
            car = model.car(key)
            if (
                car is None
                or car.car_class != race.me.car_class
                or car.place >= race.me.place
                or race.me.place - car.place > PITS_AHEAD_PLACES
            ):
                continue
            self.pits_said.add((when, key))
            words = f"P{class_place(race, car.place, car.car_class)}'s in the pits. That's a place for you."
            calls.append(
                spoken(
                    "PITS_AHEAD", words, now, {"their_place": car.place}, template=words
                )
            )
        return calls

    def held_up(self, race, model, corners, now):
        """At the line: a lap spent within a second of the same car, not getting closer, and my
        road laps behind it clearly slower than my laps in clean air -> say what it costs and the
        two ways out. Real engineers make this call; Apex never did."""
        lap = model.road_lap("me")
        ahead, _, _, _ = same_class_neighbours(race, model)
        stuck_since = (
            model.close_since.get((ahead.id, "me")) if ahead is not None else None
        )
        followed = (
            stuck_since is not None
            and lap is not None
            and now - stuck_since >= 0.9 * lap
        )
        if lap is not None and not followed:
            self.clean_laps.append(lap)
            return []
        if not followed or len(self.clean_laps) < 1 or ahead.id in self.held_up_said:
            return []
        clean = min(self.clean_laps[-3:])
        cost = round(lap - clean, 1)
        trend = model.trend(ahead.id, "me")
        closing = trend is not None and trend["closing_per_lap"] > 0.2
        if cost < HELD_UP_COST_S or closing:
            return []
        self.held_up_said.add(ahead.id)
        strong = None
        if corners:
            gains = model.corner_gains("me", ahead.id, corners)
            strong = (
                max(gains, key=gains.get)
                if gains and max(gains.values()) >= 0.15
                else None
            )
        where = f"Pass it into {strong}" if strong else "Pass it"
        words = f"You're losing {cost} a lap stuck behind that car. {where}, or drop back to two seconds."
        return [
            spoken(
                "HELD_UP",
                words,
                now,
                {"cost_s": cost, "corner": strong},
                template=words,
            )
        ]

    def gain_since_last_line(self, side, car, gap):
        """How much the gap shrank over the last lap (> 0: shrinking), or None when this is not
        the same car as at the last line."""
        before = self.gaps_at_line.get(side)
        if before is None or before[0] != identity(car):
            return None
        return round(before[1] - gap, 2)

    def said_recently(self, kind, lap, every):
        last = self.last_said_lap.get(kind)
        return last is not None and lap - last < every

    def chase_call(self, ahead, gap, gained, to_go, lap, final_lap, now):
        if gap < FIGHT_GAP_S or gap > WATCH_GAP_S or gained is None:
            return None
        driver = ahead.driver
        gap = round(gap, 1)
        if gained >= TREND_S_PER_LAP:
            # "by lap N" is an upper bound: sure catch forecasts were right 20 of 21 times on his
            # tapes, their timing off by a median 64%, inside 1.5x the forecast ~95% (25 Sep)
            catch_lap = lap + math.ceil(round(gap / gained * CATCH_UPPER, 3)) - 1
            if final_lap is None or catch_lap <= final_lap:
                self.last_said_lap["CATCHING"] = lap
                facts = {
                    "driver": driver,
                    "gap_s": gap,
                    "gain_per_lap_s": gained,
                    "catch_lap": catch_lap,
                }
                return spoken(
                    "CATCHING",
                    f"The car ahead is {gap} s up the road and you are taking {gained} s a lap out of that: at this pace you are on it by lap {catch_lap}. Keep pushing.",
                    now,
                    facts,
                    template=f"Car ahead, {gap}. You're taking {tenths_words(gained)} a lap. On it by lap {catch_lap}.",
                )
        # not closing fast enough to catch before the flag: the lap time that would
        if (
            to_go is None
            or to_go < 1
            or ahead.last_lap <= 0
            or self.said_recently("PACE_TARGET", lap, PACE_TARGET_EVERY_LAPS)
        ):
            return None
        needed = gap / to_go
        if needed > MAX_GAIN_NEEDED_SHARE * ahead.last_lap:
            return None
        target = ahead.last_lap - needed
        their_min, their_sec = lap_time_parts(ahead.last_lap)
        target_min, target_sec = lap_time_parts(target)
        self.last_said_lap["PACE_TARGET"] = lap
        facts = {
            "driver": driver,
            "gap_s": gap,
            "laps_to_go": to_go,
            "their_minutes": their_min,
            "their_seconds": their_sec,
            "target_minutes": target_min,
            "target_seconds": target_sec,
        }
        return spoken(
            "PACE_TARGET",
            f"The car ahead is {gap} s up, lapping {their_min}:{their_sec:04.1f}. To catch it by the flag you need {target_min}:{target_sec:04.1f} laps.",
            now,
            facts,
            template=f"Car ahead's doing {their_min}:{their_sec:04.1f}. You need {target_min}:{target_sec:04.1f} to catch it by the flag.",
        )

    def defence_call(self, behind, gap, his_gain, lap, final_lap, now):
        if gap < FIGHT_GAP_S or gap > WATCH_GAP_S or his_gain is None:
            return None
        driver = behind.driver
        gap = round(gap, 1)
        if his_gain >= TREND_S_PER_LAP:
            reach_lap = lap + math.ceil(round(gap / his_gain, 3)) - 1
            if final_lap is not None and reach_lap > final_lap:
                return None  # the race runs out before he gets there
            facts = {
                "driver": driver,
                "gap_s": gap,
                "gain_per_lap_s": his_gain,
                "reach_lap": reach_lap,
            }
            conclusion = f"The car behind is {gap} s back and closing {his_gain} s a lap: on your gearbox by lap {reach_lap}."
            template = f"Car behind, {gap}. Closing {tenths_words(his_gain)} a lap. On you by lap {reach_lap}."
            if behind.last_lap > 0:
                his_min, his_sec = lap_time_parts(behind.last_lap)
                facts.update({"their_minutes": his_min, "their_seconds": his_sec})
                conclusion += f" It is lapping {his_min}:{his_sec:04.1f}: match that and the gap holds."
                template += f" Match {his_min}:{his_sec:04.1f}."
            self.last_said_lap["THREAT_BEHIND"] = lap
            return spoken("THREAT_BEHIND", conclusion, now, facts, template=template)
        if (
            -his_gain >= 2 * TREND_S_PER_LAP
            and gap <= 3.0
            and not self.said_recently("GAP_GROWING", lap, GROWING_EVERY_LAPS)
        ):
            self.last_said_lap["GAP_GROWING"] = lap
            facts = {"driver": driver, "gap_s": gap}
            return spoken(
                "GAP_GROWING",
                f"The gap back to the car behind is growing, {gap} s now. Whatever you are doing, keep doing it.",
                now,
                facts,
                template=f"Gap behind growing, {gap}. Keep doing that.",
            )
        return None

    def alone(self, race, model):
        """No car of his class within ALONE_S ahead or behind on the road."""
        _, gap_ahead, _, gap_behind = same_class_neighbours(race, model)
        near = [
            g for g in (gap_ahead, gap_behind) if g is not None and 0 <= g <= ALONE_S
        ]
        return not near

    def gap_report(self, race, now, model=None):
        """Place, his lap against his best, and the cars either side with the road trend. Gaps come
        from the race model: the game's own gap said "closing 43.8" and "car behind -213.6" in his
        online lobby (25 Sep). A gap over REPORT_MAX_GAP_S is said as "nobody close"."""
        me = race.me
        facts = {
            "place": me.place,
            "class_place": class_place(race, me.place, me.car_class),
        }
        parts = [f"{said_place(race)}."]
        if self.my_lap:
            best = me.best_lap if me.best_lap > 0 else self.my_lap
            minutes, seconds = lap_time_parts(self.my_lap)
            facts.update({"lap_minutes": minutes, "lap_seconds": seconds})
            if abs(self.my_lap - best) < 0.05:
                parts.append(f"{minutes}:{seconds:04.1f}, your best.")
            else:
                off = round(self.my_lap - best, 1)
                facts["off_best_s"] = off
                parts.append(f"{minutes}:{seconds:04.1f}, {off} off your best.")
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race, model)
        if (
            ahead is not None
            and gap_ahead is not None
            and 0 <= gap_ahead <= REPORT_MAX_GAP_S
        ):
            gap = round(gap_ahead, 1)
            facts["gap_ahead_s"] = gap
            words = f"Car ahead {gap}."
            closing = sure_closing(model, ahead.id, "me") if model is not None else None
            if closing is not None and abs(closing) >= 0.2:
                facts["closing_s"] = abs(round(closing, 1))
                words += (
                    f" You're taking {abs(round(closing, 1))} a lap."
                    if closing > 0
                    else f" Losing {abs(round(closing, 1))} a lap."
                )
            parts.append(words)
        elif ahead is not None:
            parts.append("Nobody close ahead.")
        if (
            behind is not None
            and gap_behind is not None
            and 0 <= gap_behind <= REPORT_MAX_GAP_S
        ):
            gap = round(gap_behind, 1)
            facts["gap_behind_s"] = gap
            parts.append(f"Car behind {gap} back.")
        conclusion = " ".join(parts)
        return spoken("GAP_REPORT", conclusion, now, facts, template=conclusion)
