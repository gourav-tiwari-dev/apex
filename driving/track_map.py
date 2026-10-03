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
    {"name": "T1 Rettifilo", "start": 747, "end": 1088},
    {"name": "T3 Curva Grande", "start": 1250, "end": 1760},
    {"name": "T4 Roggia", "start": 1974, "end": 2321},
    {"name": "T6 Lesmo 1", "start": 2431, "end": 2742},
    {"name": "T7 Lesmo 2", "start": 2768, "end": 2998},
    {"name": "T8 Ascari", "start": 3795, "end": 4318},
    {"name": "T11 Parabolica", "start": 4992, "end": 5584},
]

MAPS_FOLDER = "track_maps"

SLICE_M = 5  # track is judged in 5 m slices
BRAKING = 0.05  # any brake pressure at all
TURNING = 6.0  # 0.6 g lateral, in m/s^2
CORE_SHARE = 0.5  # a corner's core: busy on at least half the laps
EDGE_SHARE = 0.35  # its edges: busy on at least a third of the laps
MERGE_GAP_M = 40  # busy stretches closer than this are one corner
MIN_CORNER_M = 40  # anything shorter is a wobble, not a corner
PAD_M = 25
MIN_LAPS = 3  # never learn a track from fewer laps than this
BRAKE_HELD_M = 15  # a braking zone: the brake held at least this far (shorter is a dab)


def corner_at(corners, distance):
    """The name of the corner at this distance into the lap, or None (a straight, or no
    map)."""
    if corners is None:
        return None
    for corner in corners:
        if corner["start"] <= distance < corner["end"]:
            return corner["name"]
    return None


class CornerMap:
    """The corners of the track being driven, shared by the session and everything that names a
    corner (the detectors, the corner stats). It starts on the Monza map, because old tapes carry
    no track name; the session swaps in the track's own map once it knows the track, or the map it
    learns from his laps (None while there is nothing to learn from yet)."""

    def __init__(self, corners=MONZA_CORNERS):
        self.corners = corners

    def at(self, distance):
        """The corner at this lap distance, or None (a straight, or no map yet)."""
        return corner_at(self.corners, distance)


def is_busy(brake, accel_lat):
    """Braking at all, or turning at 0.6 g or more: this slice is part of a corner."""
    return brake > BRAKING or abs(accel_lat) >= TURNING


class TrackMapLearner:
    """Watches laps as they are driven and can produce a corner map at any time."""

    def __init__(self):
        self.busy_by_lap = {}  # lap -> set of busy slice numbers
        self.braking_by_lap = {}  # lap -> set of slice numbers with the brake on
        self.track_length = 0.0

    def add(self, lap_count, distance, brake, accel_lat):
        """Marks this slice busy (and braking) on this lap; lap 0, the out-lap, is
        skipped."""
        # lap 0 is the out-lap fragment, at pit-limiter speed: it would teach wrong corners
        if lap_count < 1 or distance < 0:
            return
        if distance > self.track_length:
            self.track_length = distance
        if lap_count not in self.busy_by_lap:
            self.busy_by_lap[lap_count] = set()
            self.braking_by_lap[lap_count] = set()
        if is_busy(brake, accel_lat):
            self.busy_by_lap[lap_count].add(int(distance // SLICE_M))
        if brake > BRAKING:
            self.braking_by_lap[lap_count].add(int(distance // SLICE_M))

    def complete_laps(self, current_lap):
        """The laps already finished; the one being driven is not."""
        # the lap being driven right now is not complete yet
        return [lap for lap in self.busy_by_lap if lap < current_lap]

    def corners(self, current_lap):
        """The corner map learned from his complete laps so far, or None before MIN_LAPS."""
        laps = self.complete_laps(current_lap)
        if len(laps) < MIN_LAPS:
            return None
        share = self.busy_share(laps)
        windows = corner_windows(busy_cores(share), share)
        windows = split_at_braking(windows, self.share_of(self.braking_by_lap, laps))
        corners = []
        for number, (start, end) in enumerate(windows, start=1):
            corners.append({"name": f"Turn {number}", "start": start, "end": end})
        return corners

    def busy_share(self, laps):
        """For every 5 m slice of the track, the share of these laps it was busy on."""
        return self.share_of(self.busy_by_lap, laps)

    def share_of(self, slices_by_lap, laps):
        """For every 5 m slice of the track, the share of these laps it was marked on."""
        slices = int(self.track_length // SLICE_M) + 1
        share = []
        for s in range(slices):
            marked_laps = 0
            for lap in laps:
                if s in slices_by_lap.get(lap, ()):
                    marked_laps += 1
            share.append(marked_laps / len(laps))
        return share


def busy_cores(share):
    """The cores: slices busy on at least half the laps, joined across small gaps. Each is a
    [first slice, last slice] pair."""
    cores = []
    current = None
    for s in range(len(share)):
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
    return cores


def corner_windows(cores, share):
    """Each core long enough to be a corner, widened to where EDGE_SHARE of the laps were busy
    and padded by PAD_M; two that touch after padding are one corner (e.g. T1's exit).
    [start, end] in metres."""
    slices = len(share)
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
        if windows and start <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])
    return windows


def braking_starts(brake_share):
    """Slices where a real braking zone begins: the brake on for at least half the laps, held
    for BRAKE_HELD_M or more (a dab of the brake is not a corner)."""
    starts = []
    held = int(BRAKE_HELD_M // SLICE_M)
    for s in range(1, len(brake_share) - held):
        if brake_share[s - 1] >= CORE_SHARE or brake_share[s] < CORE_SHARE:
            continue
        if all(brake_share[s + k] >= CORE_SHARE for k in range(held)):
            starts.append(s)
    return starts


def split_at_braking(windows, brake_share):
    """Every braking zone starts a new corner, as a driver counts them (1 Oct 2026). On a twisty
    track (Portimao) he is over 0.6 g nearly all the way round, so 'busy' never stops and
    corners ran together: one learned 'corner' was 1.3 km long. A window is cut PAD_M before
    each braking zone that is not its first, if both pieces are still MIN_CORNER_M long."""
    cuts = [s * SLICE_M - PAD_M for s in braking_starts(brake_share)]
    split = []
    for start, end in windows:
        piece_start = start
        for cut in cuts:
            if piece_start + MIN_CORNER_M <= cut <= end - MIN_CORNER_M:
                split.append([piece_start, cut])
                piece_start = cut
        split.append([piece_start, end])
    return split


def overlap(a, b):
    """Metres two corners share."""
    return max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))


def borrow_names(learned, named):
    """Give learned corners the real names where they sit on a named corner."""
    for corner in learned:
        for known in named:
            shorter = min(
                corner["end"] - corner["start"], known["end"] - known["start"]
            )
            if overlap(corner, known) >= 0.5 * shorter:
                corner["name"] = known["name"]
                break
    return learned


def map_path(track):
    """Where a track's learned map is saved: track_maps/<the track as a slug>.json."""
    slug = re.sub(r"[^a-z0-9]+", "_", track.lower()).strip("_") or "unknown_track"
    return os.path.join(MAPS_FOLDER, slug + ".json")


def save_map(track, corners, laps_used):
    """Saves a learned corner map for that track, with the laps it was learned from."""
    os.makedirs(MAPS_FOLDER, exist_ok=True)
    with open(map_path(track), "w") as f:
        json.dump(
            {"track": track, "learned_from_laps": laps_used, "corners": corners},
            f,
            indent=1,
        )


def load_map(track):
    """The saved corner map for that track, or None."""
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
