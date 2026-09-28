"""Tapes: every frame of a session, written down live and played back later.

A tape is a gzipped file of JSON lines, one per car frame (a CarState), with a race snapshot line
and a near-cars line written right before the frame they belong to. Recorder writes one while he
drives; ReplaySource plays one back as if it were the game, in real time or as fast as it goes."""

import gzip
import json
import threading
import time
import zlib
from dataclasses import asdict
from queue import Queue

from game.car_frame import CarState
from game.race_snapshot import near_cars_from_dict, race_snapshot_from_dict


TAPE_PATH = "tape_60hz_clean.jsonl.gz"


class ReplaySource:
    def __init__(self, speed, tape_path=TAPE_PATH):
        self.speed = speed
        self.tape_path = tape_path
        self.race = None
        self.new_race = False
        self.near = None
        print("Connected.")
        print("Press Ctrl+C to stop.\n")

    def __iter__(self):
        try:
            with gzip.open(self.tape_path, "rt") as f:
                start_wall = time.perf_counter()
                start_sim = None
                for line in f:
                    as_dict = json.loads(line)
                    # v2 tapes interleave race lines with the car frames; each one belongs
                    # to the car frame written right after it. Old tapes have only car frames.
                    kind = as_dict.get("t")
                    if kind == "race":
                        self.race = race_snapshot_from_dict(as_dict)
                        self.new_race = True
                        continue
                    if kind == "near":
                        self.near = near_cars_from_dict(as_dict)
                        continue
                    as_data = CarState(**as_dict)
                    if start_sim is None:
                        start_sim = as_data.elapsed_time
                    if self.speed:
                        target = (
                            start_wall + (as_data.elapsed_time - start_sim) / self.speed
                        )
                        delay = target - time.perf_counter()
                        # running late: never skip the frame, just don't wait for it
                        if delay > 0:
                            time.sleep(delay)
                    yield as_data
                    # a snapshot is "new" for one frame only, and near cars belong to one frame
                    self.new_race = False
                    self.near = None

        except (EOFError, zlib.error):
            # a tape cut off when Apex was killed (24 Sep): everything up to the cut is real
            print("[the tape ends early - it was cut off; replayed up to the cut]")
        except KeyboardInterrupt:
            print("\nStopping...")
            print("Closed connection.")


class Recorder:
    def __init__(self, path):
        # path is unique per session - a hardcoded name silently overwrote the
        # previous session's tape every run.
        self.path = path
        self.q = Queue()
        self.writer_thread = threading.Thread(target=self._writer_loop)
        self.writer_thread.start()

    def record(self, frame):
        self.q.put(frame)

    def _writer_loop(self):
        written = 0
        with gzip.open(self.path, "wt") as f:
            while True:
                item = self.q.get()
                if item is None:
                    break

                as_dict = asdict(item)
                as_text = json.dumps(as_dict)
                f.write(as_text + "\n")
                written += 1
                # every few seconds, a sync point: if Apex is killed, everything up to here
                # still reads back (24 Sep: a force-stopped run left a tape cut mid-write)
                if written % 600 == 0:
                    f.flush()

    def stop(self):
        self.q.put(None)
        self.writer_thread.join()
