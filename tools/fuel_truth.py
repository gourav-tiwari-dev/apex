"""Every fuel number the radio said, against what the tape says was true (26 Sep).

The truth: with no refuel, the laps spare he really had is the fuel (or virtual energy) left at the
flag over his clean burn a lap, the same at every moment of the race. Each spoken fuel call is
replayed (no model, no speaker) and compared with it.

Usage: fuel_truth.py [TAPE ...]   (default: every race tape in tools/tapes.py)
Done-check: every spoken fuel verdict matches the true one, and every number is within 0.15 laps."""
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
from replay_orders import replay
from tapes import RACE_TAPES, snapshots
from seats.strategist import verdict_of, GREEN_PHASE

NUMBER_OFF_LAPS = 0.15


def truth(tape):
    """(laps spare at the flag, fuel or energy) or None when he did not finish on one tank."""
    at_line = {}
    finished = None
    refuelled = False
    last_fuel = None
    green = False
    for snap in snapshots(tape):
        me = snap.me
        if me is None:
            continue
        if snap.session.game_phase == GREEN_PHASE:
            green = True
        if not green:
            continue                                 # the formation lap, and a fuel set at the start
        # after the green flag, whatever the phase: at the flag the session is already "over"
        if last_fuel is not None and me.fuel > last_fuel + 0.5:
            refuelled = True
        last_fuel = me.fuel
        if me.laps not in at_line:
            at_line[me.laps] = (me.fuel, me.virtual_energy)
        if me.finish_status == 1:
            finished = (me.fuel, me.virtual_energy)
            break                                    # back in the pits the game fills the car up
    if finished is None or refuelled or len(at_line) < 3:
        return None
    laps = sorted(at_line)
    fuel_burns = []
    energy_burns = []
    for before, after in zip(laps, laps[1:]):
        fuel_burns.append(at_line[before][0] - at_line[after][0])
        energy_burns.append(at_line[before][1] - at_line[after][1])
    options = [(finished[0] / statistics.median(fuel_burns), "fuel")]
    energy_burn = statistics.median(energy_burns)
    if energy_burn > 0.0005:
        options.append((finished[1] / energy_burn, "energy"))
    return min(options)


def main():
    tapes = sys.argv[1:] or RACE_TAPES
    failures = []
    for tape in tapes:
        real = truth(tape)
        if real is None:
            print(f"{tape}: no truth (did not finish on one tank)")
            continue
        real_spare, limit = real
        print(f"{tape}: truth {real_spare:.2f} laps spare ({limit})")
        for row in replay(tape, None, "fuel_truth"):
            sim_time, kind, status, reason, line, facts = row
            if kind != "FUEL" or status != "spoken":
                continue
            facts = json.loads(facts or "{}")
            said_verdict = facts.get("verdict")
            laps_left = facts.get("laps_left") or 0
            signed_spare = facts.get("spare_laps", 0)
            if said_verdict in ("save", "box", "short"):
                signed_spare = -signed_spare         # facts carry the size, the verdict the sign
            true_verdict = verdict_of(round(real_spare, 1), laps_left)
            if said_verdict == "saving":
                true_verdict = said_verdict          # his own saving: judged by the number only
            off = abs(signed_spare - real_spare)
            mark = "ok"
            if said_verdict != true_verdict or off > NUMBER_OFF_LAPS:
                mark = "WRONG"
                failures.append(f"{tape} {sim_time:.0f}s said {said_verdict} {signed_spare}, "
                                f"true {true_verdict} {real_spare:.2f}")
            print(f"   {sim_time:7.1f}s  said {said_verdict:7s} {signed_spare:+.1f}  true {true_verdict:7s} "
                  f"{real_spare:+.2f}  {mark}  {line[:70]!r}")
    print("PASS" if not failures else "FAIL:\n  " + "\n  ".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
