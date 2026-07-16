import gzip, json
vals = []
with gzip.open("tape_60hz_clean.jsonl.gz", "rt") as f:
    for line in f:
        fr = json.loads(line)
        vals.append((abs(fr["yaw_rate"]), round(fr["lap_dist"]), round(fr["speed_kmh"])))

vals.sort(reverse=True)
print("Top 15 |yaw_rate| (clean laps = normal cornering ceiling):")
for y, d, s in vals[:15]:
    print(f"  yaw={y:.3f}  dist={d}  speed={s}")