"""Spins and caught slides, checked by a second way of seeing them (26 Sep).

The detectors (driving/detectors.py: SpinDetector, SlideCaughtDetector) compare where the car points
(its orientation matrix) with where it is going (its position 0.1 s ago). This check uses
neither of those pairings: it adds up the yaw rate to see how far the BODY turned over 3 s, and
compares that with how far the PATH turned over the same 3 s. Body turned 90 degrees more than
the path: it spun. The sign of the yaw rate is calibrated against the path in normal corners.

    python tools/spin_truth.py [tape ...]     (default: every tape of 23-25 Sep)
Done-check: every SPIN has a body-against-path turn over 75 degrees, every SLIDE_CAUGHT a peak
between 10 and 90 degrees, and no turn over 90 degrees above 20 km/h goes without a SPIN."""

import collections
import glob
import gzip
import json
import math
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from game.car_frame import CarState
from driving.detectors import SpinDetector, SlideCaughtDetector

WINDOW_S = 3.0
SPIN_CONFIRMED = 75.0  # the detector says > 90; 15 degrees for yaw-rate adding-up error
SLIDE_LOW, SLIDE_HIGH = 10.0, 90.0
MISSED_SPIN = 90.0
MISSED_MIN_KMH = 20.0


def frames_of(tape):
    frames = []
    try:
        with gzip.open(tape, "rt") as f:
            for line in f:
                d = json.loads(line)
                if d.get("t") in ("race", "near"):
                    continue
                frames.append(CarState(**d))
    except (EOFError, zlib.error, json.JSONDecodeError):
        pass  # a tape cut off mid-write
    return frames


TELEPORT_M = (
    30.0  # further than this in 0.1 s is the game moving the car (garage to grid)
)


def path_headings(frames):
    """(direction of travel in degrees, unwrapped; stretch number) per frame. The heading is None
    while barely moving. A teleport starts a new stretch: 25 Sep 12:31 the car jumped 2.2 km from
    the garage to the grid and a window across it read as a 161 degree spin (26 Sep)."""
    headings = []
    last = None
    stretch = 0
    lag = 0
    for i, frame in enumerate(frames):
        while lag < i and frames[i].elapsed_time - frames[lag].elapsed_time > 0.1:
            lag += 1
        before = frames[lag - 1] if lag > 0 else None
        heading = None
        if frame.pos is not None and before is not None and before.pos is not None:
            dx = frame.pos[0] - before.pos[0]
            dz = frame.pos[2] - before.pos[2]
            moved = math.hypot(dx, dz)
            if moved > TELEPORT_M:
                stretch += 1
                last = None
            elif moved >= 0.2:
                raw = math.degrees(math.atan2(dx, dz))
                if last is None:
                    heading = raw
                else:
                    step = (raw - last + 180.0) % 360.0 - 180.0
                    heading = last + step
                last = heading
        headings.append((heading, stretch))
    return headings


def yaw_sign(frames, headings):
    """+1 or -1: which way the yaw rate turns, from normal corners (path and body turn together)."""
    agree = 0.0
    for i in range(1, len(frames)):
        heading, stretch = headings[i]
        before, before_stretch = headings[i - 1]
        if (
            heading is None
            or before is None
            or stretch != before_stretch
            or frames[i].speed_kmh < 60
        ):
            continue
        dt = frames[i].elapsed_time - frames[i - 1].elapsed_time
        if dt <= 0:
            continue
        path_rate = math.radians(heading - before) / dt
        agree += path_rate * frames[i].yaw_rate
    return 1.0 if agree >= 0 else -1.0


def body_against_path(frames, headings, sign):
    """Per frame: degrees the body turned more than the path over the last 3 s (None if unknown)."""
    body = [0.0]
    for i in range(1, len(frames)):
        dt = frames[i].elapsed_time - frames[i - 1].elapsed_time
        if dt < 0 or dt > 0.5:
            dt = 0.0  # a gap in the tape: do not add up across it
        body.append(body[-1] + sign * math.degrees(frames[i].yaw_rate) * dt)
    turned = []
    start = 0
    for i, frame in enumerate(frames):
        while frame.elapsed_time - frames[start].elapsed_time > WINDOW_S:
            start += 1
        heading, stretch = headings[i]
        start_heading, start_stretch = headings[start]
        if heading is None or start_heading is None or stretch != start_stretch:
            turned.append(None)  # never across a teleport
            continue
        turned.append((body[i] - body[start]) - (heading - start_heading))
    return turned


def biggest(turned, frames, t_from, t_to):
    worst = None
    for value, frame in zip(turned, frames):
        if value is None or not (t_from <= frame.elapsed_time <= t_to):
            continue
        if worst is None or abs(value) > abs(worst):
            worst = value
    return worst


def main():
    tapes = sys.argv[1:] or sorted(glob.glob("tape_2026092[3-5]_*.jsonl.gz"))
    problems = []
    checked = collections.Counter()
    for tape in tapes:
        frames = frames_of(tape)
        if len(frames) < 100 or frames[0].pos is None:
            continue  # old tapes: no position, nothing to check with
        spin, slide = SpinDetector(), SlideCaughtDetector()
        detections = []
        for frame in frames:
            for detector in (spin, slide):
                event = detector.update(frame)
                if event is not None:
                    detections.append((event.sim_time, event.kind))
        headings = path_headings(frames)
        sign = yaw_sign(frames, headings)
        turned = body_against_path(frames, headings, sign)
        lines = []
        for when, kind in detections:
            checked[kind] += 1
            if kind == "SPIN":
                worst = biggest(turned, frames, when - 1.0, when + 3.0)
                ok = worst is not None and abs(worst) > SPIN_CONFIRMED
            else:
                worst = biggest(turned, frames, when - 3.0, when)
                ok = worst is not None and SLIDE_LOW <= abs(worst) <= SLIDE_HIGH
            shown = "?" if worst is None else f"{worst:+.0f}"
            lines.append(
                f"   {when:7.1f} {kind:12s} body against path {shown:>5s} deg  {'ok' if ok else 'NOT BACKED'}"
            )
            if not ok:
                problems.append(f"{tape} {when:.1f}s {kind} ({shown} deg)")
        # spins the detector did not see
        spin_times = [when for when, kind in detections if kind == "SPIN"]
        last_missed = None
        for value, frame in zip(turned, frames):
            if (
                value is None
                or abs(value) <= MISSED_SPIN
                or frame.speed_kmh < MISSED_MIN_KMH
            ):
                continue
            if any(abs(frame.elapsed_time - when) <= 10.0 for when in spin_times):
                continue
            if last_missed is not None and frame.elapsed_time - last_missed < 10.0:
                continue
            last_missed = frame.elapsed_time
            lines.append(
                f"   {frame.elapsed_time:7.1f} MISSED SPIN? body against path {value:+.0f} deg at {frame.speed_kmh:.0f} km/h"
            )
            problems.append(
                f"{tape} {frame.elapsed_time:.1f}s missed spin ({value:+.0f} deg)"
            )
        if lines:
            print(f"{tape}: yaw sign {sign:+.0f}")
            for line in lines:
                print(line)
    print(
        f"checked {checked['SPIN']} spins and {checked['SLIDE_CAUGHT']} caught slides"
    )
    print("PASS" if not problems else "FAIL:\n  " + "\n  ".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
