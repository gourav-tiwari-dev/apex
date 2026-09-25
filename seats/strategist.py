"""Strategist: will the fuel (or virtual energy) last, are the tyres in their window, is the
weather turning, and the last lap.

Fuel is measured, never assumed: litres used per lap come from my own laps in this session,
so the first call waits until two full laps are done.
"""
import math
import statistics

from radio import Call, STRATEGY, ENGINEER
from race_state import laps_to_go, leader_margin

FIRST_CALL_AFTER_LAPS = 2      # need two measured laps before saying anything about fuel
RECHECK_EVERY_LAPS = 3
SPARE_COMFORTABLE = 0.5        # laps of fuel spare above which it is simply "fine"
# GUESSED upper edge of the GT3 slick window, Celsius, averaged across the tread.
# Confirm on the first v2 tape: the audit shows real temperatures.
TYRE_HOT_C = 105.0
TYRE_NAMES = ["front left", "front right", "rear left", "rear right"]
CALL_TTL_S = 25.0
RACE_SESSIONS = range(10, 14)

# Live 25 Sep: Apex was restarted mid-race, had no laps at the line, and so no fuel picture.
# He had 0.6 laps of energy for 1.7 laps of race; the radio read out litres, the coach said
# "no need to pit", nothing warned him. Usage is now measured CONTINUOUSLY, per metre driven,
# so it is known about 1.5 km after any start, restart or refuel.
LIVE_MIN_M = 1500.0            # track driven before the live usage counts
LIVE_KEEP_LAPS = 1.2           # usage over the last lap or so: the current pace of burn
# GUESSED: lift and coast plus short-shifting saves about 8% of usage (tune from his laps)
LIFT_AND_COAST_SAVES = 0.08
FUEL_RECHECK_S = 15.0
CLOSE_CALL_S = 6.0             # the leader this close to beating the clock: "last lap" is not sure
VERDICT_WORDS = {
    "fine": "{what}'s fine to the flag, {spare} laps spare. Push.",
    "tight": "{what}'s tight, {spare} laps spare. Lift and coast into the big stops.",
    "save": "{what}'s short by {short} laps. Lift and coast every braking zone and short-shift, or you won't make it.",
    "box": "Box this lap for fuel. {what} won't make the flag, short by {short} laps.",
    # after a save call, the saving itself makes the numbers "fine": say so, don't say push
    # (live 25 Sep: "short, lift and coast" -> he saved -> "fine, push" 47 s after "box this lap")
    "saving": "Saving's working, {spare} laps spare. Keep lifting into the big stops.",
}
PUSH_AGAIN_SPARE = 1.0         # after saving, "push" again only with a full lap spare


def verdict_of(spare, laps_left):
    if spare >= SPARE_COMFORTABLE:
        return "fine"
    if spare >= 0:
        return "tight"
    if -spare <= LIFT_AND_COAST_SAVES * max(laps_left, 0.5):
        return "save"
    return "box"


def fuel_words(picture):
    what = "Fuel" if picture["limit"] == "fuel" else "Energy"
    return VERDICT_WORDS[picture["verdict"]].format(what=what, spare=picture["spare_laps"],
                                                    short=abs(picture["spare_laps"]))


def call(kind, conclusion, now, facts, template, priority=STRATEGY):
    return Call(seat="strategist", kind=kind, sim_time=now, priority=priority, ttl=CALL_TTL_S,
                conclusion=conclusion, facts=facts, template=template)


SHORTEST_LAP_S = 30.0
LONGEST_LAP_S = 900.0


def measured_lap(game_lap, line_time, now):
    """The lap he just finished: the game's time, or Apex's own clock between two line
    crossings when the game posts -1 (an invalid lap). Live 24 Sep: laps 1-4 all came back
    -1, so fuel and pace stayed "unknown" the whole race."""
    if game_lap > 0:
        return game_lap
    if line_time is None:
        return None
    lap = now - line_time
    if SHORTEST_LAP_S <= lap <= LONGEST_LAP_S:
        return round(lap, 3)
    return None


class Strategist:
    def __init__(self):
        self.fuel_at_line = []         # fuel in the tank each time I crossed the line
        self.energy_at_line = []
        self.lap_times = []
        self.last_fuel_state = None
        self.last_fuel_check_lap = 0
        self.hot_laps = {}             # tyre index -> consecutive laps above the window
        self.tyre_called = set()
        self.rain_called = False
        self.last_lap_called = False
        self.leader_laps = None        # the leader's laps done, to see it cross the line
        self.fuel_now = None           # the latest fuel picture, for "how's the fuel?" on the radio
        self.line_time = None          # sim time when he last crossed the line
        self.track_m = None            # lap length, the longest lap distance any car showed
        self.burn = []                 # (distance driven, fuel, energy) since the last refuel
        self.wraps = 0
        self.last_lap_dist = None
        self.last_live_check = None
        self.last_live_verdict = None
        self.box_said_lap = None
        self.told_to_save = False      # once told to save, "fine" means the saving is working
        # v3 step 5b: every lap for "how were my last laps / my sectors" on the radio. The laps
        # table in apex.db has no times for live sessions (all None, checked 25 Sep), so the
        # sectors are timed here on Apex's own clock, like measured_lap
        self.lap_records = []          # {"lap", "time_s", "sectors_s", "fuel_used", "valid"}
        self.sector_marks = {}         # 1 / 2 -> sim time sector 1 / 2 ended this lap
        self.last_sector = None

    def laps_left(self, race):
        lap_time = None
        if self.lap_times:
            lap_time = statistics.median(self.lap_times[-3:])
        elif race.me.last_lap > 0 or race.me.best_lap > 0:
            # Apex started mid-race (25 Sep): no line crossings timed yet, the game's own lap
            lap_time = race.me.last_lap if race.me.last_lap > 0 else race.me.best_lap
        return laps_to_go(race, lap_time)

    def usage_per_lap(self, readings):
        used = []
        for before, after in zip(readings, readings[1:]):
            if before > after:              # a refuel makes the difference negative: skip it
                used.append(before - after)
        if len(used) < FIRST_CALL_AFTER_LAPS:
            return None
        return statistics.median(used[-3:])

    def update(self, moment):
        race = moment.race
        if race is None or race.me is None:
            return []
        me = race.me
        now = moment.now
        calls = []
        # fuel-to-the-flag and last-lap calls are race calls: in qualifying (live 25 Sep) they said
        # "Box this lap for fuel" and "Last lap. Bring it home." Old tapes carry no session type.
        racing = moment.session_type is None or moment.session_type in RACE_SESSIONS

        if moment.new_race and racing:
            calls.extend(self.live_fuel(race, moment, now))
            calls.extend(self.leader_over_the_line(race, now))

        # the game's sector numbers: 1, 2, then 0 for sector 3
        if moment.new_race and me.sector != self.last_sector:
            if self.last_sector == 1 and me.sector == 2:
                self.sector_marks[1] = now
            elif self.last_sector == 2 and me.sector == 0:
                self.sector_marks[2] = now
            self.last_sector = me.sector

        if moment.lap_wrapped and moment.lap_count >= 1:
            self.fuel_at_line.append(me.fuel)
            self.energy_at_line.append(me.virtual_energy)
            lap_time = measured_lap(me.last_lap, self.line_time, now)
            if lap_time is not None:
                self.lap_times.append(lap_time)
                self.lap_records.append(self.lap_record(moment.lap_count, lap_time, me, now))
            self.sector_marks = {}
            self.line_time = now
            # the live picture (burn per metre) wins; the line picture fills in before it exists
            self.fuel_now = self.live_picture(race, 0.0) or self.fuel_picture(race)
            fuel_call = self.fuel_check(race, moment.lap_count, now) if racing else None
            if fuel_call is not None:
                calls.append(fuel_call)
            calls.extend(self.tyre_check(me, now))
            # laps to go is exact only at the line: mid-lap it would say "last lap" a lap early.
            # A last lap that starts mid-lap (the leader's flag) is the race engineer's call.
            laps_left = self.laps_left(race)
            if racing and laps_left is not None and laps_left <= 1 and not self.last_lap_called:
                self.last_lap_called = True
                margin = leader_margin(race)
                words = "Last lap. Bring it home."
                if margin is not None and 0 <= margin < CLOSE_CALL_S:
                    words = "Last lap, unless the leader beats the clock. I'll tell you."
                calls.append(call("LAST_LAP", words, now, {"leader_margin_s": margin}, words,
                                  priority=ENGINEER))

        if moment.new_race:
            if race.session.raining >= 0.1 and not self.rain_called:
                self.rain_called = True
                calls.append(call("RAIN", f"Rain is starting, severity {race.session.raining}. Grip will drop.",
                                  now, {"rain": race.session.raining}, "Rain's coming. Grip's going away."))
        return calls

    def leader_over_the_line(self, race, now):
        """After "last lap": the leader crossing with time still on the clock means one more lap
        for everyone. Seen, not predicted (the margin was ~2 s on 25 Sep)."""
        leader = next((o for o in race.opponents if o.place == 1), None)
        if leader is None or race.session.max_laps < 1000:
            return []
        crossed = self.leader_laps is not None and leader.laps > self.leader_laps
        self.leader_laps = leader.laps
        if not (crossed and self.last_lap_called and race.session.time_remaining > 0):
            return []
        self.last_lap_called = False                  # the real last lap is called at the line
        words = "One more lap after this one. The leader beat the clock."
        extra = call("EXTRA_LAP", words, now, {"time_left_s": round(race.session.time_remaining)}, words,
                     priority=ENGINEER)
        extra.immediate = True
        return [extra]

    # ---- fuel, measured all the time ------------------------------------------------------------
    def see_burn(self, race, lap_dist):
        for car in race.opponents:
            if self.track_m is None or car.lap_dist > self.track_m:
                self.track_m = car.lap_dist
        if self.track_m is None or self.track_m < 1000:
            return
        if self.last_lap_dist is not None and lap_dist < self.last_lap_dist - self.track_m / 2:
            self.wraps += 1
        self.last_lap_dist = lap_dist
        me = race.me
        if me.in_pits:
            self.burn = []                     # pit lane and refuelling: start again after it
            return
        distance = self.wraps * self.track_m + lap_dist
        if self.burn and (me.fuel > self.burn[-1][1] + 0.5 or distance < self.burn[-1][0]):
            self.burn = []                     # refuelled, or a reset
        self.burn.append((distance, me.fuel, me.virtual_energy))
        while self.burn and distance - self.burn[0][0] > LIVE_KEEP_LAPS * self.track_m:
            self.burn.pop(0)

    def live_usage(self):
        """(litres a lap, energy a lap) from the burn over the last lap or so, or None."""
        if len(self.burn) < 2 or not self.track_m:
            return None
        (d0, f0, e0), (d1, f1, e1) = self.burn[0], self.burn[-1]
        driven = d1 - d0
        if driven < LIVE_MIN_M:
            return None
        return (f0 - f1) / driven * self.track_m, (e0 - e1) / driven * self.track_m

    def laps_remaining(self, race, lap_dist):
        """Laps of driving still to do, the part of this lap included (1.7, not "2 to go")."""
        if not self.track_m:
            return None
        me, session = race.me, race.session
        done_this_lap = min(max(lap_dist / self.track_m, 0.0), 1.0)
        if session.max_laps and session.max_laps < 10000:
            return max(session.max_laps - me.laps - done_this_lap, 0.0)
        # the same count as "laps to go" (the LEADER's pace decides a timed race), minus the part
        # of this lap already driven, so fuel and "last lap" never disagree
        to_go = self.laps_left(race)
        if to_go is None:
            return None
        return max(to_go - done_this_lap, 0.0)

    def live_picture(self, race, lap_dist):
        usage = self.live_usage()
        remaining = self.laps_remaining(race, lap_dist)
        if usage is None or remaining is None:
            return None
        litres, energy = usage
        options = []
        if litres > 0.05:
            options.append((race.me.fuel / litres - remaining, "fuel"))
        if energy > 0.0005 and race.me.virtual_energy > 0:
            options.append((race.me.virtual_energy / energy - remaining, "energy"))
        if not options:
            return None
        spare, limit = min(options)
        spare = round(spare, 1)
        return {"spare_laps": spare, "laps_left": round(remaining, 1), "limit": limit,
                "verdict": verdict_of(spare, remaining), "measured": "live, over the last lap driven"}

    def live_fuel(self, race, moment, now):
        """Keeps fuel_now fresh, and speaks up the moment the verdict turns bad, unasked."""
        self.see_burn(race, moment.frame.lap_dist)
        if self.last_live_check is not None and now - self.last_live_check < FUEL_RECHECK_S:
            return []
        self.last_live_check = now
        picture = self.live_picture(race, moment.frame.lap_dist)
        if picture is None:
            return []
        if picture["verdict"] in ("save", "box"):
            self.told_to_save = True
        elif self.told_to_save and picture["spare_laps"] < PUSH_AGAIN_SPARE:
            picture["verdict"] = "saving"
        self.fuel_now = picture
        verdict = picture["verdict"]
        changed = verdict != self.last_live_verdict
        self.last_live_verdict = verdict
        bad = verdict in ("save", "box")
        if not bad or not (changed or (verdict == "box" and self.box_said_lap != moment.lap_count)):
            return []
        if verdict == "box":
            self.box_said_lap = moment.lap_count
        words = fuel_words(picture)
        fuel_call = call("FUEL", words, now, {"spare_laps": abs(picture["spare_laps"]),
                                             "laps_left": picture["laps_left"]}, words, priority=ENGINEER)
        fuel_call.immediate = True           # not held for the talk budget: this ends races
        return [fuel_call]

    def lap_record(self, lap, lap_time, me, now):
        sectors = None
        one, two = self.sector_marks.get(1), self.sector_marks.get(2)
        if self.line_time is not None and one is not None and two is not None and self.line_time < one < two < now:
            sectors = [round(one - self.line_time, 2), round(two - one, 2), round(now - two, 2)]
        used = None
        if len(self.fuel_at_line) >= 2 and self.fuel_at_line[-2] > self.fuel_at_line[-1]:
            used = round(self.fuel_at_line[-2] - self.fuel_at_line[-1], 2)
        return {"lap": lap, "time_s": round(lap_time, 3), "sectors_s": sectors, "fuel_used": used,
                "valid": me.last_lap > 0}

    def fuel_picture(self, race):
        """Laps of fuel (or virtual energy, whichever runs out first) spare at the flag, measured
        at the line. None until two laps are measured."""
        per_lap = self.usage_per_lap(self.fuel_at_line)
        laps_left = self.laps_left(race)
        if per_lap is None or laps_left is None or per_lap <= 0:
            return None
        spare = round(race.me.fuel / per_lap - laps_left, 1)
        limit = "fuel"
        energy_per_lap = self.usage_per_lap(self.energy_at_line)
        if energy_per_lap is not None and energy_per_lap > 0:
            energy_spare = round(race.me.virtual_energy / energy_per_lap - laps_left, 1)
            if energy_spare < spare:
                spare = energy_spare
                limit = "energy"
        return {"spare_laps": spare, "laps_left": laps_left, "limit": limit,
                "verdict": verdict_of(spare, laps_left), "measured": "at the line"}

    def fuel_check(self, race, lap, now):
        if lap < self.last_fuel_check_lap + RECHECK_EVERY_LAPS and self.last_fuel_state is not None:
            return None
        per_lap = self.usage_per_lap(self.fuel_at_line)
        laps_left = self.laps_left(race)
        if per_lap is None or laps_left is None or per_lap <= 0:
            return None
        self.last_fuel_check_lap = lap
        fuel = race.me.fuel
        spare = round(fuel / per_lap - laps_left, 1)
        facts = {"spare_laps": abs(spare), "per_lap_litres": round(per_lap, 2), "laps_left": laps_left}

        # virtual energy, when the car uses it, can be the tighter limit (E8)
        energy_per_lap = self.usage_per_lap(self.energy_at_line)
        if energy_per_lap is not None and energy_per_lap > 0:
            energy_spare = round(race.me.virtual_energy / energy_per_lap - laps_left, 1)
            if energy_spare < spare:
                spare = energy_spare
                facts = {"spare_laps": abs(spare), "laps_left": laps_left}

        if spare >= SPARE_COMFORTABLE:
            state = "fine"
            conclusion = f"Fuel lasts to the flag with {spare} laps spare. No saving. Push."
            template = "Fuel's fine to the flag. Push."
        elif spare >= 0:
            state = "tight"
            conclusion = f"Fuel is tight: only {spare} laps spare. Lift and coast before the big braking zones."
            template = "Fuel's tight. Lift and coast into the big stops."
        else:
            state = "short"
            save = round((-spare * per_lap) / max(laps_left, 1), 2)
            facts["save_per_lap"] = save
            conclusion = f"Fuel is short by {abs(spare)} laps. Save {save} litres a lap: lift and coast every braking zone."
            template = "We're short on fuel. Lift and coast every braking zone."
        if state == self.last_fuel_state:
            return None                      # only speak when the picture changes
        self.last_fuel_state = state
        return call("FUEL", conclusion, now, facts, template)

    def tyre_check(self, me, now):
        calls = []
        for index, zones in enumerate(me.tyre_temps):
            # 0 Kelvin (-273 C) means the game has no reading (pits, garage), 23 Sep
            if not zones or min(zones) < -200:
                continue
            average = round(sum(zones) / len(zones))
            name = TYRE_NAMES[index]
            if average > TYRE_HOT_C:
                self.hot_laps[index] = self.hot_laps.get(index, 0) + 1
            else:
                self.hot_laps[index] = 0
            if self.hot_laps[index] >= 2 and ("hot", index) not in self.tyre_called:
                self.tyre_called.add(("hot", index))
                calls.append(call("TYRE_HOT", f"The {name} is overheating at {average} degrees. Ease the slides on that corner.",
                                  now, {"temp_c": average}, f"{name.capitalize()} is cooking."))
        return calls
