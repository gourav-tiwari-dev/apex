"""How many of the lines a replay said would play straight from the phrase bank.
Usage: bank_coverage.py REPLAY_DB [REPLAY_DB ...]   (from tools/replay_radio.py)"""

import collections
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from phrasebook import Phrasebook

# the lines that must be instant (v3: "everything immediate plays in about 0.01 s")
INSTANT = {
    "CLOSING_ALARM",
    "CLOSING_ON",
    "SLOW_CAR_AHEAD",
    "CAR_STOPPED_AHEAD",
    "THREE_WIDE_AHEAD",
    "FASTER_CLASS_BEHIND",
    "FASTER_FIGHT_BEHIND",
    "STICK_IT",
    "PASS_PRAISE",
    "PASS_RETAKEN",
    "PLACE_GIFT",
    "DEFEND_HELD",
    "FIGHT_COST",
}

args = sys.argv[1:]
books = {
    "spotter": Phrasebook("spotter"),
    "engineer": Phrasebook("engineer"),
}
hits, total, misses = collections.Counter(), collections.Counter(), []
for db in args:
    conn = sqlite3.connect(db)
    session = conn.execute("select max(session_id) from radio_log").fetchone()[0]
    for seat, kind, line in conn.execute(
        "select seat, kind, line from radio_log where session_id=? and status='spoken'",
        (session,),
    ):
        book = books["spotter" if seat == "spotter" else "engineer"]
        total[kind] += 1
        if book.pieces_for(line) is not None:
            hits[kind] += 1
        elif kind in INSTANT:
            misses.append((kind, line))

for kind in sorted(total):
    mark = "*" if kind in INSTANT else " "
    print(f" {mark} {kind:20s} {hits[kind]:3d} / {total[kind]:3d}")
instant_total = sum(total[k] for k in INSTANT)
instant_hits = sum(hits[k] for k in INSTANT)
print(f"instant lines from the bank: {instant_hits} / {instant_total}")
for kind, line in misses:
    print(f"   MISS {kind}: {line}")
