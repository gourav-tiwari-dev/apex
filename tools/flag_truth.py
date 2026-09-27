"""Every "Yellow flag" the radio said, against the tape (26 Sep, reworked 27 Sep).

FAILS on the two mistakes the rules are there to stop:
- a yellow said from a flag value that is not a yellow (value 3 at the clock's end, 25 Sep), or not
  in his sector or the next one
- a yellow said while he was the slow car himself (under 60 km/h: his own incident)
REPORTS, as a measure: was another car really slow (under 60 km/h, not in the pits) in that sector
within 6 s? On the online warning-lobby race (25 Sep 22:34) 4 yellows had no slow car in the data
(no car left the lobby either): the game's yellow, cause not visible. Those are listed UNVERIFIED.

Usage: flag_truth.py [TAPE ...]   (default: every race tape in tools/tapes.py)"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
from replay_orders import replay
from tapes import RACE_TAPES, snapshots
from race_state import YELLOW_FLAG
from seats.race_engineer import FLAG_SLOT, NEXT_SECTOR

SLOW_KMH = 60.0
HIS_OWN_INCIDENT_KMH = 60.0
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


def his_speed_at(tape, when):
    """His speed (car frames) at a moment, from the tape."""
    import gzip, json, zlib

    speed = None
    try:
        with gzip.open(tape, "rt") as f:
            for line in f:
                d = json.loads(line)
                if d.get("t") is None:
                    if d["elapsed_time"] > when:
                        return speed
                    speed = d["speed_kmh"]
    except (EOFError, zlib.error, json.JSONDecodeError):
        pass
    return speed


def main():
    tapes = sys.argv[1:] or RACE_TAPES
    mistakes = []
    unverified = []
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
            yellow_slots = yellow_slots_of(at_the_call)
            here = FLAG_SLOT.get(at_the_call.me.sector)
            next_one = FLAG_SLOT.get(NEXT_SECTOR.get(at_the_call.me.sector))
            if here not in yellow_slots and next_one not in yellow_slots:
                mistakes.append(
                    f"{tape} {when:.1f}s: no yellow flag (value 1) in his sector or the next "
                    f"(flags {at_the_call.session.sector_flags})"
                )
            speed = his_speed_at(tape, when)
            if speed is not None and speed < HIS_OWN_INCIDENT_KMH:
                mistakes.append(
                    f"{tape} {when:.1f}s: said while he was at {speed:.0f} km/h (his own incident)"
                )
            real = False
            for snap in near:
                if slow_car_in(snap, yellow_slots):
                    real = True
                    break
            if real:
                backed += 1
            else:
                unverified.append(f"{tape} {when:.1f}s")
        print(f"{tape}: {len(said)} yellows said, {backed} with a slow car behind them")
    backed_share = (total - len(unverified)) / total if total else 1.0
    print(
        f"{total} yellows said on {len(tapes)} tapes, {backed_share:.0%} with a slow car behind them"
    )
    if unverified:
        print("UNVERIFIED (the game's yellow, no slow car in the data):")
        for line in unverified:
            print("  " + line)
    if not mistakes:
        print("PASS")
        return 0
    print("FAIL:")
    for line in mistakes:
        print("  " + line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
