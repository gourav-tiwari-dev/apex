"""Where the corners are, on any track. The one place in Apex that decides it.

Monza: Gourav's hand-measured windows, which carry the real corner names.
Any other track is learned from my own laps:
  - the track is cut into 5 m slices
  - a slice is "busy" on a lap when I was braking or turning hard (lateral >= 0.6 g) there.
    A lift alone does not count: on a straight that is traffic, not a corner.
  - a corner = slices busy on at least half of the laps
  - each corner widens to where a fifth of the laps were busy (so an early braker's brake
    point still lands inside it), plus 25 m either side, the same rule the Monza windows
    were measured with by hand
Checked on the 11-lap Monza tape (23 Sep 2026): all 7 hand corners found with no fake
ones from only 3 laps, and from 5 and 11. With lifts counted, or at 0.5 g, warm-up lifts and
the Serraglio kink turned into fake corners until about 11 laps.
"""
import json
import os
import re

# windows measured from my own laps (tape 20260821, laps 1-11):
# start = earliest braking - 25 m, end = back to full throttle and straight (lateral g < 0.5) + 25 m
# Curva Grande is taken flat, so it keeps its old hand-picked window
MONZA_CORNERS = [
    {"name": "T1 Rettifilo",   "start":  747, "end": 1088},
    {"name": "T3 Curva Grande","start": 1250, "end": 1760},
    {"name": "T4 Roggia",      "start": 1974, "end": 2321},
    {"name": "T6 Lesmo 1",     "start": 2431, "end": 2742},
    {"name": "T7 Lesmo 2",     "start": 2768, "end": 2998},
    {"name": "T8 Ascari",      "start": 3795, "end": 4318},
    {"name": "T11 Parabolica", "start": 4992, "end": 5584},
]

MAPS_FOLDER = "track_maps"

SLICE_M = 5              # track is judged in 5 m slices
BRAKING = 0.05           # any brake pressure at all
TURNING = 6.0            # 0.6 g lateral, in m/s^2
CORE_SHARE = 0.5         # a corner's core: busy on at least half the laps
EDGE_SHARE = 0.35        # its edges: busy on at least a third of the laps
MERGE_GAP_M = 40         # busy stretches closer than this are one corner
MIN_CORNER_M = 40        # anything shorter is a wobble, not a corner
PAD_M = 25
MIN_LAPS = 3             # never learn a track from fewer laps than this


def corner_at(corners, distance):
    if corners is None:
        return None
    for corner in corners:
        if corner["start"] <= distance < corner["end"]:
            return corner["name"]
    return None


def is_busy(brake, throttle, accel_lat):
    return brake > BRAKING or abs(accel_lat) >= TURNING


class TrackMapLearner:
    """Watches laps as they are driven and can produce a corner map at any time."""

    def __init__(self):
        self.busy_by_lap = {}      # lap -> set of busy slice numbers
        self.track_length = 0.0

    def add(self, lap_count, distance, brake, throttle, accel_lat):
        # lap 0 is the out-lap fragment, at pit-limiter speed: it would teach wrong corners
        if lap_count < 1 or distance < 0:
            return
        if distance > self.track_length:
            self.track_length = distance
        if lap_count not in self.busy_by_lap:
            self.busy_by_lap[lap_count] = set()
        if is_busy(brake, throttle, accel_lat):
            self.busy_by_lap[lap_count].add(int(distance // SLICE_M))

    def complete_laps(self, current_lap):
        # the lap being driven right now is not complete yet
        return [lap for lap in self.busy_by_lap if lap < current_lap]

    def corners(self, current_lap):
        laps = self.complete_laps(current_lap)
        if len(laps) < MIN_LAPS:
            return None
        slices = int(self.track_length // SLICE_M) + 1
        share = []
        for s in range(slices):
            busy_laps = 0
            for lap in laps:
                if s in self.busy_by_lap[lap]:
                    busy_laps += 1
            share.append(busy_laps / len(laps))

        # the cores: slices busy on at least half the laps, joined across small gaps
        cores = []
        current = None
        for s in range(slices):
            if share[s] < CORE_SHARE:
                continue
            if current is not None and (s - current[1]) * SLICE_M <= MERGE_GAP_M:
                current[1] = s
            else:
                if current is not None:
                    cores.append(current)
                current = [s, s]
        if current is not None:
            cores.append(current)

        windows = []
        for first, last in cores:
            if (last - first) * SLICE_M < MIN_CORNER_M:
                continue
            while first > 0 and share[first - 1] >= EDGE_SHARE:
                first -= 1
            while last < slices - 1 and share[last + 1] >= EDGE_SHARE:
                last += 1
            start = first * SLICE_M - PAD_M
            end = (last + 1) * SLICE_M + PAD_M
            # two corners that touch after padding are one corner (e.g. T1's exit)
            if windows and start <= windows[-1][1]:
                windows[-1][1] = max(windows[-1][1], end)
            else:
                windows.append([start, end])

        corners = []
        for number, (start, end) in enumerate(windows, start=1):
            corners.append({"name": f"Turn {number}", "start": start, "end": end})
        return corners


def overlap(a, b):
    return max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))


def borrow_names(learned, named):
    """Give learned corners the real names where they sit on a named corner."""
    for corner in learned:
        for known in named:
            shorter = min(corner["end"] - corner["start"], known["end"] - known["start"])
            if overlap(corner, known) >= 0.5 * shorter:
                corner["name"] = known["name"]
                break
    return learned


def map_path(track):
    slug = re.sub(r"[^a-z0-9]+", "_", track.lower()).strip("_") or "unknown_track"
    return os.path.join(MAPS_FOLDER, slug + ".json")


def save_map(track, corners, laps_used):
    os.makedirs(MAPS_FOLDER, exist_ok=True)
    with open(map_path(track), "w") as f:
        json.dump({"track": track, "learned_from_laps": laps_used, "corners": corners}, f, indent=1)


def load_map(track):
    path = map_path(track)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)["corners"]


def corners_for_track(track):
    """The corner map to use for a track, or None when it still has to be learned."""
    saved = load_map(track)
    if saved is not None:
        return saved
    # GUESSED: LMU's track name for the full Monza layout contains "monza".
    # Check it on the first v2 tape: there is also a shorter Monza layout.
    if "monza" in track.lower():
        return MONZA_CORNERS
    return None
