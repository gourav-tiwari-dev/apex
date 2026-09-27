"""Check a v2 tape: which fields the game actually filled in.

    python audit_tape.py tape_20260924_203000.jsonl.gz

LIVE      the value changed during the recording
CONSTANT  it had one value the whole time (fine for things like track name)
DEAD      always zero / empty: the game never filled it in, so no seat may rely on it

One recording can't prove a field is dead if nothing happened to change it
(no pit stop -> pit fields stay 0). Those need a session where it happens.
"""

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


def main(tape_path):
    car_frames = 0
    race_lines = 0
    near_lines = 0
    near_cars_total = 0
    first_time = None
    last_time = None
    first_race_time = None
    last_race_time = None
    car_fields = {}
    session_fields = {}
    me_fields = {}
    opponent_fields = {}
    opponents_per_snapshot = []
    opponents_without_telemetry = 0

    with gzip.open(tape_path, "rt") as f:
        for line in f:
            row = json.loads(line)
            kind = row.get("t")

            if kind == "race":
                race_lines += 1
                if first_race_time is None:
                    first_race_time = row["sim_time"]
                last_race_time = row["sim_time"]
                for name, value in row["session"].items():
                    add(session_fields, name, value)
                if row["me"] is not None:
                    for name, value in row["me"].items():
                        add(me_fields, name, value)
                opponents_per_snapshot.append(len(row["opponents"]))
                for opponent in row["opponents"]:
                    if opponent["x"] is None:
                        opponents_without_telemetry += 1
                    for name, value in opponent.items():
                        # judge each field per car: "live" must mean it moved for a car
                        add(opponent_fields, name, [opponent["id"], value])
                continue

            if kind == "near":
                near_lines += 1
                near_cars_total += len(row["cars"])
                continue

            car_frames += 1
            if first_time is None:
                first_time = row["elapsed_time"]
            last_time = row["elapsed_time"]
            for name in (
                "steering",
                "steering_filtered",
                "pos",
                "ori",
                "delta_best",
                "last_impact_time",
                "last_impact_magnitude",
            ):
                add(car_fields, name, row.get(name))

    if car_frames == 0:
        print("No car frames on this tape.")
        return
    duration = last_time - first_time
    print(f"tape: {tape_path}")
    print(
        f"car frames: {car_frames} over {duration:.1f} s  ({car_frames / max(duration, 0.001):.1f} per second)"
    )
    if duration <= 0:
        print(
            "BUFFER FROZEN: the sim clock never moved. Was the game paused or in a menu?"
        )
    if race_lines == 0:
        print(
            "No race snapshots: this is a v1 tape (or the game was not in a session)."
        )
        return
    race_span = last_race_time - first_race_time
    print(
        f"race snapshots: {race_lines}  ({race_lines / max(race_span, 0.001):.1f} per second)"
    )
    print(
        f"opponents per snapshot: min {min(opponents_per_snapshot)}  max {max(opponents_per_snapshot)}"
    )
    print(f"opponent rows with no telemetry: {opponents_without_telemetry}")
    if near_lines:
        print(
            f"near-car lines: {near_lines}  (average {near_cars_total / near_lines:.1f} cars when someone was near)"
        )
    else:
        print("near-car lines: 0  (nobody came within the spotter radius)")

    print_table("MY CAR, every frame", car_fields)
    print_table("SESSION", session_fields)
    print_table("ME, each snapshot", me_fields)

    # opponent fields: a field is LIVE if it changed for at least one car
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
