"""Check a v2 tape: which fields the game actually filled in.

    python dev/audit_tape.py tape_20260924_203000.jsonl.gz

LIVE      the value changed during the recording
CONSTANT  it had one value the whole time (fine for things like track name)
DEAD      always zero / empty: the game never filled it in, so no seat may rely on it

One recording can't prove a field is dead if nothing happened to change it
(no pit stop -> pit fields stay 0). Those need a session where it happens.
"""

import os
import sys

# run as `python dev/audit_tape.py` from the project folder: Apex's modules are one folder up
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gzip
import json
import sys

ZEROISH = (None, 0, 0.0, "", False, [], [0, 0, 0])


def as_key(value):
    return json.dumps(value, sort_keys=True)


def verdict(values):
    distinct = set(values)
    if len(distinct) > 1:
        return "LIVE"
    only = json.loads(next(iter(distinct))) if distinct else None
    if only in ZEROISH or (isinstance(only, list) and all(v in ZEROISH for v in only)):
        return "DEAD"
    return "CONSTANT"


def add(seen, name, value):
    if name not in seen:
        seen[name] = []
    seen[name].append(as_key(value))


def print_table(title, seen):
    print()
    print(title)
    print("-" * len(title))
    for name in seen:
        values = seen[name]
        example = json.loads(values[-1])
        print(f"  {name:26s} {verdict(values):9s} e.g. {str(example)[:60]}")


CAR_FIELDS = (
    "steering",
    "steering_filtered",
    "pos",
    "ori",
    "delta_best",
    "last_impact_time",
    "last_impact_magnitude",
)


class TapeAudit:
    """What one tape holds: how many lines of each kind, over what time, and every value each
    field took."""

    def __init__(self):
        self.car_frames = 0
        self.race_lines = 0
        self.near_lines = 0
        self.near_cars_total = 0
        self.first_time = None
        self.last_time = None
        self.first_race_time = None
        self.last_race_time = None
        self.car_fields = {}
        self.session_fields = {}
        self.me_fields = {}
        self.opponent_fields = {}
        self.opponents_per_snapshot = []
        self.opponents_without_telemetry = 0

    def read(self, row):
        """One line of the tape: a race snapshot, a near-cars line, or a car frame."""
        kind = row.get("t")
        if kind == "race":
            self.read_race(row)
        elif kind == "near":
            self.near_lines += 1
            self.near_cars_total += len(row["cars"])
        else:
            self.read_car(row)

    def read_race(self, row):
        """A race snapshot: the session, my car, and every opponent."""
        self.race_lines += 1
        if self.first_race_time is None:
            self.first_race_time = row["sim_time"]
        self.last_race_time = row["sim_time"]
        for name, value in row["session"].items():
            add(self.session_fields, name, value)
        if row["me"] is not None:
            for name, value in row["me"].items():
                add(self.me_fields, name, value)
        self.opponents_per_snapshot.append(len(row["opponents"]))
        for opponent in row["opponents"]:
            if opponent["x"] is None:
                self.opponents_without_telemetry += 1
            for name, value in opponent.items():
                # judge each field per car: "live" must mean it moved for a car
                add(self.opponent_fields, name, [opponent["id"], value])

    def read_car(self, row):
        """A frame of my car's telemetry."""
        self.car_frames += 1
        if self.first_time is None:
            self.first_time = row["elapsed_time"]
        self.last_time = row["elapsed_time"]
        for name in CAR_FIELDS:
            add(self.car_fields, name, row.get(name))


def main(tape_path):
    audit = TapeAudit()
    with gzip.open(tape_path, "rt") as f:
        for line in f:
            audit.read(json.loads(line))

    if audit.car_frames == 0:
        print("No car frames on this tape.")
        return
    if not print_counts(tape_path, audit):
        return

    print_table("MY CAR, every frame", audit.car_fields)
    print_table("SESSION", audit.session_fields)
    print_table("ME, each snapshot", audit.me_fields)
    print_opponents(audit.opponent_fields)


def print_counts(tape_path, audit):
    """The tape's frames, race snapshots and near-car lines. False when it has no race
    snapshots: then there is nothing more to report."""
    duration = audit.last_time - audit.first_time
    print(f"tape: {tape_path}")
    print(
        f"car frames: {audit.car_frames} over {duration:.1f} s  ({audit.car_frames / max(duration, 0.001):.1f} per second)"
    )
    if duration <= 0:
        print(
            "BUFFER FROZEN: the sim clock never moved. Was the game paused or in a menu?"
        )
    if audit.race_lines == 0:
        print(
            "No race snapshots: this is a v1 tape (or the game was not in a session)."
        )
        return False
    race_span = audit.last_race_time - audit.first_race_time
    print(
        f"race snapshots: {audit.race_lines}  ({audit.race_lines / max(race_span, 0.001):.1f} per second)"
    )
    print(
        f"opponents per snapshot: min {min(audit.opponents_per_snapshot)}  max {max(audit.opponents_per_snapshot)}"
    )
    print(f"opponent rows with no telemetry: {audit.opponents_without_telemetry}")
    if audit.near_lines:
        print(
            f"near-car lines: {audit.near_lines}  (average {audit.near_cars_total / audit.near_lines:.1f} cars when someone was near)"
        )
    else:
        print("near-car lines: 0  (nobody came within the spotter radius)")
    return True


def print_opponents(opponent_fields):
    """Opponent fields: a field is LIVE if it changed for at least one car."""
    print()
    print("OPPONENTS, each snapshot")
    print("------------------------")
    for name, pairs in opponent_fields.items():
        by_car = {}
        for pair in pairs:
            car_id, value = json.loads(pair)
            if car_id not in by_car:
                by_car[car_id] = []
            by_car[car_id].append(as_key(value))
        verdicts = [verdict(values) for values in by_car.values()]
        if "LIVE" in verdicts:
            overall = "LIVE"
        elif all(v == "DEAD" for v in verdicts):
            overall = "DEAD"
        else:
            overall = "CONSTANT"
        live_cars = verdicts.count("LIVE")
        print(f"  {name:26s} {overall:9s} live on {live_cars} of {len(verdicts)} cars")


if __name__ == "__main__":
    main(sys.argv[1])
