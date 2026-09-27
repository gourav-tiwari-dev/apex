"""The behaviour lock for the refactor (27 Sep 2026): proves a change did not change what Apex does.

    python tools/behaviour_lock.py record     replay every race tape, save everything it wrote
    python tools/behaviour_lock.py check      replay again and compare with what was saved

What is saved per tape (golden/<tape>.json): every row the session wrote to the database - the
radio log (time, seat, kind, priority, status, reason, the words, the facts), the detector
events, corner stats, laps, rivals, opponents' corners, pass attempts, the session row itself -
and the governor's decision hash. Row ids, wall-clock times, the speaking latency and the tape's
path are left out: they are not decisions.

Each tape replays in its own process, so no module state leaks from one tape to the next, and all
of them run at once. They all start from the same frozen copy of apex.db (golden/apex_golden.db),
taken at record time, so the races he drives later cannot move the baseline.

APEX_TAPES (optional): the folder the tapes are in, when this runs from a checkout without them
(the tapes are not in git).

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
TAPES_FOLDER = os.environ.get("APEX_TAPES", HERE)
COLUMNS = "sim_time, seat, kind, priority, urgent, status, reason, coalesce(line, ''), conclusion, facts, evidence"
SESSION_TABLES = (
    "events",
    "corner_stats",
    "laps",
    "rivals_seen",
    "opponent_corners",
    "pass_attempts",
    "focus_contracts",
    "llm_calls",
)
NOT_COMPARED = {"id", "started_at", "ended_at", "latency_ms", "tape_path"}
SHOWN_DIFFERENCES = 5


class NoModel:
    """No language model in a replay: every line comes from code."""

    clean = False

    def online(self):
        return False

    def phrase(self, call):
        return None, 0, 0, 0.0


def compared_columns(connection, table):
    columns = []
    for row in connection.execute(f"pragma table_info({table})"):
        if row[1] not in NOT_COMPARED:
            columns.append(row[1])
    return ", ".join(columns)


def tables_written(connection, session_id):
    """Every row this session wrote, table by table, in the order it was written."""
    tables = {}
    session_columns = compared_columns(connection, "sessions")
    tables["sessions"] = connection.execute(
        f"select {session_columns} from sessions where id=?", (session_id,)
    ).fetchall()
    for table in SESSION_TABLES:
        columns = compared_columns(connection, table)
        tables[table] = connection.execute(
            f"select {columns} from {table} where session_id=? order by id",
            (session_id,),
        ).fetchall()
    as_lists = {}
    for table, rows in tables.items():
        as_lists[table] = [list(row) for row in rows]
    return as_lists


def replay_one(tape):
    """What one tape made Apex write: {radio rows, decision hash, every table's rows}."""
    import session
    import memory

    database = os.path.join(
        tempfile.gettempdir(), f"apex_lock_{os.path.basename(tape)}.db"
    )
    shutil.copy(GOLDEN_DB, database)
    session.connect_db = lambda db_path=None: memory.connect_db(database)
    with contextlib.redirect_stdout(io.StringIO()):
        session_id = session.run_session(
            True,
            None,
            os.path.join(TAPES_FOLDER, tape),
            out_loud=False,
            persona=NoModel(),
        )
    connection = sqlite3.connect(database)
    rows = connection.execute(
        f"select {COLUMNS} from radio_log where session_id=? order by sim_time, id",
        (session_id,),
    ).fetchall()
    event_hash = connection.execute(
        "select event_hash from sessions where id=?", (session_id,)
    ).fetchone()[0]
    tables = tables_written(connection, session_id)
    connection.close()
    return {
        "rows": [list(row) for row in rows],
        "event_hash": event_hash,
        "tables": tables,
    }


def replay_all():
    """Every race tape at once, each in its own process: tape -> what it wrote (None: failed)."""
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
            results[tape] = json.load(f)
    return results


def record():
    os.makedirs(GOLDEN, exist_ok=True)
    if not os.path.exists(GOLDEN_DB):
        shutil.copy(os.path.join(HERE, "apex.db"), GOLDEN_DB)
    for tape, result in replay_all().items():
        if result is None:
            print(f"{tape}: REPLAY FAILED, nothing recorded")
            continue
        with open(os.path.join(GOLDEN, tape + ".json"), "w", encoding="utf-8") as f:
            json.dump(result, f)
        written = sum(len(rows) for rows in result["tables"].values())
        print(f"{tape}: recorded {len(result['rows'])} radio rows, {written} other rows")
    return 0


def show_radio_difference(golden_rows, rows):
    before = {json.dumps(row) for row in golden_rows}
    after = {json.dumps(row) for row in rows}
    gone = [row for row in golden_rows if json.dumps(row) not in after]
    new = [row for row in rows if json.dumps(row) not in before]
    for row in gone[:SHOWN_DIFFERENCES]:
        print(f"   - {row[0]:7.1f} {row[2]:16s} {row[5]:8s} {row[7][:90]!r}")
    for row in new[:SHOWN_DIFFERENCES]:
        print(f"   + {row[0]:7.1f} {row[2]:16s} {row[5]:8s} {row[7][:90]!r}")
    print(f"   radio: {len(gone)} rows gone, {len(new)} rows new")


def show_table_difference(table, golden_rows, rows):
    before = [json.dumps(row) for row in golden_rows]
    after = [json.dumps(row) for row in rows]
    gone = [row for row in before if row not in after]
    new = [row for row in after if row not in before]
    print(f"   {table}: {len(golden_rows)} -> {len(rows)} rows, {len(gone)} gone, {len(new)} new")
    for row in gone[:SHOWN_DIFFERENCES]:
        print(f"      - {row[:150]}")
    for row in new[:SHOWN_DIFFERENCES]:
        print(f"      + {row[:150]}")
    if not gone and not new:
        print("      (the same rows in a different order)")


def compare(golden, result):
    """The list of things that differ, printing each; empty when identical."""
    differences = []
    rows = json.loads(json.dumps(result["rows"]))  # the same types as the saved ones
    if rows != golden["rows"]:
        differences.append("radio")
        show_radio_difference(golden["rows"], rows)
    if result["event_hash"] != golden["event_hash"]:
        differences.append("decision hash")
        print("   the governor's decisions changed (decision hash)")
    golden_tables = golden.get("tables", {})
    tables = json.loads(json.dumps(result["tables"]))
    for table, golden_rows in golden_tables.items():
        if tables.get(table) != golden_rows:
            differences.append(table)
            show_table_difference(table, golden_rows, tables.get(table, []))
    return differences


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
        if "tables" not in golden:
            print(f"{tape}: the baseline has no tables (recorded by the old lock): run record")
            failures += 1
            continue
        print(f"{tape}:")
        differences = compare(golden, result)
        if differences:
            failures += 1
            print(f"   CHANGED: {', '.join(differences)}")
            continue
        written = sum(len(rows) for rows in golden["tables"].values())
        print(f"   IDENTICAL ({len(golden['rows'])} radio rows, {written} other rows)")
    print("LOCK HOLDS" if failures == 0 else f"LOCK BROKEN on {failures} tapes")
    return 0 if failures == 0 else 1


def one(tape, output):
    result = replay_one(tape)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "record":
        sys.exit(record())
    if command == "one":
        sys.exit(one(sys.argv[2], sys.argv[3]))
    sys.exit(check())
