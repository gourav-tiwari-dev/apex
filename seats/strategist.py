"""Strategist: will the fuel (or virtual energy) last, are the tyres in their window, is the
weather turning, and the last lap.

Fuel is measured, never assumed: litres used per lap come from my own laps in this session,
so the first call waits until two full laps are done.
"""

import statistics

from radio import Call, STRATEGY, ENGINEER
from race_state import laps_to_go, leader_margin
from game.constants import GREEN_FLAG, RACE_SESSIONS
from game.constants import WHEEL_NAMES
from race_state import NO_TYRE_READING_C

FIRST_CALL_AFTER_LAPS = 2  # need two measured laps before saying anything about fuel
RECHECK_EVERY_LAPS = 3
SPARE_COMFORTABLE = 0.5  # laps of fuel spare above which it is simply "fine"
# GUESSED upper edge of the GT3 slick window, Celsius, averaged across the tread.
# Confirm on the first v2 tape: the audit shows real temperatures.
HOT_TYRE_C = 105  # a tyre over this at two lines in a row is cooking
CALL_TTL_S = 25.0

# Live 25 Sep: Apex was restarted mid-race, had no laps at the line, and so no fuel picture.
# He had 0.6 laps of energy for 1.7 laps of race; the radio read out litres, the coach said
# "no need to pit", nothing warned him. Usage is now measured CONTINUOUSLY, per metre driven,
# so a "box" can come about 1.5 km after any start, restart or refuel.
#
# Replay of 25 Sep night (26 Sep): the burn per metre changes around a lap (the Porsche Curves and
# the Mulsanne burn more than the chicanes), so 1.9 km stretched to a whole lap said 8.2 litres a
# lap against 7.75 on the tape, and the radio said "tight, 0.0 laps spare" against 0.33. A number
# now comes from a whole lap; part of a lap may only say "box", and only if it is box even at 10%
# less burn. And his spin at the Porsche Curves had wiped the lap's burn as if it were a reset.
LIVE_MIN_M = 1500.0  # track driven before part of a lap can say "box"
LIVE_KEEP_LAPS = 1.2  # usage over the last lap or so: the current pace of burn
PART_LAP_ERROR = 0.10  # part of a lap was 6-7% off on 25 Sep night; 10% to be sure
BACKWARDS_RESET_M = (
    500.0  # GUESSED: a spin rolls back metres; a jump back this far is a reset
)
# GUESSED: lift and coast plus short-shifting saves about 8% of usage (tune from his laps)
LIFT_AND_COAST_SAVES = 0.08
FUEL_RECHECK_S = 15.0
CLOSE_CALL_S = 6.0  # the leader this close to beating the clock: "last lap" is not sure
VERDICT_WORDS = {
    "fine": "{what}'s fine to the flag, {spare} laps spare. Push.",
    "tight": "{what}'s tight, {spare} laps spare. Lift and coast into the big stops.",
    "save": "{what}'s short by {short} laps. Lift and coast every braking zone and short-shift, or you won't make it.",
    "box": "Box this lap for fuel. {what} won't make the flag, short by {short} laps.",
    # after a save call, the saving itself makes the numbers "fine": say so, don't say push
    # (live 25 Sep: "short, lift and coast" -> he saved -> "fine, push" 47 s after "box this lap")
    "saving": "Saving's working, {spare} laps spare. Keep lifting into the big stops.",
}
PUSH_AGAIN_SPARE = 1.0  # after saving, "push" again only with a full lap spare
# how bad each verdict is: the live picture speaks on its first verdict and whenever it gets worse
# (live 25 Sep: 0.3 laps spare the whole race and the radio never said "tight")
VERDICT_RANK = {"fine": 0, "saving": 1, "tight": 1, "save": 2, "box": 3}


# ...but in the last laps less is plenty. Live 27 Sep: "Energy's tight, 0.4 laps spare. Lift and
# coast" with 2.4 and then 1.3 laps to go (17% and 31% margins) - 1.47 spare at the flag, his mark.
# On the 4 tapes that reach the flag the spare is measured to within 0.06 laps. The right "tight"
# calls there had up to 10.4% of the laps left spare, the wrong ones 17-31%: 13% sits between
FINE_SHARE_OF_LAPS_LEFT = 0.13
FINE_AT_LEAST = 0.1  # laps: never "fine" on less, however few laps are left


def fine_margin(laps_left):
    """Laps spare that make the fuel simply "fine": SPARE_COMFORTABLE, less in the last laps."""
    share = FINE_SHARE_OF_LAPS_LEFT * max(laps_left, 0.0)
    return min(SPARE_COMFORTABLE, max(FINE_AT_LEAST, share))


def verdict_of(spare, laps_left):
    if spare >= fine_margin(laps_left):
        return "fine"
    if spare >= 0:
        return "tight"
    if -spare <= LIFT_AND_COAST_SAVES * max(laps_left, 0.5):
        return "save"
    return "box"


def picture_of(me, litres, energy, remaining):
    """The fuel picture from the usage a lap: whichever of fuel or virtual energy runs out first."""
    options = []
    if litres > 0.05:
        options.append((me.fuel / litres - remaining, "fuel"))
    if energy > 0.0005 and me.virtual_energy > 0:
        options.append((me.virtual_energy / energy - remaining, "energy"))
    if not options:
        return None
    spare, limit = min(options)
    spare = round(spare, 1) + 0.0  # + 0.0: never "-0.0 laps spare"
    return {
        "spare_laps": spare,
        "laps_left": round(remaining, 1),
        "limit": limit,
        "verdict": verdict_of(spare, remaining),
        "measured": "live, over the last lap driven",
    }


def fuel_words(picture):
    what = "Fuel" if picture["limit"] == "fuel" else "Energy"
    return VERDICT_WORDS[picture["verdict"]].format(
        what=what, spare=picture["spare_laps"], short=abs(picture["spare_laps"])
    )


def call(kind, conclusion, now, facts, template, priority=STRATEGY):
    return Call(
        seat="strategist",
        kind=kind,
        sim_time=now,
        priority=priority,
        ttl=CALL_TTL_S,
        conclusion=conclusion,
        facts=facts,
        template=template,
    )


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
        self.model = None  # the race model: the leader's rolling lap
        self.fuel_at_line = []  # fuel in the tank each time I crossed the line
        self.energy_at_line = []
        self.lap_times = []
        self.last_fuel_state = None
        self.last_fuel_check_lap = 0
        self.hot_laps = {}  # tyre index -> consecutive laps above the window
        self.tyre_called = set()
        self.rain_called = False
        self.last_lap_called = False
        self.leader_laps = None  # the leader's laps done, to see it cross the line
        self.fuel_now = (
            None  # the latest fuel picture, for "how's the fuel?" on the radio
        )
        self.line_time = None  # sim time when he last crossed the line
        self.track_m = None  # lap length, the longest lap distance any car showed
        self.burn = []  # (distance driven, fuel, energy) since the last refuel
        self.wraps = 0
        self.last_lap_dist = None
        self.was_green = None  # None: Apex joined a race already running
        self.first_lap_until = (
            None  # distance where the first lap after a green flag ends
        )
        self.last_live_check = None
        self.last_live_verdict = None
        self.box_said_lap = None
        self.told_to_save = (
            False  # once told to save, "fine" means the saving is working
        )
        # v3 step 5b: every lap for "how were my last laps / my sectors" on the radio. The laps
        # table in apex.db has no times for live sessions (all None, checked 25 Sep), so the
        # sectors are timed here on Apex's own clock, like measured_lap
        self.lap_records = []  # {"lap", "time_s", "sectors_s", "fuel_used", "valid"}
        self.sector_marks = {}  # 1 / 2 -> sim time sector 1 / 2 ended this lap
        self.last_sector = None

    def laps_left(self, race):
        lap_time = None
        if self.lap_times:
            lap_time = statistics.median(self.lap_times[-3:])
        elif race.me.laps >= 2 and (race.me.last_lap > 0 or race.me.best_lap > 0):
            # Apex started mid-race (25 Sep): no line crossings timed yet, the game's own lap
            lap_time = race.me.last_lap if race.me.last_lap > 0 else race.me.best_lap
        return laps_to_go(race, lap_time, self.model)

    def usage_per_lap(self, readings):
        used = []
        for before, after in zip(readings, readings[1:]):
            if before > after:  # a refuel makes the difference negative: skip it
                used.append(before - after)
        if len(used) < FIRST_CALL_AFTER_LAPS:
            return None
        return statistics.median(used[-3:])

    def update(self, moment):
        race = moment.race
        if race is None or race.me is None:
            return []
        self.model = moment.model
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

        if moment.lap_wrapped and me.laps == 0:
            self.line_time = now
        # a line crossing counts once the game has a race lap done: at lights out he crosses the
        # line with none, and Apex timed the formation as a 2:23 "lap" (live 25 Sep)
        if moment.lap_wrapped and moment.lap_count >= 1 and me.laps >= 1:
            self.fuel_at_line.append(me.fuel)
            self.energy_at_line.append(me.virtual_energy)
            lap_time = measured_lap(me.last_lap, self.line_time, now)
            if (
                lap_time is not None and me.laps >= 2
            ):  # lap 1 is a standing start: not pace
                self.lap_times.append(lap_time)
                self.lap_records.append(
                    self.lap_record(moment.lap_count, lap_time, me, now)
                )
            self.sector_marks = {}
            self.line_time = now
            # the live picture (burn per metre) wins; the line picture fills in before it exists
            self.fuel_now = self.live_picture(race, 0.0) or self.fuel_picture(race)
            # the line check only while the live picture has nothing yet (they said "tight" twice)
            live = self.live_picture(race, 0.0) is not None
            fuel_call = (
                self.fuel_check(race, moment.lap_count, now)
                if racing and not live
                else None
            )
            if fuel_call is not None:
                calls.append(fuel_call)
            calls.extend(self.tyre_check(me, now))
            # laps to go is exact only at the line: mid-lap it would say "last lap" a lap early.
            # A last lap that starts mid-lap (the leader's flag) is the race engineer's call.
            laps_left = self.laps_left(race)
            if (
                racing
                and laps_left is not None
                and laps_left <= 1
                and not self.last_lap_called
            ):
                self.last_lap_called = True
                margin = leader_margin(race, self.model)
                words = "Last lap. Bring it home."
                if margin is not None and 0 <= margin < CLOSE_CALL_S:
                    words = (
                        "Last lap, unless the leader beats the clock. I'll tell you."
                    )
                calls.append(
                    call(
                        "LAST_LAP",
                        words,
                        now,
                        {"leader_margin_s": margin},
                        words,
                        priority=ENGINEER,
                    )
                )

        if moment.new_race:
            if race.session.raining >= 0.1 and not self.rain_called:
                self.rain_called = True
                calls.append(
                    call(
                        "RAIN",
                        f"Rain is starting, severity {race.session.raining}. Grip will drop.",
                        now,
                        {"rain": race.session.raining},
                        "Rain's coming. Grip's going away.",
                    )
                )
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
        self.last_lap_called = False  # the real last lap is called at the line
        words = "One more lap after this one. The leader beat the clock."
        extra = call(
            "EXTRA_LAP",
            words,
            now,
            {"time_left_s": round(race.session.time_remaining)},
            words,
            priority=ENGINEER,
        )
        extra.immediate = True
        return [extra]

    # ---- fuel, measured all the time ------------------------------------------------------------
    def see_burn(self, race, lap_dist):
        length = getattr(race.session, "lap_length", None)
        if length and length > 1000:
            self.track_m = length  # the session's lap, not the farthest car so far
        for car in race.opponents:
            if not (length and length > 1000) and (
                self.track_m is None or car.lap_dist > self.track_m
            ):
                self.track_m = car.lap_dist
        if self.track_m is None or self.track_m < 1000:
            return
        if (
            self.last_lap_dist is not None
            and lap_dist < self.last_lap_dist - self.track_m / 2
        ):
            self.wraps += 1
        elif (
            self.last_lap_dist is not None
            and lap_dist > self.last_lap_dist + self.track_m / 2
        ):
            self.wraps -= 1  # rolled back over the line in a spin
        self.last_lap_dist = lap_dist
        me = race.me
        if race.session.game_phase != GREEN_FLAG:
            self.burn = []  # the formation lap burns at half pace: not race burn
            self.was_green = False
            return
        if me.in_pits:
            self.burn = []  # pit lane and refuelling: start again after it
            return
        distance = self.wraps * self.track_m + lap_dist
        if self.was_green is False:
            # the green flag: the start or a restart, and the lap after it is not a fuel lap
            # (25 Sep night, in the traffic: 7.53 litres against 7.74-7.78 for the clean laps)
            self.first_lap_until = distance + self.track_m
        self.was_green = True
        if self.burn and me.fuel > self.burn[-1][1] + 0.5:
            self.burn = []  # refuelled
        elif self.burn and distance < self.burn[-1][0]:
            if self.burn[-1][0] - distance > BACKWARDS_RESET_M:
                self.burn = []  # a jump back, not a spin: start again
            else:
                return  # spun and rolled back: skip it, keep the lap's burn
        self.burn.append((distance, me.fuel, me.virtual_energy))
        while self.burn and distance - self.burn[0][0] > LIVE_KEEP_LAPS * self.track_m:
            self.burn.pop(0)

    def live_usage(self):
        """(litres a lap, energy a lap, whole lap?) or None. Over exactly the last lap once a whole
        lap is in; before that over what there is, which is only good enough to say "box"."""
        if len(self.burn) < 2 or not self.track_m:
            return None
        end_distance, end_fuel, end_energy = self.burn[-1]
        start = self.burn[0]
        whole = False
        for point in self.burn:
            if end_distance - point[0] >= self.track_m:
                start = point  # the latest point still a whole lap back
                whole = True
            else:
                break
        if (
            whole
            and self.first_lap_until is not None
            and start[0] < self.first_lap_until
        ):
            whole = False  # the lap is the start's: only good enough for "box"
            start = self.burn[0]
        start_distance, start_fuel, start_energy = start
        driven = end_distance - start_distance
        if driven < LIVE_MIN_M:
            return None
        litres = (start_fuel - end_fuel) / driven * self.track_m
        energy = (start_energy - end_energy) / driven * self.track_m
        return litres, energy, whole

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
        litres, energy, whole = usage
        picture = picture_of(race.me, litres, energy, remaining)
        if picture is None or whole:
            return picture
        # part of a lap: box, and only if it is box even at 10% less burn
        kinder = picture_of(
            race.me,
            litres * (1 - PART_LAP_ERROR),
            energy * (1 - PART_LAP_ERROR),
            remaining,
        )
        if kinder is None or kinder["verdict"] != "box":
            return None
        picture["measured"] = "live, over part of a lap"
        return picture

    def live_fuel(self, race, moment, now):
        """Keeps fuel_now fresh, and speaks up the moment the verdict turns bad, unasked."""
        self.see_burn(race, moment.frame.lap_dist)
        if (
            self.last_live_check is not None
            and now - self.last_live_check < FUEL_RECHECK_S
        ):
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
        before = self.last_live_verdict
        self.last_live_verdict = verdict
        first = before is None
        worse = before is not None and VERDICT_RANK[verdict] > VERDICT_RANK[before]
        box_again = verdict == "box" and self.box_said_lap != moment.lap_count
        if not (first or worse or box_again):
            return []
        bad = verdict in ("save", "box")
        if verdict == "box":
            self.box_said_lap = moment.lap_count
        words = fuel_words(picture)
        fuel_call = call(
            "FUEL",
            words,
            now,
            {
                "spare_laps": abs(picture["spare_laps"]),
                "laps_left": picture["laps_left"],
                "verdict": verdict,
            },
            words,
            priority=ENGINEER,
        )
        fuel_call.immediate = (
            bad  # save / box are not held for the talk budget: they end races
        )
        return [fuel_call]

    def lap_record(self, lap, lap_time, me, now):
        sectors = None
        one, two = self.sector_marks.get(1), self.sector_marks.get(2)
        if (
            self.line_time is not None
            and one is not None
            and two is not None
            and self.line_time < one < two < now
        ):
            sectors = [
                round(one - self.line_time, 2),
                round(two - one, 2),
                round(now - two, 2),
            ]
        used = None
        if (
            len(self.fuel_at_line) >= 2
            and self.fuel_at_line[-2] > self.fuel_at_line[-1]
        ):
            used = round(self.fuel_at_line[-2] - self.fuel_at_line[-1], 2)
        return {
            "lap": lap,
            "time_s": round(lap_time, 3),
            "sectors_s": sectors,
            "fuel_used": used,
            "valid": me.last_lap > 0,
        }

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
        return {
            "spare_laps": spare,
            "laps_left": laps_left,
            "limit": limit,
            "verdict": verdict_of(spare, laps_left),
            "measured": "at the line",
        }

    def fuel_check(self, race, lap, now):
        if (
            lap < self.last_fuel_check_lap + RECHECK_EVERY_LAPS
            and self.last_fuel_state is not None
        ):
            return None
        per_lap = self.usage_per_lap(self.fuel_at_line)
        laps_left = self.laps_left(race)
        if per_lap is None or laps_left is None or per_lap <= 0:
            return None
        self.last_fuel_check_lap = lap
        fuel = race.me.fuel
        spare = round(fuel / per_lap - laps_left, 1)
        facts = {
            "spare_laps": abs(spare),
            "per_lap_litres": round(per_lap, 2),
            "laps_left": laps_left,
        }

        # virtual energy, when the car uses it, can be the tighter limit (E8)
        energy_per_lap = self.usage_per_lap(self.energy_at_line)
        if energy_per_lap is not None and energy_per_lap > 0:
            energy_spare = round(race.me.virtual_energy / energy_per_lap - laps_left, 1)
            if energy_spare < spare:
                spare = energy_spare
                facts = {"spare_laps": abs(spare), "laps_left": laps_left}

        if spare >= fine_margin(laps_left):
            state = "fine"
            conclusion = (
                f"Fuel lasts to the flag with {spare} laps spare. No saving. Push."
            )
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
            return None  # only speak when the picture changes
        self.last_fuel_state = state
        facts["verdict"] = state  # his "we push" order lets "short" through (orders.py)
        return call("FUEL", conclusion, now, facts, template)

    def tyre_check(self, me, now):
        calls = []
        for index, zones in enumerate(me.tyre_temps):
            if not zones or min(zones) <= NO_TYRE_READING_C:
                continue
            average = round(sum(zones) / len(zones))
            name = WHEEL_NAMES[index]
            if average > HOT_TYRE_C:
                self.hot_laps[index] = self.hot_laps.get(index, 0) + 1
            else:
                self.hot_laps[index] = 0
            if self.hot_laps[index] >= 2 and ("hot", index) not in self.tyre_called:
                self.tyre_called.add(("hot", index))
                calls.append(
                    call(
                        "TYRE_HOT",
                        f"The {name} is overheating at {average} degrees. Ease the slides on that corner.",
                        now,
                        {"temp_c": average},
                        f"{name.capitalize()} is cooking.",
                    )
                )
        return calls
