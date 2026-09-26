"""Faster classes against the tape (27 Sep): every time a faster-class car went from behind him to
ahead of him on the road, was he told in the 15 s before? And was every "Hypercar / LMP2 behind"
call followed by a real pass? His mark on the 58-car race (25 Sep): "LMP do not detected".

Usage: class_truth.py [TAPE ...]   (default: every race tape with another class in it)
Passes while he is below 60 km/h (stopped after a crash, in the pits) do not count."""
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
from tapes import RACE_TAPES
from race_state import race_snapshot_from_dict
from seats.track_awareness import class_rank

WARNED_WITHIN_S = 15.0
PASS_WINDOW_M = 300.0        # a pass is a car crossing from behind to ahead within this of him
RACING_KMH = 60.0
CLASS_CALLS = ("FASTER_CLASS_BEHIND", "FASTER_FIGHT_BEHIND")


def passes_on(tape):
    """(sim time, driver, class) of every faster-class car that went by him on the road."""
    passes = []
    where = {}                     # car id -> its place relative to him last snapshot, metres
    my_lap_dist = None
    my_speed = 0.0
    try:
        with gzip.open(tape, "rt") as f:
            for line in f:
                d = json.loads(line)
                kind = d.get("t")
                if kind is None:
                    my_lap_dist = d.get("lap_dist")
                    my_speed = d.get("speed_kmh") or 0.0
                    continue
                if kind != "race" or my_lap_dist is None:
                    continue
                race = race_snapshot_from_dict(d)
                if race.me is None or race.session.game_phase != 5:
                    continue
                length = race.session.lap_length or max([o.lap_dist for o in race.opponents] + [0.0])
                if not length or length < 1000:
                    continue
                mine = class_rank(race.me.car_class)
                for car in race.opponents:
                    if car.in_pits or class_rank(car.car_class) <= mine:
                        continue
                    ahead = (car.lap_dist - my_lap_dist + length / 2) % length - length / 2
                    before = where.get(car.id)
                    where[car.id] = ahead
                    if before is None or abs(ahead) > PASS_WINDOW_M or abs(before) > PASS_WINDOW_M:
                        continue
                    if before < 0 <= ahead and my_speed >= RACING_KMH:
                        passes.append((race.sim_time, car.driver, car.car_class))
    except (EOFError, zlib.error, json.JSONDecodeError):
        pass                        # a tape cut off mid-write
    return passes


def main():
    tapes = sys.argv[1:] or RACE_TAPES
    total_passes = total_warned = total_calls = total_real = 0
    for tape in tapes:
        passes = passes_on(tape)
        if not passes:
            print(f"{tape}: no faster class went by")
            continue
        calls = []
        for row in replay(tape, None, "class_truth"):
            if row[1] in CLASS_CALLS and row[2] == "spoken":
                calls.append(row[0])
        warned = 0
        for when, driver, car_class in passes:
            for said in calls:
                if 0 <= when - said <= WARNED_WITHIN_S:
                    warned += 1
                    break
        real = 0
        for said in calls:
            for when, driver, car_class in passes:
                if 0 <= when - said <= WARNED_WITHIN_S:
                    real += 1
                    break
        total_passes += len(passes)
        total_warned += warned
        total_calls += len(calls)
        total_real += real
        print(f"{tape}: {len(passes)} faster-class passes, {warned} warned before; "
              f"{len(calls)} calls, {real} followed by a pass")
    if total_passes:
        print(f"recall {total_warned}/{total_passes} = {total_warned / total_passes:.0%}, "
              f"precision {total_real}/{total_calls} = {(total_real / total_calls if total_calls else 0):.0%}")


if __name__ == "__main__":
    main()
