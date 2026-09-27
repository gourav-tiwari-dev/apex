"""Every gap the radio said, against the tape (27 Sep).

The truth is the same-point gap from positions only (not the game's gap fields: they disagreed with
the positions by up to 2 s on 25 Sep night, measured at the timing points):
- a car BEHIND: seconds since he was where it is now (his car frames)
- a car AHEAD: seconds since it was where he is now (its positions, 5 a second, interpolated)
The car is the nearest of his class on the road that side that is also on that side in the race (the
radio's car ahead is the car ahead in the race: 25 Sep 14:41, 722 s, the car he had just passed sat
1 m up the road beside him). A said gap is right within 0.3 s + 15%.
A crossing inside a jump of the scoring is not timed: 23 Sep, 1239-1241 s, a car's position stood
still for 2.2 s then jumped 188 m in 0.2 s, and a straight line across the jump made 0.84 s of a
gap that its speed either side puts at 1.4 s.

Checked: CLOSING_ALARM (behind), CLOSING_ON (ahead), GAP_REPORT (ahead and behind), GAP_GROWING
(behind), PASS_PRAISE's "next one" (ahead).

Usage: gap_truth.py [TAPE ...]   (default: every race tape in tools/tapes.py)"""

import bisect
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

SIDES = {
    "CLOSING_ALARM": [("gap_s", "behind")],
    "CLOSING_ON": [("gap_s", "ahead")],
    "GAP_REPORT": [("gap_ahead_s", "ahead"), ("gap_behind_s", "behind")],
    "GAP_GROWING": [("gap_s", "behind")],
    "PASS_PRAISE": [("gap_ahead_s", "ahead")],
}
LOOK_M = 600.0
BACK_S = 40.0
FASTEST_MS = 120.0  # 432 km/h: a step faster than this is the scoring catching up


def my_frames(tape):
    rows = []
    try:
        with gzip.open(tape, "rt") as f:
            for line in f:
                d = json.loads(line)
                # his lap distance moves 5 times a second, the frames come 60: the first frame of each
                # new distance is when it was there (the repeats would look like 11 m jumps in 1/60 s)
                if d.get("t") is None and (not rows or d["lap_dist"] != rows[-1][1]):
                    rows.append((d["elapsed_time"], d["lap_dist"]))
    except (EOFError, zlib.error, json.JSONDecodeError):
        pass  # a tape cut off mid-write
    return rows


def crossing_time(series, point, before, length):
    """Last time before `before` a (time, lap distance) series went forward past `point`."""
    found = None
    for (t0, d0), (t1, d1) in zip(series, series[1:]):
        if t1 > before:
            break
        if before - t1 > BACK_S:
            continue
        step = (d1 - d0) % length
        if step > length / 2 or step == 0:
            continue  # a jump back, or standing still
        if (point - d0) % length <= step:
            if step / (t1 - t0) > FASTEST_MS:
                found = None  # it crossed somewhere inside a jump: when is unknown
            else:
                found = t0 + ((point - d0) % length) / step * (t1 - t0)
    return found


def main():
    tapes = sys.argv[1:] or RACE_TAPES
    wrong = []
    checked = 0
    for tape in tapes:
        race_snapshots = [s for s in snapshots(tape) if s.me is not None]
        times = [s.sim_time for s in race_snapshots]
        mine = my_frames(tape)
        my_times = [t for t, _ in mine]
        tape_checked = 0
        tape_wrong = 0
        for row in replay(tape, None, "gap_truth"):
            if row[1] not in SIDES or row[2] != "spoken":
                continue
            when = row[0]
            facts = json.loads(row[5] or "{}")
            i = bisect.bisect_right(times, when) - 1
            j = bisect.bisect_right(my_times, when) - 1
            if i < 0 or j < 0:
                continue
            snap = race_snapshots[i]
            length = snap.session.lap_length or 13626.0
            me_d = mine[j][1]
            for key, side in SIDES[row[1]]:
                said = facts.get(key)
                if said is None:
                    continue
                best = None
                for car in snap.opponents:
                    if car.in_pits or car.car_class != snap.me.car_class:
                        continue
                    if (side == "ahead") != (car.place < snap.me.place):
                        continue  # beside him on the road, the other side in the race
                    rel = (car.lap_dist - me_d + length / 2) % length - length / 2
                    if (
                        side == "ahead"
                        and 0 < rel <= LOOK_M
                        and (best is None or rel < best[0])
                    ):
                        best = (rel, car)
                    if (
                        side == "behind"
                        and -LOOK_M <= rel < 0
                        and (best is None or rel > best[0])
                    ):
                        best = (rel, car)
                if best is None:
                    continue
                car = best[1]
                if side == "behind":
                    was_there = crossing_time(mine, car.lap_dist, when, length)
                else:
                    series = [
                        (s.sim_time, o.lap_dist)
                        for s in race_snapshots[max(0, i - 250) : i + 1]
                        for o in s.opponents
                        if o.id == car.id
                    ]
                    was_there = crossing_time(series, me_d, when, length)
                if was_there is None:
                    continue
                truth = when - was_there
                checked += 1
                tape_checked += 1
                if abs(said - truth) > 0.3 + 0.15 * truth:
                    tape_wrong += 1
                    wrong.append(
                        f"{tape} {when:.1f}s {row[1]} {side}: said {said:.2f}, truth {truth:.2f} "
                        f"(P{car.place}, {abs(best[0]):.0f} m)"
                    )
        print(f"{tape}: {tape_checked} gaps checked, {tape_checked - tape_wrong} right")
    print(f"{checked - len(wrong)}/{checked} gaps within 0.3 s + 15% of the tape")
    if not wrong:
        print("PASS")
        return 0
    print("FAIL:")
    for line in wrong:
        print("  " + line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
