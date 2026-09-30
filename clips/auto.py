"""Auto-clips (feature 11, 30 Sep): every race ends with a ready vertical short of its best radio.

apex.py --clips starts one AutoClips per LMU session. It records the game window while the
session runs; when a race finishes, the moment finder lines the recording up with this session's
radio log and the short is built with the "radio by Apex" ending, in Videos/Apex. Recordings of
practice and qualifying are Apex's own scratch files and are deleted; the race recording is kept
next to its short, because a full race is what longer videos are made from.
"""

import os
import shutil

from clips.find_moments import find
from clips.make_short import make
from clips.recorder import RaceRecorder

CLIPS_DIR = os.path.join(os.path.expanduser("~"), "Videos", "Apex")


class AutoClips:
    def __init__(self, db_path, out_dir=CLIPS_DIR):
        self.db_path = db_path
        self.out_dir = out_dir
        self.recorder = None

    def session_starting(self):
        self.recorder = RaceRecorder(self.out_dir)
        if not self.recorder.start():
            self.recorder = None

    def session_over(self, session_id, was_a_finished_race):
        """Stops recording; after a finished race, builds the short. Returns its path or None."""
        if self.recorder is None:
            return None
        race = self.recorder.stop()
        folder = self.recorder.piece_dir
        self.recorder = None
        if not was_a_finished_race:
            shutil.rmtree(folder, ignore_errors=True)
            return None
        if race is None:
            return None
        print("[clips] finding the radio in the recording...")
        moments = find(race, self.db_path, session=session_id)
        short = make(race, moments, os.path.join(folder, "short.mp4"), ending="driver")
        if short:
            print(f"[clips] your short is ready: {short}")
        return short
