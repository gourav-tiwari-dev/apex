"""Record a session to a tape and nothing else: no voice, no LLM calls, no database.

    python dev/record_race.py

Start it, then drive. It stops by itself when the session is over, or with Ctrl+C.
The tape it writes is what the v2 seats get built and tested on.
"""

import os
import sys

# run as `python dev/record_race.py` from the project folder: Apex's modules are one folder up
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from game.sharedmemory import MMapControl
from game.lmu_data import LMUObjectOut, LMUConstants
from game.tape import Recorder
from game.live_source import LiveSource
from game.constants import SESSION_OVER

STATUS_EVERY_S = 10.0  # sim seconds between progress lines


def open_game():
    info = MMapControl(LMUConstants.LMU_SHARED_MEMORY_FILE, LMUObjectOut)
    info.create(0)
    return info


def record(info, tape_path):
    """Every frame, race snapshot and near-cars line onto the tape, with a progress line every
    STATUS_EVERY_S of sim time, until the session is over (or Ctrl+C)."""
    source = LiveSource(info)
    recorder = Recorder(tape_path)
    frames = 0
    snapshots = 0
    near_lines = 0
    next_status = None
    print(f"Recording to {tape_path}")

    try:
        for frame in source:
            if source.new_race:
                recorder.record(source.race)
                snapshots += 1
            if source.near is not None:
                recorder.record(source.near)
                near_lines += 1
            recorder.record(frame)
            frames += 1

            if next_status is None:
                next_status = frame.elapsed_time
            if frame.elapsed_time >= next_status and source.race is not None:
                next_status = frame.elapsed_time + STATUS_EVERY_S
                print_status(source.race, frames, snapshots, near_lines)

            if (
                source.race is not None
                and source.race.session.game_phase == SESSION_OVER
            ):
                print("Session over - stopping.")
                break
    finally:
        recorder.stop()
        print(
            f"Saved {frames} frames, {snapshots} race snapshots, {near_lines} near lines to {tape_path}"
        )
        print(f"Check it with:  python dev/audit_tape.py {tape_path}")


def print_status(race, frames, snapshots, near_lines):
    """One progress line: what is on the tape so far, his place and laps, and how many
    opponents come with telemetry (none: he is in the monitor view, not in the car)."""
    me = race.me
    opponents = len(race.opponents)
    matched = len([o for o in race.opponents if o.x is not None])
    place = me.place if me else "?"
    laps = me.laps if me else "?"
    print(
        f"  frames {frames}  snapshots {snapshots}  near lines {near_lines}  "
        f"P{place}  laps {laps}  opponents {matched}/{opponents} with telemetry"
    )
    if opponents and matched == 0:
        print(
            "  ! no opponent telemetry - are you in the monitor view? Get in the car."
        )


if __name__ == "__main__":
    tape_path = f"tape_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl.gz"
    record(open_game(), tape_path)
