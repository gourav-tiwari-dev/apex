import json, gzip
from live_telemetry import CarState
from driving.corner_stats import CornerStats
from driving.laps import LapDistance, LapCounter

TAPE = "tape_60hz_clean.jsonl.gz"
GOLDEN = "golden_telemetry.json"


def replay_corner_stats(tape_data):
    lap_counter = LapCounter()
    lap_distance = LapDistance()
    corner_stats = CornerStats()
    rows = []
    with gzip.open(tape_data, "rt") as f:
        for x in f:
            data = json.loads(x)
            car_state = CarState(**data)
            lap_count = lap_counter.update(car_state)
            real_lap_distance = lap_distance.update(car_state)
            stat = corner_stats.update(car_state, lap_count, real_lap_distance)
            if stat:
                rows.append(
                    [stat.lap_count, stat.corner, stat.brake_onset, stat.min_speed]
                )
    return rows


def test_golden_match_tape():
    rows = replay_corner_stats(TAPE)
    with open(GOLDEN, "rt") as f:
        golden_data = json.load(f)
    assert rows == golden_data


if __name__ == "__main__":
    rows = replay_corner_stats(TAPE)
    with open(GOLDEN, "wt") as f:
        json.dump(rows, f, indent=1)
    print(f"saved {len(rows)} rows to {GOLDEN}")
