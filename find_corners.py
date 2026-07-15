import gzip, json

prev_brake = 0.0
with gzip.open("tape.jsonl.gz", "rt") as f:
    for line in f:
        frame = json.loads(line)
        b = frame["brake"]
        if prev_brake < 0.5 <= b:          # brake just crossed into "hard"
            print(f"dist={frame['lap_dist']:6.0f}  speed={frame['speed_kmh']:4.0f}")
        prev_brake = b