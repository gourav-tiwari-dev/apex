"""Performance engineer: where the time is going, corner by corner, while you drive.

Live cues (v1's detector lines, now radio calls): lock-ups, off-tracks and spins.

Corner feedback, at the line after each lap, at most two corners a lap:
  - the corner where this lap lost the most time against your own best there today
  - the corner where the fastest car of YOUR model (else your class) gains the most on you
Both are said as time and ONE thing to do - brake later, let it roll, get on the power
earlier - never as a speed: Gourav drives by feel and never looks at the speedo (24 Sep 2026).

Earned praise, at most twice a race and only for something measured (Gourav's July
"micro-win" idea): a new personal best lap.
"""

import statistics
from dataclasses import dataclass

from radio import Call, ENGINEER, PERFORMANCE, MEMORY
from track_map import corner_at
from race_state import identity
from seats.spotter import CAR_LENGTH_M
from balance import BalanceMeter, FIX

# the kinds said on the radio. HARD_BRAKING, CORNER_ENTRY and THROTTLE_LIFT are recorded, never
# said: v1's "coasting" is any throttle under 50% with no brake, which is correct part-throttle
# driving through the Porsche Curves, and on 23 Sep the model turned its "no braking" into
# "No fucking braking at Arnage" - an instruction not to brake into a hairpin.
SPOKEN_KINDS = {"SPIN", "OFF_TRACK", "LOCKUP", "SLIDE_CAUGHT"}
# 25 Sep, 58-car race: the spin call, then "Wide at Indianapolis" twice for the same crash; 25 Sep
# night: hit at the Porsche Curves, then "Wide at Porsche Curves. Reset." as if it were his error
WIDE_AFTER_INCIDENT_S = 10.0
# and 25 s after that crash, parked at 1 km/h, the car crept onto the grass: "Wide at Indianapolis".
# Running wide happens at corner speed (Le Mans' slowest corners are ~65 km/h), not at a crawl
WIDE_BELOW_KMH_IS_NOT_WIDE = 30.0
SPIN_TTL_S = 10.0
SLIDE_TTL_S = 4.0  # said while he still feels it, or not at all
CATCH_HOLD_S = (
    3.0  # a caught slide is praised once it has held this long without a spin
)
INCIDENTS = {"SPIN", "OFF_TRACK", "LOCKUP"}
STALE_AFTER_S = 6.0  # v1's STALE_THRESHOLD: advice about a corner 6 s ago is useless

LAPS_FOR_A_REFERENCE = 2  # one lap of another car is not a reference
MAX_PRAISE_PER_SESSION = 2
CORNER_CALL_TTL_S = 15.0
QUALIFYING = range(5, 9)

# what is worth a call. A corner's time moves a tenth lap to lap on its own, so against his
# own best only a bigger drop counts; against another car a tenth a lap is real time.
OWN_BEST_LOSS_S = 0.2
RIVAL_LOSS_S = 0.1
MAX_CORNER_CALLS_PER_LAP = (
    2  # his rule (24 Sep): no more than two corners to work on a lap
)
# what counts as a real difference in HOW the corner is driven
BRAKE_DIFF_MINE_M = 8.0  # my brake point is measured at 60 Hz: good to a metre or two
BRAKE_DIFF_THEIRS_M = 15.0  # theirs at 5 Hz: one sample every 14 m at 250 km/h
ROLL_DIFF_KMH = 3.0  # slowest-point speed; 5 Hz minimums are good to a km/h or two
POWER_DIFF_M = 10.0
# a brake point 129 m apart (Indianapolis, 23 Sep) is not the same braking: one car tapped the
# brake for the kink before it. Past this, the two brake points are not compared at all.
MAX_BRAKE_DIFF_M = 40.0
# the same for the power-on point: in the 1.4 km Porsche Curves window "back on the power
# after the slowest point" can land in a different curve, and the agent said "51 car lengths"
MAX_POWER_DIFF_M = 40.0
BRAKE_ON = 0.4  # same thresholds as my own corner stats
THROTTLE_ON = 0.5
MAX_SAMPLE_GAP_M = 200.0  # two snapshots further apart than this cannot be interpolated


def car_lengths(metres):
    return max(1, round(metres / CAR_LENGTH_M))


def tenths_words(seconds):
    tenths = round(seconds * 10)
    if tenths <= 1:
        return "a tenth"
    if tenths >= 10:
        return f"{round(seconds, 1)} seconds"
    return f"{tenths} tenths"


def call_from_event(event, event_id):
    if event.kind not in SPOKEN_KINDS:
        return None
    if event.kind in INCIDENTS:
        priority = ENGINEER
    else:
        priority = PERFORMANCE
    # the event keeps v1's wording for the log; the radio gets it without the speed
    said = {
        "LOCKUP": f"Locked the fronts into {event.corner}.",
        "OFF_TRACK": f"Wide at {event.corner}.",
        "SPIN": "Spun. Wait for the traffic, then rejoin. Cars coming.",
        "SLIDE_CAUGHT": "Big moment. Caught it. Good hands.",
    }[event.kind]
    if event.kind == "SPIN" and "after contact" in (event.conclusion or ""):
        said = "You got hit and spun. Wait for the traffic, then rejoin. Cars coming."
    if event.kind == "SLIDE_CAUGHT":
        return Call(
            seat="performance",
            kind="SLIDE_CAUGHT",
            sim_time=event.sim_time,
            priority=ENGINEER,
            ttl=SLIDE_TTL_S,
            conclusion=said,
            facts={"corner": event.corner, "degrees": event.magnitude},
            template=said,
            evidence={"event_id": event_id},
            immediate=True,
        )
    if event.kind == "SPIN":
        # live 25 Sep: two spins at Indianapolis expired unspoken while racecraft kept saying
        # "mega defending". A spin is a safety call: said at once, and it lives long enough
        return Call(
            seat="performance",
            kind="SPIN",
            sim_time=event.sim_time,
            priority=priority,
            ttl=SPIN_TTL_S,
            conclusion=said,
            facts={"corner": event.corner},
            template=said,
            evidence={"event_id": event_id},
            immediate=True,
        )
    return Call(
        seat="performance",
        kind=event.kind,
        sim_time=event.sim_time,
        priority=priority,
        ttl=STALE_AFTER_S,
        conclusion=said,
        facts={"corner": event.corner},
        template=said,
        evidence={"event_id": event_id},
    )


@dataclass
class CornerPass:
    """One other car through one corner on one lap, from the 5 Hz race snapshots."""

    who: str  # identity(): steam id, or "name:<driver>"
    driver: str
    car_class: str
    car_model: str | None
    corner: str
    lap: int
    min_speed: float
    time_s: float | None = None
    brake_onset: float | None = None
    throttle_on: float | None = None


def crossing(before, after, value, key):
    """Where between two samples a quantity crossed a value, by straight-line interpolation.
    Samples are dicts with t, dist and the key. Returns (t, dist) or None."""
    if before is None or after is None:
        return None
    span = after["dist"] - before["dist"]
    if span <= 0 or span > MAX_SAMPLE_GAP_M or after["t"] <= before["t"]:
        return None
    if key == "dist":
        share = (value - before["dist"]) / span
    else:
        change = after[key] - before[key]
        if change == 0:
            return None
        share = (value - before[key]) / change
    share = min(1.0, max(0.0, share))
    return (
        before["t"] + share * (after["t"] - before["t"]),
        before["dist"] + share * span,
    )


def median_of(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return statistics.median(values)


class OpponentCorners:
    """Every same-class car through each corner, from the race snapshots: its time through the
    corner, slowest speed, brake point and where it is back on the power. Snapshots come 5
    times a second, so times and points are interpolated between them."""

    def __init__(self):
        self.inside = {}  # opponent id -> {"corner", "lap", "before", "samples"}
        self.last_sample = {}  # opponent id -> its previous snapshot sample
        self.rows = []  # finished CornerPass

    def update(self, race, corners, my_class):
        bounds = {c["name"]: (c["start"], c["end"]) for c in corners}
        for opponent in race.opponents:
            if opponent.speed_kmh is None or opponent.car_class != my_class:
                continue
            sample = {
                "t": race.sim_time,
                "dist": opponent.lap_dist,
                "speed": opponent.speed_kmh,
                "brake": opponent.brake or 0.0,
                "throttle": opponent.throttle or 0.0,
            }
            previous = self.last_sample.get(opponent.id)
            self.last_sample[opponent.id] = sample
            corner = corner_at(corners, opponent.lap_dist)
            if opponent.in_pits:
                corner = None
            current = self.inside.get(opponent.id)
            if current is not None and current["corner"] != corner:
                self.finish(opponent, current, sample, bounds)
                del self.inside[opponent.id]
                current = None
            if corner is None:
                continue
            if current is None:
                self.inside[opponent.id] = {
                    "corner": corner,
                    "lap": opponent.laps,
                    "before": previous,
                    "samples": [sample],
                }
            else:
                current["samples"].append(sample)

    def finish(self, opponent, current, after, bounds):
        samples = current["samples"]
        start, end = bounds.get(current["corner"], (None, None))
        slowest = 0
        for index, sample in enumerate(samples):
            if sample["speed"] < samples[slowest]["speed"]:
                slowest = index

        time_s = None
        if start is not None:
            entered = crossing(current["before"], samples[0], start, "dist")
            left = crossing(samples[-1], after, end, "dist")
            if entered is not None and left is not None:
                time_s = round(left[0] - entered[0], 3)

        brake_onset = None
        chain = [current["before"]] + samples
        for before, sample in zip(chain, chain[1:]):
            if before is not None and before["brake"] < BRAKE_ON <= sample["brake"]:
                point = crossing(before, sample, BRAKE_ON, "brake")
                if point is not None:
                    brake_onset = round(point[1], 1)
                break

        throttle_on = None
        for before, sample in zip(samples[slowest:], samples[slowest + 1 :]):
            if before["throttle"] < THROTTLE_ON <= sample["throttle"]:
                point = crossing(before, sample, THROTTLE_ON, "throttle")
                if point is not None:
                    throttle_on = round(point[1], 1)
                break

        self.rows.append(
            CornerPass(
                who=identity(opponent),
                driver=opponent.driver,
                car_class=opponent.car_class,
                car_model=opponent.car_model,
                corner=current["corner"],
                lap=current["lap"],
                min_speed=round(samples[slowest]["speed"], 1),
                time_s=time_s,
                brake_onset=brake_onset,
                throttle_on=throttle_on,
            )
        )

    def fastest_through(self, corner, my_model=None):
        """How the quickest car through this corner drives it: the median of its laps.
        Cars of my own model first (a Porsche and a BMW take a hairpin differently, 23 Sep),
        and only if none of them has the laps, the quickest of my class. None if nobody does."""
        by_car = {}
        for row in self.rows:
            if row.corner == corner and row.time_s is not None:
                by_car.setdefault(row.who, []).append(row)
        ready = {}
        for who, rows in by_car.items():
            if len(rows) >= LAPS_FOR_A_REFERENCE:
                ready[who] = rows
        same_model = {}
        for who, rows in ready.items():
            if my_model and rows[0].car_model == my_model:
                same_model[who] = rows
        pool = same_model or ready
        fastest = None
        for who, rows in pool.items():
            summary = {
                "driver": rows[0].driver,
                "same_model": bool(same_model),
                "car_model": rows[0].car_model,
                "time_s": median_of(r.time_s for r in rows),
                "min_speed": median_of(r.min_speed for r in rows),
                "brake_onset": median_of(r.brake_onset for r in rows),
                "throttle_on": median_of(r.throttle_on for r in rows),
            }
            if fastest is None or summary["time_s"] < fastest["time_s"]:
                fastest = summary
        return fastest


def what_to_change(mine, theirs, brake_margin_m):
    """The one change in HOW the corner is driven that best explains the time, or None when
    the measurements do not explain it (then nothing is said: advice has to be actionable).
    mine / theirs: dicts with brake_onset, min_speed, throttle_on (any may be None).
    Returns (change, car lengths or None)."""
    brake_diff = None
    if mine.get("brake_onset") is not None and theirs.get("brake_onset") is not None:
        brake_diff = (
            theirs["brake_onset"] - mine["brake_onset"]
        )  # > 0: they brake later
        if abs(brake_diff) > MAX_BRAKE_DIFF_M:
            brake_diff = None
    roll_diff = None
    if mine.get("min_speed") is not None and theirs.get("min_speed") is not None:
        roll_diff = theirs["min_speed"] - mine["min_speed"]  # > 0: they carry more
    power_diff = None
    if mine.get("throttle_on") is not None and theirs.get("throttle_on") is not None:
        power_diff = (
            mine["throttle_on"] - theirs["throttle_on"]
        )  # > 0: they power earlier
        if abs(power_diff) > MAX_POWER_DIFF_M:
            power_diff = None

    # braking later AND slower in the middle: in too deep, the classic overdriving
    if (
        brake_diff is not None
        and roll_diff is not None
        and -brake_diff >= brake_margin_m
        and roll_diff >= ROLL_DIFF_KMH
    ):
        return "too_deep", None
    options = []
    if brake_diff is not None and brake_diff >= brake_margin_m:
        options.append(
            (brake_diff / brake_margin_m, "brake_later", car_lengths(brake_diff))
        )
    if roll_diff is not None and roll_diff >= ROLL_DIFF_KMH:
        options.append((roll_diff / ROLL_DIFF_KMH, "let_it_roll", None))
    if power_diff is not None and power_diff >= POWER_DIFF_M:
        options.append(
            (power_diff / POWER_DIFF_M, "power_earlier", car_lengths(power_diff))
        )
    if not options:
        return None
    options.sort(key=lambda option: option[0], reverse=True)
    return options[0][1], options[0][2]


# the one thing to do, in a driver's words
ADVICE = {
    "brake_later": "Brake later, about {n} car lengths.",
    "let_it_roll": "You over-slow it. Off the brake earlier, let it roll.",
    "power_earlier": "Get on the power earlier, about {n} car lengths.",
    "too_deep": "You go in too deep. Brake a touch earlier, carry the speed.",
}


def advice_text(change, lengths):
    return ADVICE[change].format(n=lengths)


def as_dict(stat):
    return {
        "brake_onset": stat.brake_onset,
        "min_speed": stat.min_speed,
        "throttle_on": stat.throttle_on,
    }


class PerformanceEngineer:
    def __init__(self):
        self.my_speeds = {}  # corner -> every minimum speed I did (racecraft uses it)
        self.my_passes = {}  # corner -> every CornerStat of mine this session
        self.this_lap = []  # CornerStats finished this lap
        self.fastest_said = set()  # corners already compared to the fastest car
        self.best_lap_seen = None
        self.praise_given = 0
        self.opponents = OpponentCorners()
        self.my_model = None
        self.balance = BalanceMeter()  # understeer / oversteer, from his own laps
        self.balance_said = set()  # corners already told about their balance
        self.incident_at = None  # sim time of his last spin or hit
        self.held_catch = None  # (the "caught it" call, when it may be said)

    def saw_hit(self, sim_time):
        """A contact with a car or a wall, told by the race loop (as the spin detector is)."""
        self.incident_at = sim_time

    def call_for_event(self, event, event_id):
        if event.kind == "SPIN":
            self.incident_at = event.sim_time
            # live 27 Sep: "Big moment. Caught it. Good hands." then "Spun." 2 s later. The save
            # waits CATCH_HOLD_S before it is praised; a spin in that time means it did not hold
            self.held_catch = None
        elif event.kind == "OFF_TRACK" and event.speed_kmh < WIDE_BELOW_KMH_IS_NOT_WIDE:
            return None  # parked or crawling after a crash
        elif event.kind == "OFF_TRACK" and self.incident_at is not None:
            if 0 <= event.sim_time - self.incident_at < WIDE_AFTER_INCIDENT_S:
                return None  # the spin or the hit is the news, not "wide"
        call = call_from_event(event, event_id)
        if call is not None and call.kind == "SLIDE_CAUGHT":
            self.held_catch = (call, event.sim_time + CATCH_HOLD_S)
            return None
        return call

    def update(self, moment):
        calls = []
        race = moment.race
        now = moment.now

        if self.held_catch is not None and now >= self.held_catch[1]:
            caught = self.held_catch[0]
            caught.sim_time = now  # said now, while he still feels it
            calls.append(caught)
            self.held_catch = None

        if (
            race is not None
            and race.me is not None
            and moment.new_race
            and moment.corners
        ):
            self.my_model = race.me.car_model
            self.opponents.update(race, moment.corners, race.me.car_class)

        self.balance.update(
            moment.frame, moment.corner if moment.lap_count >= 1 else None
        )
        stat = moment.corner_stat
        if stat is not None and stat.min_speed is not None and stat.lap_count >= 1:
            self.corner_finished(stat)

        if moment.lap_wrapped:
            calls.extend(self.lap_finished(moment, now))
        return calls

    def corner_finished(self, stat):
        self.my_speeds.setdefault(stat.corner, []).append(round(stat.min_speed, 1))
        self.my_passes.setdefault(stat.corner, []).append(stat)
        self.this_lap.append(stat)

    def own_best_call(self, now):
        """The corner of this lap that lost the most against my own best there today."""
        worst = None
        for stat in self.this_lap:
            if stat.time_s is None:
                continue
            others = [
                p
                for p in self.my_passes[stat.corner]
                if p is not stat and p.time_s is not None
            ]
            if not others:
                continue
            best = min(others, key=lambda p: p.time_s)
            loss = round(stat.time_s - best.time_s, 2)
            if loss < OWN_BEST_LOSS_S:
                continue
            change = what_to_change(as_dict(stat), as_dict(best), BRAKE_DIFF_MINE_M)
            if change is None:
                continue
            if worst is None or loss > worst[0]:
                worst = (loss, stat.corner, change)
        if worst is None:
            return None
        loss, corner, (change, lengths) = worst
        facts = {"corner": corner, "time_lost_s": loss}
        if lengths is not None:
            facts["car_lengths"] = lengths
        advice = advice_text(change, lengths)
        return Call(
            seat="performance",
            kind="CORNER_LOSS",
            sim_time=now,
            priority=PERFORMANCE,
            ttl=CORNER_CALL_TTL_S,
            conclusion=f"{corner} this lap was {loss} s slower than your best there today. {advice}",
            facts=facts,
            template=f"{corner}: {tenths_words(loss)} off your best. {advice}",
        )

    def rival_gaps(self):
        """Every corner where the fastest car of my model gains a tenth or more on me and the
        measurements say why: (gap, corner, fastest, change), biggest first."""
        found = []
        for corner, passes in self.my_passes.items():
            timed = [p for p in passes if p.time_s is not None]
            if len(timed) < LAPS_FOR_A_REFERENCE:
                continue
            fastest = self.opponents.fastest_through(corner, self.my_model)
            if fastest is None:
                continue
            mine = {
                "time_s": median_of(p.time_s for p in timed),
                "brake_onset": median_of(p.brake_onset for p in timed),
                "min_speed": median_of(p.min_speed for p in timed),
                "throttle_on": median_of(p.throttle_on for p in timed),
            }
            gap = round(mine["time_s"] - fastest["time_s"], 2)
            if gap < RIVAL_LOSS_S:
                continue
            change = what_to_change(mine, fastest, BRAKE_DIFF_THEIRS_M)
            if change is None:
                continue
            found.append((gap, corner, fastest, change))
        found.sort(key=lambda item: item[0], reverse=True)
        return found

    def focus(self):
        """For "where am I losing time?": the biggest measured gap, or None."""
        gaps = self.rival_gaps()
        if not gaps:
            return None
        gap, corner, fastest, (change, lengths) = gaps[0]
        return {
            "corner": corner,
            "gap_s": gap,
            "driver": fastest["driver"],
            "advice": advice_text(change, lengths),
            "car_lengths": lengths,
        }

    def rival_call(self, now, skip_corners):
        """The corner where the fastest car of my model gains most on me, once per corner."""
        biggest = None
        for item in self.rival_gaps():
            if item[1] in self.fastest_said or item[1] in skip_corners:
                continue
            biggest = item
            break
        if biggest is None:
            return None
        gap, corner, fastest, (change, lengths) = biggest
        self.fastest_said.add(corner)
        driver = fastest["driver"]
        if fastest["same_model"] and fastest["car_model"]:
            who = f"the fastest {fastest['car_model']}"
        else:
            who = "the fastest car in your class"
        facts = {"corner": corner, "driver": driver, "time_gap_s": gap}
        if lengths is not None:
            facts["car_lengths"] = lengths
        advice = advice_text(change, lengths)
        return Call(
            seat="performance",
            kind="FASTEST_CAR",
            sim_time=now,
            priority=PERFORMANCE,
            ttl=CORNER_CALL_TTL_S,
            conclusion=f"{who.capitalize()} is {gap} s quicker than you through {corner}. {advice}",
            facts=facts,
            template=f"{corner}: {who} finds {tenths_words(gap)} there. {advice}",
        )

    def balance_call(self, now, skip):
        """The corner whose balance is most off his normal, once per corner per session
        (24 Sep: "add the understeer and oversteer discovery")."""
        worst = None
        for corner in self.balance.passes:
            if corner in self.balance_said or corner in skip:
                continue
            problems = self.balance.problems(corner)
            if problems and (worst is None or problems[0][2] > worst[1][2]):
                worst = (corner, problems[0])
        if worst is None:
            return None
        corner, (phase, kind, how_far) = worst
        self.balance_said.add(corner)
        fix = FIX[(phase, kind)]
        return Call(
            seat="performance",
            kind="BALANCE",
            sim_time=now,
            priority=PERFORMANCE,
            ttl=CORNER_CALL_TTL_S,
            conclusion=f"{corner}: {fix} Measured every lap: the car rotates {round(how_far * 100)} percent off his normal there.",
            facts={"corner": corner, "phase": phase, "problem": kind},
            template=f"{corner}: {fix}",
        )

    def lap_finished(self, moment, now):
        calls = []
        own = self.own_best_call(now)
        if own is not None:
            calls.append(own)
        said = set()
        if own is not None:
            said.add(own.facts["corner"])
        balance = self.balance_call(now, said)
        if balance is not None:
            calls.append(balance)
            said.add(balance.facts["corner"])
        rival = self.rival_call(now, said)
        if rival is not None:
            calls.append(rival)
        calls = calls[:MAX_CORNER_CALLS_PER_LAP]
        self.this_lap = []

        race = moment.race
        if race is not None and race.me is not None:
            calls.extend(self.lap_time_calls(race.me, moment, now))
        return calls

    def lap_time_calls(self, me, moment, now):
        calls = []
        best = me.best_lap if me.best_lap > 0 else None
        if (
            best is not None
            and self.best_lap_seen is not None
            and best < self.best_lap_seen
            and self.praise_given < MAX_PRAISE_PER_SESSION
            and moment.session_type not in QUALIFYING
        ):
            gain = round(self.best_lap_seen - best, 2)
            self.praise_given += 1
            calls.append(
                Call(
                    seat="performance",
                    kind="PRAISE",
                    sim_time=now,
                    priority=MEMORY,
                    ttl=CORNER_CALL_TTL_S,
                    conclusion=f"New personal best, {gain} seconds quicker. Tell him that is his lap, earned.",
                    facts={"gain_s": gain},
                    template="New best lap. That's your lap.",
                )
            )
        if best is not None:
            self.best_lap_seen = best

        # qualifying laps (E15) moved to seats/qualifying.py, 25 Sep: this one said "0.0 off your
        # best" on the best lap itself and never said where the lap put him
        return calls
