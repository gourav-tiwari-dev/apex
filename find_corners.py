import gzip, json

rows = []
with gzip.open("tape.jsonl.gz", "rt") as f:
    for line in f:
        frame = json.loads(line)
        if frame["brake"] > 0.5:
            rows.append((frame["lap_dist"], frame["speed_kmh"]))

for d, s in sorted(rows):
    print(f"dist={d:6.0f}  speed={s:4.0f}")