"""Faster classes against the tape (27 Sep). His mark on the 58-car race (25 Sep): "LMP do not detected".
Two truths, both from tape positions only:
- ARRIVAL: a faster-class car comes within 1 s behind him on the road (the standard "within a
  second" of racing: the tow, DRS range), once per car a minute. This is what the call says
  ("on you in about N seconds"). Warned = a call in the 20 s before; a call is right when an
  arrival follows within 20 s
- PASS: it goes from behind to ahead. Shown too, but a pass waits on corners, traffic and his
  defending: on that race Felber sat ~1 s behind him for 30 s through the corners before passing

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
ARRIVED_S = 1.0              # within a second behind: on him
ARRIVAL_AGAIN_S = 60.0       # the same car arriving again inside this is the same arrival
ARRIVAL_WINDOW_S = 20.0
RACING_KMH = 60.0
CLASS_CALLS = ("FASTER_CLASS_BEHIND", "FASTER_FIGHT_BEHIND")


def passes_on(tape):
    """(passes, arrivals): (sim time, driver, class) of every faster-class car that went by him on
    the road, and of every time one came within 50 m behind him."""
    passes = []
    arrivals = []
    arrived_at = {}                # car id -> last time it was within a second behind
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
                    if before is None or my_speed < RACING_KMH:
                        continue
                    seconds_behind = -ahead / (car.speed_kmh / 3.6) if car.speed_kmh and ahead < 0 else None
                    if seconds_behind is not None and seconds_behind <= ARRIVED_S:
                        last = arrived_at.get(car.id)
                        if last is None or race.sim_time - last > ARRIVAL_AGAIN_S:
                            arrivals.append((race.sim_time, car.driver, car.car_class))
                        arrived_at[car.id] = race.sim_time
                    if abs(ahead) > PASS_WINDOW_M or abs(before) > PASS_WINDOW_M:
                        continue
                    if before < 0 <= ahead:
                        passes.append((race.sim_time, car.driver, car.car_class))
    except (EOFError, zlib.error, json.JSONDecodeError):
        pass                        # a tape cut off mid-write
    return passes, arrivals


def matched(events, calls, window):
    """(events warned by a call in the window before, calls followed by an event in the window)."""
    warned = 0
    for when, driver, car_class in events:
        for said in calls:
            if 0 <= when - said <= window:
                warned += 1
                break
    right = 0
    for said in calls:
        for when, driver, car_class in events:
            if 0 <= when - said <= window:
                right += 1
                break
    return warned, right


def main():
    tapes = sys.argv[1:] or RACE_TAPES
    totals = {"arrivals": 0, "arrivals_warned": 0, "passes": 0, "passes_warned": 0,
              "calls": 0, "calls_arrived": 0, "calls_passed": 0}
    for tape in tapes:
        passes, arrivals = passes_on(tape)
        if not passes and not arrivals:
            print(f"{tape}: no faster class came up behind him")
            continue
        calls = []
        for row in replay(tape, None, "class_truth"):
            if row[1] in CLASS_CALLS and row[2] == "spoken":
                calls.append(row[0])
        arrivals_warned, calls_arrived = matched(arrivals, calls, ARRIVAL_WINDOW_S)
        passes_warned, calls_passed = matched(passes, calls, WARNED_WITHIN_S)
        print(f"{tape}: {len(calls)} calls | arrivals {arrivals_warned}/{len(arrivals)} warned, "
              f"{calls_arrived}/{len(calls)} calls followed by an arrival | passes {passes_warned}/{len(passes)} "
              f"warned, {calls_passed}/{len(calls)} calls followed by a pass")
        totals["arrivals"] += len(arrivals)
        totals["arrivals_warned"] += arrivals_warned
        totals["passes"] += len(passes)
        totals["passes_warned"] += passes_warned
        totals["calls"] += len(calls)
        totals["calls_arrived"] += calls_arrived
        totals["calls_passed"] += calls_passed
    print(f"ARRIVALS warned {totals['arrivals_warned']}/{totals['arrivals']}, calls followed by an arrival "
          f"{totals['calls_arrived']}/{totals['calls']} | PASSES warned {totals['passes_warned']}/{totals['passes']}, "
          f"calls followed by a pass {totals['calls_passed']}/{totals['calls']}")


if __name__ == "__main__":
    main()
