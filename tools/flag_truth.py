"""Every "Yellow flag" the radio said, against the tape (26 Sep): was another car really slow
(under 60 km/h, not in the pits) in the sector showing the yellow, within 6 s of the call?
The game throws the yellow as the incident starts: on 23 Sep a car stopped 5.8 s after it.

Usage: flag_truth.py [TAPE ...]   (default: every race tape in tools/tapes.py)
Done-check: at least 85% of spoken yellows have a slow car behind them (26 Sep: 70% before the
value-3 and own-incident fixes, 90% after). The rest are listed as UNVERIFIED, not false: they
are the game's own yellow flag, the cause just is not in the data (a car off at speed, say)."""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
from replay_orders import replay
from tapes import RACE_TAPES, snapshots
from race_state import YELLOW_FLAG
from seats.race_engineer import FLAG_SLOT

SLOW_KMH = 60.0
BACKED_BAR = 0.85
WITHIN_S = 6.0
# where sectors 1 and 2 end, in lap metres: tapes before 25 Sep night carry no sector per car.
# Measured on the 25 Sep night tape (every car's sector against its lap distance, 133k rows)
SECTOR_ENDS = {"Circuit de la Sarthe": (1910.0, 7735.0)}


def sector_of(car, track):
    """The game's sector number (1, 2, 0 = sector 3) of a car, from the tape or its lap distance."""
    if car.sector is not None:
        return car.sector
    ends = SECTOR_ENDS.get(track)
    if ends is None or car.lap_dist is None:
        return None
    if car.lap_dist < ends[0]:
        return 1
    if car.lap_dist < ends[1]:
        return 2
    return 0


def yellow_slots_of(snap):
    slots = []
    for slot, flag in enumerate(snap.session.sector_flags):
        if flag == YELLOW_FLAG:
            slots.append(slot)
    return slots


def slow_car_in(snap, yellow_slots):
    """True when some other car is slow in one of these flag slots."""
    for car in snap.opponents:
        if car.in_pits or car.speed_kmh is None or car.speed_kmh < 0:
            continue
        sector = sector_of(car, snap.session.track)
        if car.speed_kmh < SLOW_KMH and FLAG_SLOT.get(sector) in yellow_slots:
            return True
    return False


def main():
    tapes = sys.argv[1:] or RACE_TAPES
    wrong = []
    total = 0
    for tape in tapes:
        race_snapshots = list(snapshots(tape))
        said = []
        for row in replay(tape, None, "flag_truth"):
            if row[1] == "YELLOW" and row[2] == "spoken":
                said.append(row[0])
        backed = 0
        for when in said:
            total += 1
            near = [s for s in race_snapshots if abs(s.sim_time - when) <= WITHIN_S]
            at_the_call = min(near, key=lambda s: abs(s.sim_time - when))
            # the sectors the call was about: the flags can move on before the car stops
            yellow_slots = yellow_slots_of(at_the_call)
            real = False
            for snap in near:
                if slow_car_in(snap, yellow_slots):
                    real = True
                    break
            if real:
                backed += 1
            else:
                wrong.append(f"{tape} {when:.1f}s")
        print(f"{tape}: {len(said)} yellows said, {backed} with a slow car behind them")
    backed_share = (total - len(wrong)) / total if total else 1.0
    print(f"{total} yellows said on {len(tapes)} tapes, {backed_share:.0%} with a slow car behind them "
          f"(bar {BACKED_BAR:.0%})")
    if wrong:
        print("UNVERIFIED (the game's yellow, no slow car seen):\n  " + "\n  ".join(wrong))
    passed = backed_share >= BACKED_BAR
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
