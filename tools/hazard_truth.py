"""Stopped and slow cars ahead, against the tape (27 Sep).

For every "Car stopped ahead" / "Slow car ahead" the radio said: was a car really there, on the road
ahead within 60 m of the distance the call was about, and how fast was it going? Stopped means under
30 km/h on the tape; slow, under 120 (half of racing speed at Le Mans is ~75-150). Also counts calls
for a place already called in the 10 s before (two cars of one crash were two calls, 23 Sep).

Usage: hazard_truth.py [TAPE ...]   (default: every race tape in tools/tapes.py)
Exit code 0 = every call had its car."""

import gzip
import json
import os
import sys
import zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
from replay_orders import replay
from tapes import RACE_TAPES, snapshots

NEAR_M = 60.0
STOPPED_TRUTH_KMH = 30.0
SLOW_TRUTH_KMH = 120.0
SAME_PLACE_S = 10.0


def my_lap_dists(tape):
    """(time, his lap distance) from the car frames."""
    rows = []
    try:
        with gzip.open(tape, "rt") as f:
            for line in f:
                d = json.loads(line)
                if d.get("t") is None:
                    rows.append((d["elapsed_time"], d["lap_dist"]))
    except (EOFError, zlib.error, json.JSONDecodeError):
        pass  # a tape cut off mid-write
    return rows


def lap_dist_at(rows, when):
    found = None
    for t, d in rows:
        if t > when:
            break
        found = d
    return found


def main():
    tapes = sys.argv[1:] or RACE_TAPES
    wrong = []
    total_calls = total_repeats = 0
    for tape in tapes:
        race_snapshots = [s for s in snapshots(tape) if s.me is not None]
        mine = my_lap_dists(tape)
        calls = []
        for row in replay(tape, None, "hazard_truth"):
            if row[1] in ("CAR_STOPPED_AHEAD", "SLOW_CAR_AHEAD") and row[2] == "spoken":
                calls.append((row[0], row[1], json.loads(row[5] or "{}")))
        repeats = 0
        backed = 0
        last_at_place = {}
        for when, kind, facts in calls:
            place = facts.get("corner")
            if (
                place is not None
                and place in last_at_place
                and when - last_at_place[place] < SAME_PLACE_S
            ):
                repeats += 1
            last_at_place[place] = when
            snap = min(race_snapshots, key=lambda s: abs(s.sim_time - when))
            length = snap.session.lap_length or max(
                [o.lap_dist for o in snap.opponents] + [0.0]
            )
            my_lap_dist = lap_dist_at(mine, when)
            limit = STOPPED_TRUTH_KMH if kind == "CAR_STOPPED_AHEAD" else SLOW_TRUTH_KMH
            slowest = None
            for car in snap.opponents:
                if (
                    car.in_pits
                    or car.speed_kmh is None
                    or my_lap_dist is None
                    or not length
                ):
                    continue
                ahead = (car.lap_dist - my_lap_dist) % length
                if abs(ahead - facts.get("metres", 0)) <= NEAR_M:
                    if slowest is None or car.speed_kmh < slowest:
                        slowest = car.speed_kmh
            if slowest is not None and slowest < limit:
                backed += 1
            else:
                shown = (
                    "no car there"
                    if slowest is None
                    else f"slowest there {slowest:.0f} km/h"
                )
                wrong.append(f"{tape} {when:.1f}s {kind} {place}: {shown}")
        total_calls += len(calls)
        total_repeats += repeats
        print(
            f"{tape}: {len(calls)} calls, {backed} with the car there, {repeats} for a place called in the 10 s before"
        )
    print(f"{total_calls} calls, {total_repeats} repeats for the same place")
    if not wrong:
        print("PASS")
        return 0
    print("FAIL:")
    for line in wrong:
        print("  " + line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
