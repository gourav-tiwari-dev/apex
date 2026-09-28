"""D7 on replays (27 Sep): what the radio claims against the tape, and how much it talks.

- his place said ("Chequered flag. P13.", a gap report's "P19.") is his place on the tape then (within
  2 s, places move). Battle and train calls name other cars' places: not his, not checked here
- "last lap" is said only on the lap that really is his last (tapes that reach his flag)
- no fuel flip-flop: two fuel verdicts a minute apart that disagree in direction (fine <-> short)
- talk: lines a minute and the kinds that say the most, reported (the log does not keep which calls
  were exempt from the Governor's budget, so the budget itself is not re-checked here)

Usage: claims_check.py [TAPE ...]   (default: every race tape in tools/tapes.py)
Exit code 0 = no false claim."""

import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
from replay_orders import replay
from tapes import RACE_TAPES, snapshots
from race.facts import class_place

PLACE_WITHIN_S = 2.0
FLIP_FLOP_S = 60.0
GOOD_FUEL = ("fine", "saving")
BAD_FUEL = ("save", "short", "box")
HIS_PLACE_KINDS = ("FINISH", "GAP_REPORT", "SETTLED")
PLACE_WORD = re.compile(r"\bP(\d{1,2})( in class)?\b")


def places_between(race_snapshots, start, end, in_class=False):
    """His places on the tape in that window: overall, or among his class ("P7 in class", 27 Sep)."""
    places = set()
    for snap in race_snapshots:
        if start <= snap.sim_time <= end:
            if in_class:
                places.add(class_place(snap, snap.me.place, snap.me.car_class))
            else:
                places.add(snap.me.place)
    return places


def main():
    tapes = sys.argv[1:] or RACE_TAPES
    false_claims = []
    for tape in tapes:
        race_snapshots = [s for s in snapshots(tape) if s.me is not None]
        finish_laps = None
        for snap in race_snapshots:
            if snap.me.finish_status == 1:
                finish_laps = snap.me.laps
                break
        spoken = [r for r in replay(tape, None, "claims") if r[2] == "spoken"]
        places_checked = 0
        last_laps_checked = 0
        fuel_said = []
        kinds = collections.Counter()
        for when, kind, status, reason, line, facts in spoken:
            facts = json.loads(facts or "{}")
            kinds[kind] += 1
            if kind in HIS_PLACE_KINDS:
                for number, in_class in PLACE_WORD.findall(line):
                    places_checked += 1
                    around = places_between(
                        race_snapshots,
                        when - PLACE_WITHIN_S,
                        when + PLACE_WITHIN_S,
                        in_class=bool(in_class),
                    )
                    if around and int(number) not in around:
                        false_claims.append(
                            f"{tape} {when:.1f}s {kind}: said P{number}{in_class}, tape had "
                            f"{sorted(around)}: {line!r}"
                        )
            if kind in ("LAST_LAP", "FLAG_LAST_LAP") and finish_laps is not None:
                last_laps_checked += 1
                before = [s for s in race_snapshots if s.sim_time <= when]
                if before:
                    laps_done = before[-1].me.laps
                    if finish_laps - laps_done != 1:
                        false_claims.append(
                            f"{tape} {when:.1f}s {kind}: 'last lap' with {finish_laps - laps_done} "
                            f"laps still to drive (laps done {laps_done}, flag at {finish_laps})"
                        )
            if kind == "FUEL" and facts.get("verdict"):
                fuel_said.append((when, facts["verdict"]))
        for (t1, v1), (t2, v2) in zip(fuel_said, fuel_said[1:]):
            flipped = (v1 in GOOD_FUEL and v2 in BAD_FUEL) or (
                v1 in BAD_FUEL and v2 in GOOD_FUEL
            )
            if flipped and t2 - t1 < FLIP_FLOP_S:
                false_claims.append(
                    f"{tape} {t2:.1f}s FUEL flip-flop: {v1} at {t1:.0f}s, {v2} at {t2:.0f}s"
                )
        minutes = (
            (race_snapshots[-1].sim_time - race_snapshots[0].sim_time) / 60
            if race_snapshots
            else 0
        )
        top = ", ".join(f"{kind} {count}" for kind, count in kinds.most_common(3))
        per_minute = len(spoken) / minutes if minutes else 0
        print(
            f"{tape}: {len(spoken)} lines ({per_minute:.1f}/min; most: {top}), his places checked "
            f"{places_checked}, last laps checked {last_laps_checked}, fuel verdicts {len(fuel_said)}"
        )
    if not false_claims:
        print("PASS")
        return 0
    print("FAIL:")
    for claim in false_claims:
        print("  " + claim)
    return 1


if __name__ == "__main__":
    sys.exit(main())
