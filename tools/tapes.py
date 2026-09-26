"""Reading race tapes for studies: every scoring snapshot, in order, tolerant of a tape that was
cut off mid-write (Apex stopped, a crash). Race snapshots only: the near-car frames are skipped."""
import json
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from race_state import race_snapshot_from_dict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the tapes of whole races (24 Sep 201632 is the start of 202800's race)
RACE_TAPES = ["tape_20260923_201605.jsonl.gz", "tape_20260924_202800.jsonl.gz",
              "tape_20260925_123131.jsonl.gz", "tape_20260925_144141.jsonl.gz",
              # 25 Sep night: standing start + formation lap, timed race, the leader took the flag first
              "tape_20260925_235830.jsonl.gz",
              # added 27 Sep, they had been left out: the 58-car multiclass race (18 Hyper, 19 LMP2,
              # 25 GT3; he crashed out at Indianapolis, no flag) and the warning-lobby race (joined
              # late, alone on track, P13 at the flag)
              "tape_20260925_200154.jsonl.gz", "tape_20260925_223404.jsonl.gz"]


def snapshots(tape):
    """RaceSnapshot after RaceSnapshot, as recorded."""
    path = tape if os.path.isabs(tape) else os.path.join(HERE, tape)
    raw = open(path, "rb").read()
    text = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw).decode("utf8", "ignore")
    for line in text.splitlines():
        if '"opponents"' not in line[:4000]:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue                          # the last line of a cut-off tape
        if d.get("me") is None:
            continue
        yield race_snapshot_from_dict(d)
