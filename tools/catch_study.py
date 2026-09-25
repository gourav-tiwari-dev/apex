"""D3: the race model's catch forecasts ("on him in X"), for every pair on his tapes, against what
happened. Usage: catch_study.py"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tapes import snapshots, RACE_TAPES
from race_model import RaceModel

SAMPLE_S = 15.0
CAUGHT_S = 0.4                 # within this: caught (the forecast aims at 0.3)


def forecasts(tape):
    length = max(max([o.lap_dist for o in s.opponents] + [0.0]) for s in snapshots(tape))
    model = RaceModel(lap_length=length)
    live, done, last = {}, [], None
    for snap in snapshots(tape):
        now = snap.sim_time
        model.see_race(snap, now)
        for pair, (made, predicted) in list(live.items()):
            front, back = pair
            g = model.gap(front, back)
            if g is not None and g <= CAUGHT_S:
                done.append((predicted, now - made))
                del live[pair]
            elif now - made > max(3 * predicted, 120) or g is None:
                done.append((predicted, None))           # never caught (or the pair broke up)
                del live[pair]
        if last is not None and now - last < SAMPLE_S:
            continue
        last = now
        order = model.order()
        for front, back in zip(order, order[1:]):
            if (front, back) in live:
                continue
            g = model.gap(front, back)
            if g is None or not 0.8 < g < 6:
                continue
            found = model.catch(back, front)
            if found is not None and found[0] < 900:
                live[(front, back)] = (now, found[0])
    return done


def main():
    rows = []
    for tape in RACE_TAPES:
        rows += forecasts(tape)
    caught = [(p, a) for p, a in rows if a is not None]
    print(f"{len(rows)} sure catch forecasts; caught {len(caught)} ({len(caught) / max(len(rows), 1):.0%})")
    if caught:
        rel = [abs(a - p) / p for p, a in caught]
        print(f"   when caught: median time error {statistics.median(rel):.0%} of the forecast; "
              f"within 25%: {sum(1 for r in rel if r <= 0.25) / len(rel):.0%}; within 50%: {sum(1 for r in rel if r <= 0.5) / len(rel):.0%}")
    for lo, hi in ((0, 60), (60, 180), (180, 900)):
        sel = [(p, a) for p, a in rows if lo <= p < hi]
        if sel:
            hit = sum(1 for p, a in sel if a is not None and a <= 1.5 * p) / len(sel)
            print(f"   forecast {lo:3d}-{hi:3d} s: {len(sel):3d}, caught within 1.5x the forecast: {hit:.0%}")


if __name__ == "__main__":
    main()
