"""Spins and caught slides on every tape: the slip-angle detectors (26 Sep) against the old
yaw-rate rule, so a change to the thresholds shows its false alarms at once.

    python tools/spin_check.py [tape ...]
"""

import glob
import gzip
import json
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_telemetry import CarState, SpinDetector, SlideCaughtDetector
from track_map import CornerMap, corners_for_track

tapes = sys.argv[1:] or sorted(glob.glob("tape_2026092[3-5]_*.jsonl.gz"))
for tape in tapes:
    corner_map = CornerMap()
    spin, slide = SpinDetector(corner_map), SlideCaughtDetector(corner_map)
    old_spins = 0
    old_armed = False
    found = []
    track_known = False
    try:
        with gzip.open(tape, "rt") as f:
            for line in f:
                d = json.loads(line)
                if d.get("t") == "race" and not track_known:
                    # the tape's own track, as the live loop does: without it every tape was named
                    # with Monza's corners ("T3 Curva Grande" at Le Mans, 26 Sep)
                    track_known = True
                    corners = corners_for_track(d["session"]["track"])
                    if corners is not None:
                        corner_map.corners = corners
                if d.get("t") in ("race", "near"):
                    continue
                frame = CarState(**d)
                for detector in (spin, slide):
                    event = detector.update(frame)
                    if event is not None:
                        found.append(
                            f"{event.sim_time:7.1f} {event.kind:12s} {event.conclusion}"
                        )
                fast = abs(frame.yaw_rate) > 1.7
                if fast and not old_armed:
                    old_spins += 1
                old_armed = fast
    except (EOFError, zlib.error, json.JSONDecodeError):
        pass
    print(f"{tape}: old yaw rule {old_spins} spins")
    for row in found:
        print("   ", row)
