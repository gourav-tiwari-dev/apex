"""D4: in every fight on his tapes (any two same-class cars within a second, not just him), how often
did the car behind get past within the next lap - by how much quicker it was on the road?
The team call (defend / let by / attack) uses these numbers.  Usage: pass_study.py"""

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tapes import snapshots, RACE_TAPES
from race_model import RaceModel, BATTLE_S

SAMPLE_S = 10.0
PASS_HOLD_S = 10.0  # a pass counts when the new order holds this long


def fights(tape):
    """(quicker by, sure, gap, passed within a lap) for every fight sampled every SAMPLE_S."""
    length = max(
        max([o.lap_dist for o in s.opponents] + [0.0]) for s in snapshots(tape)
    )
    model = RaceModel(lap_length=length)
    open_items, done, last = [], [], None
    for snap in snapshots(tape):
        now = snap.sim_time
        model.see_race(snap, now)
        order = model.order()
        position = {key: i for i, key in enumerate(order)}
        keep = []
        for item in open_items:
            front, back, start, info, swapped_at = item
            if front not in position or back not in position:
                continue
            ahead_now = position[back] < position[front]
            if ahead_now and swapped_at is None:
                swapped_at = now
            elif not ahead_now:
                swapped_at = None
            if swapped_at is not None and now - swapped_at >= PASS_HOLD_S:
                done.append(info + (True,))
                continue
            back_d = model.distance(back)
            if back_d is not None and back_d > start + length:
                done.append(info + (False,))
                continue
            keep.append((front, back, start, info, swapped_at))
        open_items = keep
        if last is not None and now - last < SAMPLE_S:
            continue
        last = now
        busy = {(f, b) for f, b, *_ in open_items}
        for front, back in zip(order, order[1:]):
            fc, bc = model.car(front), model.car(back)
            if (
                fc is None
                or bc is None
                or fc.car_class != bc.car_class
                or (front, back) in busy
            ):
                continue
            g = model.gap(front, back)
            q = model.quicker(back, front)
            if g is None or q is None or not 0 < g <= BATTLE_S:
                continue
            open_items.append(
                (front, back, model.distance(back), (q[0], q[1], g), None)
            )
    return done


def main():
    rows = []
    for tape in RACE_TAPES:
        rows += fights(tape)
    print(
        f"{len(rows)} fight samples, {sum(r[3] for r in rows)} ended in a pass within a lap"
    )
    bands = [(-99, -0.5), (-0.5, 0.0), (0.0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 99)]
    print("car behind quicker by (s/lap, road) -> passed within a lap")
    for lo, hi in bands:
        sel = [r for r in rows if lo <= r[0] < hi]
        if sel:
            rate = sum(r[3] for r in sel) / len(sel)
            sure = [r for r in sel if r[1]]
            sure_rate = (sum(r[3] for r in sure) / len(sure)) if sure else None
            print(
                f"   {lo:+5.1f} .. {hi:+5.1f}: {len(sel):4d} fights, passed {rate:4.0%}"
                + (
                    f"   (sure trend only: {len(sure)}, {sure_rate:.0%})"
                    if sure
                    else ""
                )
            )
    return rows


if __name__ == "__main__":
    main()
