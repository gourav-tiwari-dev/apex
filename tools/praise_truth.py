"""Pass praise against the tape (27 Sep): did the pass stick?

His marks, 25 Sep: "wrong call, overtake" and "I did not make the overtake completely" - praise said
while the car was still alongside or came back past. The fix waits for the pass to be done. This
checks every PASS_PRAISE on the race tapes: 10 s after it, is his place no worse than at the praise
(the car he passed has not come back by)? A place lost to someone else also counts against it: the
check is strict on purpose.

Usage: praise_truth.py [TAPE ...]   (default: every race tape in tools/tapes.py)
Exit code 0 = every praise held."""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
from replay_orders import replay
from tapes import RACE_TAPES, snapshots

STICKS_FOR_S = 10.0


def place_at(race_snapshots, when):
    found = None
    for snap in race_snapshots:
        if snap.sim_time > when:
            break
        found = snap.me.place
    return found


def main():
    tapes = sys.argv[1:] or RACE_TAPES
    undone = []
    total = held = 0
    for tape in tapes:
        race_snapshots = [s for s in snapshots(tape) if s.me is not None]
        praised = []
        for row in replay(tape, None, "praise_truth"):
            if row[1] == "PASS_PRAISE" and row[2] == "spoken":
                praised.append((row[0], row[4]))
        tape_held = 0
        for when, line in praised:
            total += 1
            before = place_at(race_snapshots, when)
            after = place_at(race_snapshots, when + STICKS_FOR_S)
            if before is not None and after is not None and after <= before:
                held += 1
                tape_held += 1
            else:
                undone.append(
                    f"{tape} {when:.1f}s: P{before} at the praise, P{after} 10 s later: {line!r}"
                )
        print(f"{tape}: {len(praised)} praises, {tape_held} held 10 s")
    print(f"{held}/{total} praises held")
    if not undone:
        print("PASS")
        return 0
    print("FAIL:")
    for line in undone:
        print("  " + line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
