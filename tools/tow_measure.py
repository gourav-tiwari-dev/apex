"""How strong is the tow in LMU, from his own tapes? At the end of each long straight, his top
speed against the same-point gap to the car ahead, compared per straight with clean air."""

import io, contextlib, os, sys, statistics

os.chdir(r"C:\Users\gourav\Downloads\apex_telemetry")
sys.path.insert(0, os.getcwd())
from live_telemetry import ReplaySource
from race_state import same_class_neighbours
from gaps import TrackClock
from track_map import corners_for_track

TAPES = [
    "tape_20260924_201632.jsonl.gz",
    "tape_20260924_202800.jsonl.gz",
    "tape_20260923_200543.jsonl.gz",
    "tape_20260923_201109.jsonl.gz",
    "tape_20260923_201605.jsonl.gz",
]
rows = []  # (straight name, top speed, gap ahead at that moment)
for tape in TAPES:
    with contextlib.redirect_stdout(io.StringIO()):
        src = ReplaySource(None, tape)
        clock = TrackClock()
        corners = None
        straights = None
        best = {}  # straight -> (speed, gap) for the current pass
        for f in src:
            r = src.race
            if src.new_race and r is not None:
                clock.see_race(r, f.elapsed_time)
                if corners is None:
                    corners = sorted(
                        corners_for_track(r.session.track) or [],
                        key=lambda c: c["start"],
                    )
                    straights = []
                    for a, b in zip(corners, corners[1:]):
                        if b["start"] - a["end"] >= 1000:
                            straights.append(
                                (
                                    f"{a['name']} -> {b['name']}",
                                    b["start"] - 250,
                                    b["start"],
                                )
                            )
            clock.see_me(f.lap_dist, f.elapsed_time)
            if not straights or r is None or r.me is None or r.session.game_phase != 5:
                continue
            for name, lo, hi in straights:
                if lo <= f.lap_dist <= hi:
                    ahead, game_gap, _, _ = same_class_neighbours(r)
                    gap = (
                        clock.gap_ahead(ahead.id)
                        if ahead is not None
                        else None
                    )
                    if gap is None and ahead is not None:
                        gap = game_gap
                    top = best.get(name)
                    if top is None or f.speed_kmh > top[0]:
                        best[name] = (f.speed_kmh, gap if gap is not None else 99.0)
                elif name in best and f.lap_dist > hi:
                    rows.append((name,) + best.pop(name))
bands = [(0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 999)]
for name in sorted({r[0] for r in rows}):
    clean = [s for n, s, g in rows if n == name and g >= 2.0]
    if len(clean) < 2:
        continue
    base = statistics.median(clean)
    print(f"{name}: clean-air top speed {base:.1f} km/h ({len(clean)} passes)")
    for lo, hi in bands[:-1]:
        near = [s - base for n, s, g in rows if n == name and lo <= g < hi]
        if near:
            print(
                f"   {lo}-{hi} s behind: {statistics.median(near):+.1f} km/h  ({len(near)} passes)"
            )
