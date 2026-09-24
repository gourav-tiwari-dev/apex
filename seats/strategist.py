"""Strategist: will the fuel (or virtual energy) last, are the tyres in their window, is the
weather turning, and the last lap.

Fuel is measured, never assumed: litres used per lap come from my own laps in this session,
so the first call waits until two full laps are done.
"""
import statistics

from radio import Call, STRATEGY, ENGINEER
from race_state import laps_to_go

FIRST_CALL_AFTER_LAPS = 2      # need two measured laps before saying anything about fuel
RECHECK_EVERY_LAPS = 3
SPARE_COMFORTABLE = 0.5        # laps of fuel spare above which it is simply "fine"
# GUESSED upper edge of the GT3 slick window, Celsius, averaged across the tread.
# Confirm on the first v2 tape: the audit shows real temperatures.
TYRE_HOT_C = 105.0
TYRE_NAMES = ["front left", "front right", "rear left", "rear right"]
CALL_TTL_S = 25.0


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
        self.fuel_now = None           # the latest fuel picture, for "how's the fuel?" on the radio
        self.line_time = None          # sim time when he last crossed the line

    def laps_left(self, race):
        lap_time = None
        if self.lap_times:
            lap_time = statistics.median(self.lap_times[-3:])
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

        if moment.lap_wrapped and moment.lap_count >= 1:
            self.fuel_at_line.append(me.fuel)
            self.energy_at_line.append(me.virtual_energy)
            lap_time = measured_lap(me.last_lap, self.line_time, now)
            if lap_time is not None:
                self.lap_times.append(lap_time)
            self.line_time = now
            self.fuel_now = self.fuel_picture(race)
            fuel_call = self.fuel_check(race, moment.lap_count, now)
            if fuel_call is not None:
                calls.append(fuel_call)
            calls.extend(self.tyre_check(me, now))
            # laps to go is exact only at the line: mid-lap it would say "last lap" a lap early.
            # A last lap that starts mid-lap (the leader's flag) is the race engineer's call.
            laps_left = self.laps_left(race)
            if laps_left is not None and laps_left <= 1 and not self.last_lap_called:
                self.last_lap_called = True
                calls.append(call("LAST_LAP", "Last lap. Bring it home.", now, {}, "Last lap. Bring it home.",
                                  priority=ENGINEER))

        if moment.new_race:
            if race.session.raining >= 0.1 and not self.rain_called:
                self.rain_called = True
                calls.append(call("RAIN", f"Rain is starting, severity {race.session.raining}. Grip will drop.",
                                  now, {"rain": race.session.raining}, "Rain's coming. Grip's going away."))
        return calls

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
        return {"spare_laps": spare, "laps_left": laps_left, "limit": limit}

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
