"""The behaviour lock for the refactor (27 Sep 2026): proves a change did not change what Apex says.

    python tools/behaviour_lock.py record     replay every race tape, save what the radio did
    python tools/behaviour_lock.py check      replay again and compare with what was saved

What is saved per tape (golden/<tape>.json): every radio_log row of the replay (time, seat, kind,
priority, status, reason, the words, the facts) and the session's event hash (every detector
event). The row ids and the speaking latency are left out: they are not decisions.

Each tape replays in its own process, so no module state leaks from one tape to the next, and all
of them run at once. They all start from the same frozen copy of apex.db (golden/apex_golden.db),
taken at record time, so the races he drives later cannot move the baseline.

A structure-only commit must print IDENTICAL for every tape. A labelled behaviour fix is expected to
show differences: they are listed, then recorded again as the new baseline."""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
from tapes import RACE_TAPES

GOLDEN = os.path.join(HERE, "golden")
GOLDEN_DB = os.path.join(GOLDEN, "apex_golden.db")
COLUMNS = "sim_time, seat, kind, priority, urgent, status, reason, coalesce(line, ''), conclusion, facts, evidence"
SHOWN_DIFFERENCES = 5


class NoModel:
    """No language model in a replay: every line comes from code."""
    clean = False

    def online(self):
        return False

    def phrase(self, call):
        return None, 0, 0, 0.0


def replay_one(tape):
    """What the radio did on one tape: (radio rows, event hash)."""
    import live_telemetry
    import memory
    database = os.path.join(tempfile.gettempdir(), f"apex_lock_{os.path.basename(tape)}.db")
    shutil.copy(GOLDEN_DB, database)
    live_telemetry.connect_db = lambda db_path=None: memory.connect_db(database)
    with contextlib.redirect_stdout(io.StringIO()):
        session_id = live_telemetry.run_session(True, None, tape, out_loud=False, persona=NoModel())
    connection = sqlite3.connect(database)
    rows = connection.execute(f"select {COLUMNS} from radio_log where session_id=? order by sim_time, id",
                              (session_id,)).fetchall()
    event_hash = connection.execute("select event_hash from sessions where id=?", (session_id,)).fetchone()[0]
    connection.close()
    return [list(row) for row in rows], event_hash


def replay_all():
    """Every race tape at once, each in its own process: tape -> (rows, event hash)."""
    workers = []
    for tape in RACE_TAPES:
        output = os.path.join(tempfile.gettempdir(), f"apex_lock_{tape}.json")
        process = subprocess.Popen([sys.executable, __file__, "one", tape, output])
        workers.append((tape, output, process))
    results = {}
    for tape, output, process in workers:
        process.wait()
        if process.returncode != 0 or not os.path.exists(output):
            results[tape] = None
            continue
        with open(output, encoding="utf-8") as f:
            saved = json.load(f)
        results[tape] = (saved["rows"], saved["event_hash"])
    return results


def record():
    os.makedirs(GOLDEN, exist_ok=True)
    if not os.path.exists(GOLDEN_DB):
        shutil.copy(os.path.join(HERE, "apex.db"), GOLDEN_DB)
    for tape, result in replay_all().items():
        if result is None:
            print(f"{tape}: REPLAY FAILED, nothing recorded")
            continue
        rows, event_hash = result
        with open(os.path.join(GOLDEN, tape + ".json"), "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "event_hash": event_hash}, f)
        print(f"{tape}: recorded {len(rows)} radio rows")
    return 0


def check():
    failures = 0
    for tape, result in replay_all().items():
        golden_file = os.path.join(GOLDEN, tape + ".json")
        if result is None:
            print(f"{tape}: REPLAY FAILED")
            failures += 1
            continue
        if not os.path.exists(golden_file):
            print(f"{tape}: no baseline yet (run record)")
            failures += 1
            continue
        with open(golden_file, encoding="utf-8") as f:
            golden = json.load(f)
        rows, event_hash = result
        rows = json.loads(json.dumps(rows))             # the same types as the saved ones
        if rows == golden["rows"] and event_hash == golden["event_hash"]:
            print(f"{tape}: IDENTICAL ({len(rows)} radio rows)")
            continue
        failures += 1
        print(f"{tape}: CHANGED")
        if event_hash != golden["event_hash"]:
            print("   detector events changed (event hash)")
        before = {json.dumps(row) for row in golden["rows"]}
        after = {json.dumps(row) for row in rows}
        gone = [row for row in golden["rows"] if json.dumps(row) not in after]
        new = [row for row in rows if json.dumps(row) not in before]
        for row in gone[:SHOWN_DIFFERENCES]:
            print(f"   - {row[0]:7.1f} {row[2]:16s} {row[5]:8s} {row[7][:90]!r}")
        for row in new[:SHOWN_DIFFERENCES]:
            print(f"   + {row[0]:7.1f} {row[2]:16s} {row[5]:8s} {row[7][:90]!r}")
        print(f"   {len(gone)} rows gone, {len(new)} rows new")
    print("LOCK HOLDS" if failures == 0 else f"LOCK BROKEN on {failures} tapes")
    return 0 if failures == 0 else 1


def one(tape, output):
    rows, event_hash = replay_one(tape)
    with open(output, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "event_hash": event_hash}, f)
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "record":
        sys.exit(record())
    if command == "one":
        sys.exit(one(sys.argv[2], sys.argv[3]))
    sys.exit(check())
