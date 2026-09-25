"""Race engineer: the main voice. The start, flags, penalties, track limits, damage, and
where you are in the race every few laps.

Everything comes from the race snapshots. Urgent things (lights out, safety car, blue flag,
yellow) come from the voice bank; the rest is phrased by the persona on the next straight.
"""
import math

from radio import Call, RACE_CONTROL, ENGINEER
from race_state import identity, same_class_neighbours, laps_to_go

FORMATION = 3                 # mGamePhase values
GREEN = 5
SAFETY_CAR = 6
FLAG = 8                      # the leader has taken the chequered flag
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

# the race picture at the line (24 Sep: "I got no data of other cars, like the car behind is
# closing in, or I'm closing on the next car, keep going" and "the lap times I need to catch
# the next guy", "at this pace the next battle is on lap 5 or 6")
RACE_SESSIONS = range(10, 14)
WATCH_GAP_S = 8.0             # further than this, a car is not a target yet
FIGHT_GAP_S = 0.8             # closer than this, the spotter and racecraft have it
TREND_S_PER_LAP = 0.1         # a tenth a lap is a trend; less is noise
# needing more than this share of a lap, every lap, is not a target: 0.66 s at Monza, 1.4 s at
# Le Mans (a fixed 1 s hid the call at Le Mans, where 1.1 s is 0.5 % of a lap)
MAX_GAIN_NEEDED_SHARE = 0.006
PACE_TARGET_EVERY_LAPS = 2
GROWING_EVERY_LAPS = 3


def urgent(kind, text, now, priority=RACE_CONTROL):
    return Call(seat="race_engineer", kind=kind, sim_time=now, priority=priority, ttl=3.0,
                conclusion=text, template=text, urgent=True)


def spoken(kind, conclusion, now, facts, template=None, priority=ENGINEER, ttl=REPORT_TTL_S, evidence=None):
    return Call(seat="race_engineer", kind=kind, sim_time=now, priority=priority, ttl=ttl,
                conclusion=conclusion, facts=facts, template=template, evidence=evidence or {})


def lap_time_parts(seconds_total):
    minutes = int(seconds_total // 60)
    seconds = round(seconds_total - minutes * 60, 1)
    if seconds >= 60.0:
        minutes += 1
        seconds = round(seconds - 60.0, 1)
    return minutes, seconds


def tenths(seconds):
    count = round(seconds * 10)
    if count <= 1:
        return "a tenth"
    if count >= 10:
        return f"{round(seconds, 1)} seconds"
    return f"{count} tenths"


def car_behind(race):
    for opponent in race.opponents:
        if opponent.place == race.me.place + 1:
            return opponent
    return None


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
        self.phase = None
        self.formation_called = False
        self.blue_flag = False
        self.yellow = False
        self.limit_steps = None
        self.penalties = None
        self.dents = None
        self.last_report_lap = 0
        self.last_gap_ahead = None
        self.gaps_at_line = {}        # "ahead" / "behind" -> (identity, gap) at the last line
        self.to_go_at_line = None     # laps to go as counted at the last line
        self.flag_called = False
        self.line_time = None         # sim time of the last line crossing
        self.my_lap = None            # his last lap, the game's or measured by Apex
        self.finish_called = False
        self.last_said_lap = {}       # kind -> lap it was last said

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
        # the end: the leader's flag makes this my last lap, unless the lap count already said so
        in_race = moment.session_type in RACE_SESSIONS
        if in_race and phase == FLAG and me.finish_status == 0 and not self.flag_called:
            self.flag_called = True
            already_told = self.to_go_at_line is not None and self.to_go_at_line <= 1
            if not already_told:
                calls.append(spoken("FLAG_LAST_LAP", "The leader has the flag. This is your last lap: bring it home.",
                                    now, {}, template="Flag's out. Last lap. Bring it home."))
        if in_race and me.finish_status == 1 and not self.finish_called:
            self.finish_called = True
            calls.append(spoken("FINISH", f"Chequered flag. P{me.place}. Tell him where he finished, like a team would.",
                                now, {"place": me.place}, priority=RACE_CONTROL,
                                template=f"Chequered flag. P{me.place}."))
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

        # who is catching whom, and the lap times that matter, at every line in a race
        racing = moment.session_type in RACE_SESSIONS and phase == GREEN and not me.in_pits
        if moment.lap_wrapped:
            from seats.strategist import measured_lap
            self.my_lap = measured_lap(me.last_lap, self.line_time, now) or self.my_lap
            self.line_time = now
        if moment.lap_wrapped and racing and moment.lap_count >= 1:
            calls.extend(self.race_picture(race, moment.lap_count, now, moment.model))

        # where you are in the race, every few laps, said on a straight
        if moment.lap_wrapped and moment.lap_count >= self.last_report_lap + REPORT_EVERY_LAPS and moment.lap_count > 1:
            self.last_report_lap = moment.lap_count
            calls.append(self.gap_report(race, now))
        return calls

    def race_picture(self, race, lap, now, model=None):
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race, model)
        to_go = laps_to_go(race, self.my_lap)
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
            chase = self.chase_call(ahead, gap_ahead, gained, to_go, lap, final_lap, now)
            if chase is not None:
                calls.append(chase)
        if behind is not None and gap_behind is not None:
            his_gain = self.gain_since_last_line("behind", behind, gap_behind)
            if model is not None:
                his_gain = sure_closing(model, "me", behind.id)
            defence = self.defence_call(behind, gap_behind, his_gain, lap, final_lap, now)
            if defence is not None:
                calls.append(defence)
        self.gaps_at_line = {}
        if ahead is not None and gap_ahead is not None:
            self.gaps_at_line["ahead"] = (identity(ahead), gap_ahead)
        if behind is not None and gap_behind is not None:
            self.gaps_at_line["behind"] = (identity(behind), gap_behind)
        return calls

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
            catch_lap = lap + math.ceil(round(gap / gained, 3)) - 1
            if final_lap is None or catch_lap <= final_lap:
                self.last_said_lap["CATCHING"] = lap
                facts = {"driver": driver, "gap_s": gap, "gain_per_lap_s": gained, "catch_lap": catch_lap}
                return spoken("CATCHING",
                              f"The car ahead is {gap} s up the road and you are taking {gained} s a lap out of that: at this pace you are on it by lap {catch_lap}. Keep pushing.",
                              now, facts,
                              template=f"Car ahead, {gap}. You're taking {tenths(gained)} a lap. On it by lap {catch_lap}.")
        # not closing fast enough to catch before the flag: the lap time that would
        if to_go is None or to_go < 1 or ahead.last_lap <= 0 or self.said_recently("PACE_TARGET", lap, PACE_TARGET_EVERY_LAPS):
            return None
        needed = gap / to_go
        if needed > MAX_GAIN_NEEDED_SHARE * ahead.last_lap:
            return None
        target = ahead.last_lap - needed
        their_min, their_sec = lap_time_parts(ahead.last_lap)
        target_min, target_sec = lap_time_parts(target)
        self.last_said_lap["PACE_TARGET"] = lap
        facts = {"driver": driver, "gap_s": gap, "laps_to_go": to_go,
                 "their_minutes": their_min, "their_seconds": their_sec,
                 "target_minutes": target_min, "target_seconds": target_sec}
        return spoken("PACE_TARGET",
                      f"The car ahead is {gap} s up, lapping {their_min}:{their_sec:04.1f}. To catch it by the flag you need {target_min}:{target_sec:04.1f} laps.",
                      now, facts,
                      template=f"Car ahead's doing {their_min}:{their_sec:04.1f}. You need {target_min}:{target_sec:04.1f} to catch it by the flag.")

    def defence_call(self, behind, gap, his_gain, lap, final_lap, now):
        if gap < FIGHT_GAP_S or gap > WATCH_GAP_S or his_gain is None:
            return None
        driver = behind.driver
        gap = round(gap, 1)
        if his_gain >= TREND_S_PER_LAP:
            reach_lap = lap + math.ceil(round(gap / his_gain, 3)) - 1
            if final_lap is not None and reach_lap > final_lap:
                return None                      # the race runs out before he gets there
            facts = {"driver": driver, "gap_s": gap, "gain_per_lap_s": his_gain, "reach_lap": reach_lap}
            conclusion = f"The car behind is {gap} s back and closing {his_gain} s a lap: on your gearbox by lap {reach_lap}."
            template = f"Car behind, {gap}. Closing {tenths(his_gain)} a lap. On you by lap {reach_lap}."
            if behind.last_lap > 0:
                his_min, his_sec = lap_time_parts(behind.last_lap)
                facts.update({"their_minutes": his_min, "their_seconds": his_sec})
                conclusion += f" It is lapping {his_min}:{his_sec:04.1f}: match that and the gap holds."
                template += f" Match {his_min}:{his_sec:04.1f}."
            self.last_said_lap["THREAT_BEHIND"] = lap
            return spoken("THREAT_BEHIND", conclusion, now, facts, template=template)
        if -his_gain >= 2 * TREND_S_PER_LAP and gap <= 3.0 and not self.said_recently("GAP_GROWING", lap, GROWING_EVERY_LAPS):
            self.last_said_lap["GAP_GROWING"] = lap
            facts = {"driver": driver, "gap_s": gap}
            return spoken("GAP_GROWING", f"The gap back to the car behind is growing, {gap} s now. Whatever you are doing, keep doing it.",
                          now, facts, template=f"Gap behind growing, {gap}. Keep doing that.")
        return None

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
