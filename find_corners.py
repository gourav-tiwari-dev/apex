import gzip, json
from collections import defaultdict

seen = defaultdict(list)          # surface code -> list of lap_dists where it appeared
with gzip.open("tape_60hz_clean.jsonl.gz", "rt") as f:
    for line in f:
        frame = json.loads(line)
        for s in frame["surface"]:
            if s != 0:
                seen[s].append(round(frame["lap_dist"]))

for val in sorted(seen):
    dists = sorted(set(seen[val]))
    print(f"surface={val}:  {len(seen[val])} frames,  sample dists: {dists[:15]}")