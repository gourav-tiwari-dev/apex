"""The field study: what the whole grid on his tapes says, measured before anything is modelled.
Usage: field_study.py [laps|pace|battles|catches|end] ...   (default: all)

Every car on the tape counts, not just him: 19-24 GT3s a race, four races."""

import bisect
import collections
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tapes import snapshots, RACE_TAPES


class Road:
    """Every car's (distance raced, time), unwrapped by its own lap-distance wraps."""

    def __init__(self):
        self.lap_length = 0.0
        self.trails = collections.defaultdict(
            lambda: ([], [])
        )  # car key -> (distances, times)
        self.wraps = {}
        self.last_dist = {}
        self.info = {}  # car key -> latest Opponent/Me
        self.pits = collections.defaultdict(list)  # car key -> times in the pits

    def see(self, snap):
        cars = [("me", snap.me, None)] + [(o.id, o, o.lap_dist) for o in snap.opponents]
        for _, car, dist in cars:
            if dist is not None and dist > self.lap_length:
                self.lap_length = dist
        if self.lap_length < 1000:
            return
        for key, car, dist in cars:
            if key == "me":
                continue  # my lap distance is in the frames, not scoring
            self.info[key] = car
            if car.in_pits:
                self.pits[key].append(snap.sim_time)
            # the game's own lap count, repaired where it and the lap distance disagree for a
            # moment at the line (one ticks before the other)
            d = car.laps * self.lap_length + dist
            ds, ts = self.trails[key]
            if ds:
                if d < ds[-1] - self.lap_length / 2:
                    d += self.lap_length
                elif d > ds[-1] + self.lap_length / 2:
                    d -= self.lap_length
            if ds and d <= ds[-1]:
                if d < ds[-1] - 500:  # a reset (teleport to the pits): start again
                    ds.clear()
                    ts.clear()
                else:
                    continue
            ds.append(d)
            ts.append(snap.sim_time)

    def time_at(self, key, d):
        ds, ts = self.trails[key]
        if not ds or d < ds[0] or d > ds[-1]:
            return None
        i = bisect.bisect_left(ds, d)
        if ds[i] == d or i == 0:
            return ts[i]
        return ts[i - 1] + (ts[i] - ts[i - 1]) * (d - ds[i - 1]) / (ds[i] - ds[i - 1])


def load(tape):
    road = Road()
    posted = collections.defaultdict(dict)  # car key -> {laps done: posted last lap}
    for snap in snapshots(tape):
        road.see(snap)
        for o in snap.opponents:
            if o.last_lap > 0:
                posted[o.id][o.laps] = o.last_lap
    return road, posted


def road_laps(road, key):
    """Durations between the car's consecutive line crossings on the road trail."""
    ds, ts = road.trails[key]
    L = road.lap_length
    if len(ds) < 2:
        return []
    k = math.ceil(ds[0] / L)
    crossings = []
    while k * L <= ds[-1]:
        crossings.append(road.time_at(key, k * L))
        k += 1
    return [b - a for a, b in zip(crossings, crossings[1:])]


def study_laps():
    """Every lap measured on the road trail against the nearest lap the game posted for that car
    (the game's lap numbering at the start is its own business; the durations must match)."""
    errors = []
    for tape in RACE_TAPES:
        road, posted = load(tape)
        for key, laps in posted.items():
            real = [t for done, t in laps.items() if done >= 2]
            for lap in road_laps(road, key):
                if real and 200 < lap < 400:
                    errors.append(min(abs(lap - p) for p in real))
    errors.sort()
    print(
        f"laps from the road vs posted: {len(errors)} laps, median error {statistics.median(errors):.3f} s, "
        f"90% under {errors[int(len(errors) * 0.9)]:.3f} s, worst {errors[-1]:.2f} s"
    )


def replay(tape, every=None):
    """Replays a tape into a RaceModel; calls every(model, snap) after each snapshot."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from race_model import RaceModel

    length = max(
        max([o.lap_dist for o in snap.opponents] + [0.0]) for snap in snapshots(tape)
    )
    model = RaceModel(lap_length=length)
    for snap in snapshots(tape):
        model.see_race(snap, snap.sim_time)
        if every is not None:
            every(model, snap)
    return model


def study_pace():
    """D2: the model's pace for a car, taken as it crosses the line, against that car's NEXT lap.
    Baseline: its last lap (what Apex used). Clean laps only (within 5% of its median, no pit)."""
    model_err, last_err, available = [], [], [0, 0]
    for tape in RACE_TAPES:
        crossings = collections.defaultdict(list)  # key -> [(time, pace then)]
        seen = {}

        def every(model, snap):
            L = model.lap_length
            if not L:
                return
            for key, car in model.cars.items():
                if key == "me" or car.distance is None:
                    continue
                lap_no = int(car.distance // L)
                if seen.get(key) is not None and lap_no > seen[key]:
                    crossings[key].append(
                        (
                            car.trail.time_at(lap_no * L) or snap.sim_time,
                            model.pace(key),
                        )
                    )
                seen[key] = lap_no

        model = replay(tape, every)
        for key, rows in crossings.items():
            laps = [(rows[i + 1][0] - rows[i][0]) for i in range(len(rows) - 1)]
            if len(laps) < 2:
                continue
            typical = statistics.median(laps)
            for i in range(1, len(laps)):
                nxt, prev, pace = laps[i], laps[i - 1], rows[i][1]
                if abs(nxt - typical) > 0.05 * typical:
                    continue
                available[1] += 1
                if pace is not None:
                    available[0] += 1
                    model_err.append(abs(pace - nxt))
                    if abs(prev - typical) <= 0.05 * typical:
                        last_err.append(abs(prev - nxt))
    print(
        f"D2 pace vs next clean lap: model median error {statistics.median(model_err):.3f} s "
        f"(90% {sorted(model_err)[int(len(model_err) * 0.9)]:.2f}) on {len(model_err)} laps; "
        f"last-lap baseline {statistics.median(last_err):.3f} s on {len(last_err)}; "
        f"model had a pace for {available[0]} of {available[1]} laps"
    )


STUDIES = {"laps": study_laps, "pace": study_pace}

if __name__ == "__main__":
    wanted = sys.argv[1:] or list(STUDIES)
    for name in wanted:
        STUDIES[name]()
