"""Record a session to a tape and nothing else: no voice, no LLM calls, no database.

    python record_race.py

Start it, then drive. It stops by itself when the session is over, or with Ctrl+C.
The tape it writes is what the v2 seats get built and tested on.
"""

from datetime import datetime

from sharedmemory import MMapControl
from lmu_data import LMUObjectOut, LMUConstants
from live_telemetry import LiveSource, Recorder

SESSION_OVER = 8  # mGamePhase: the session has finished
STATUS_EVERY_S = 10.0  # sim seconds between progress lines


def open_game():
    info = MMapControl(LMUConstants.LMU_SHARED_MEMORY_FILE, LMUObjectOut)
    info.create(0)
    return info


def record(info, tape_path):
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
                me = source.race.me
                opponents = len(source.race.opponents)
                matched = len([o for o in source.race.opponents if o.x is not None])
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
        print(f"Check it with:  python audit_tape.py {tape_path}")


if __name__ == "__main__":
    tape_path = f"tape_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl.gz"
    record(open_game(), tape_path)
