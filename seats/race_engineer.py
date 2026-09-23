"""Race engineer: the main voice. The start, flags, penalties, track limits, damage, and
where you are in the race every few laps.

Everything comes from the race snapshots. Urgent things (lights out, safety car, blue flag,
yellow) come from the voice bank; the rest is phrased by the persona on the next straight.
"""
from radio import Call, RACE_CONTROL, ENGINEER

FORMATION = 3                 # mGamePhase values
GREEN = 5
SAFETY_CAR = 6
BLUE_FLAG = 6                 # mFlag value
# mSectorFlag read 11 in every sector for a whole green session (23 Sep 2026): 11 and 0 are
# "no yellow". GUESSED: any other value is a local yellow; the first real yellow will confirm.
NO_YELLOW = {0, 11}
# Measured in the race of 23 Sep: 1 (and sometimes 3) = a local yellow, lasting 10-20 s, 13
# times in 27 minutes around a 13.6 km lap. Only the yellow in my sector or the next matters.
# GUESSED: the flag list uses the game's own sector numbering (index 0 = sector 3), like mSector.
NEXT_SECTOR = {1: 2, 2: 0, 0: 1}
REPORT_EVERY_LAPS = 3
REPORT_TTL_S = 20.0           # a gap report can wait for a straight, but not for ever


def urgent(kind, text, now, priority=RACE_CONTROL):
    return Call(seat="race_engineer", kind=kind, sim_time=now, priority=priority, ttl=3.0,
                conclusion=text, template=text, urgent=True)


def spoken(kind, conclusion, now, facts, template=None, priority=ENGINEER, ttl=REPORT_TTL_S, evidence=None):
    return Call(seat="race_engineer", kind=kind, sim_time=now, priority=priority, ttl=ttl,
                conclusion=conclusion, facts=facts, template=template, evidence=evidence or {})


def car_behind(race):
    for opponent in race.opponents:
        if opponent.place == race.me.place + 1:
            return opponent
    return None


class RaceEngineer:
    def __init__(self):
        self.phase = None
        self.formation_called = False
        self.blue_flag = False
        self.yellow = False
        self.limit_steps = None
        self.penalties = None
        self.dents = None
        self.last_report_lap = 0
        self.last_gap_ahead = None

    def update(self, moment):
        race = moment.race
        if race is None or race.me is None or not moment.new_race:
            return []
        me = race.me
        session = race.session
        now = moment.now
        calls = []

        # the start (E1)
        phase = session.game_phase
        if phase == FORMATION and not self.formation_called:
            self.formation_called = True
            calls.append(spoken("FORMATION",
                                "Formation lap: weave to put heat in the tyres, and brake hard a few times to warm the brakes.",
                                now, {}, template="Formation lap. Heat in the tyres, heat in the brakes."))
        if phase == GREEN and self.phase is not None and self.phase < GREEN:
            calls.append(urgent("LIGHTS_OUT", "Lights out. Go.", now))
        if phase == SAFETY_CAR and self.phase != SAFETY_CAR:
            calls.append(urgent("SAFETY_CAR", "Safety car. Safety car.", now))
        if phase == GREEN and self.phase == SAFETY_CAR:
            calls.append(urgent("GREEN", "Green, green, green.", now))
        self.phase = phase

        # flags (E2)
        if me.flag == BLUE_FLAG and not self.blue_flag:
            calls.append(urgent("BLUE_FLAG", "Blue flag. Let him by on the exit.", now))
        self.blue_flag = me.flag == BLUE_FLAG
        yellow_sectors = [i for i, flag in enumerate(session.sector_flags) if flag not in NO_YELLOW]
        yellow_now = me.sector in yellow_sectors or NEXT_SECTOR.get(me.sector) in yellow_sectors
        if yellow_now and not self.yellow and phase == GREEN:
            calls.append(urgent("YELLOW", "Yellow flag. Yellow.", now))
        self.yellow = yellow_now

        # penalties
        if self.penalties is not None and me.penalties > self.penalties:
            calls.append(spoken("PENALTY", f"Penalty: {me.penalties} outstanding. Serve it.",
                                now, {"penalties": me.penalties}, priority=RACE_CONTROL,
                                template="Penalty. Serve it."))
        self.penalties = me.penalties

        # track limits (E3). GUESSED: the game counts limit "steps" and gives a penalty at
        # limit_steps_per_penalty; confirm the numbers on the first v2 tape.
        if self.limit_steps is not None and me.track_limit_steps > self.limit_steps:
            where = moment.corner or "that corner"
            facts = {"corner": where, "steps": me.track_limit_steps,
                     "penalty_at": session.limit_steps_per_penalty}
            calls.append(spoken("TRACK_LIMITS",
                                f"Track limits at {where}. {me.track_limit_steps} steps now, a penalty at {session.limit_steps_per_penalty}. Keep it inside.",
                                now, facts, priority=RACE_CONTROL, template=f"Track limits at {where}. Keep it inside."))
        self.limit_steps = me.track_limit_steps

        # damage (E4)
        dent_total = sum(me.dents)
        if self.dents is not None and dent_total > self.dents:
            calls.append(spoken("DAMAGE", "The car has picked up damage. Tell him, and say we are watching the lap times.",
                                now, {}, template="Damage on the car. Watching your times."))
        self.dents = dent_total

        # where you are in the race, every few laps, said on a straight
        if moment.lap_wrapped and moment.lap_count >= self.last_report_lap + REPORT_EVERY_LAPS and moment.lap_count > 1:
            self.last_report_lap = moment.lap_count
            calls.append(self.gap_report(race, now))
        return calls

    def gap_report(self, race, now):
        me = race.me
        facts = {"place": me.place}
        parts = [f"P{me.place}."]
        if me.place > 1 and me.laps_behind_leader == 0:
            gap_ahead = round(me.time_behind_next, 1)
            facts["gap_ahead_s"] = gap_ahead
            parts.append(f"Gap to the car ahead {gap_ahead} seconds.")
            if self.last_gap_ahead is not None:
                change = round(self.last_gap_ahead - gap_ahead, 1)
                if abs(change) >= 0.2:
                    facts["gap_change_s"] = abs(change)
                    direction = "closing" if change > 0 else "losing"
                    parts.append(f"You are {direction} {abs(change)} since the last report.")
            self.last_gap_ahead = gap_ahead
        behind = car_behind(race)
        if behind is not None:
            gap_behind = round(behind.time_behind_next, 1)
            facts["gap_behind_s"] = gap_behind
            parts.append(f"Car behind {gap_behind} seconds back.")
        conclusion = " ".join(parts)
        return spoken("GAP_REPORT", conclusion, now, facts, template=conclusion)
