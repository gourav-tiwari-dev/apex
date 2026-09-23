"""Is Apex v2 done? The check that decides it (V2_DONE.md, locked by Gourav 23 Sep 2026).

    python done_check.py            check the latest race
    python done_check.py 42         check session 42

DONE = one full ranked race with Apex on, never switched off, and every one of the seven
seats made at least one real call. Plus the upgrades he accepted (D1-D5):
  D1 calls must be right: he rates every seat, and none may be "wrong"
  D2 any track: nothing here depends on the track
  D3 the whole ranked event: qualifying in the same launch
  D4 measured limits: LLM cost of the race at or under Rs 5
  D5 "quiet" never counts as switching it off (reported only)

A REAL call = actually spoken, triggered by data (not the radio check, not a fallback notice).
Debrief and brief lines count: that is where the setup engineer works.
"""
import json
import statistics
import sys
from datetime import datetime

from memory import connect_db

SEATS = ["race_engineer", "strategist", "spotter", "performance", "racecraft", "setup", "memory"]
NOT_REAL = {"RADIO_CHECK", "LLM_OFFLINE"}
BETWEEN_SESSIONS = {"setup"}
RACE = range(10, 14)
QUALIFYING = range(5, 9)
GREEN = 5
COST_CAP_RS = 5.0

CHECKS_TABLE = """
CREATE TABLE IF NOT EXISTS done_checks (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL,
    checked_at  TEXT    NOT NULL,
    verdict     TEXT    NOT NULL,
    details     TEXT    NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions (id)
);
"""


def latest_race(conn):
    row = conn.execute("SELECT MAX(id) FROM sessions WHERE session_type BETWEEN 10 AND 13").fetchone()
    return row[0]


def check(conn, session_id, answers):
    """answers = {"switched_off": bool, "ratings": {seat: "useful" | "noise" | "wrong"}}"""
    session_type, end_reason, first_phase, launch_id = conn.execute(
        "SELECT session_type, end_reason, first_phase, launch_id FROM sessions WHERE id = ?",
        (session_id,)).fetchone()

    problems = []
    if session_type not in RACE:
        problems.append("not a race session")
    if first_phase is None or first_phase >= GREEN:
        problems.append("Apex was not on before the green flag")
    if end_reason != "session_over":
        problems.append(f"the race did not run to the flag with Apex on (ended: {end_reason})")

    # the setup engineer works between sessions: its brief before the race counts too
    launch_sessions = [session_id]
    if launch_id is not None:
        launch_sessions = [row[0] for row in conn.execute("SELECT id FROM sessions WHERE launch_id = ?", (launch_id,))]
    seats = {}
    for seat in SEATS:
        sessions = launch_sessions if seat in BETWEEN_SESSIONS else [session_id]
        marks = ",".join("?" * len(sessions))
        rows = conn.execute(
            f"SELECT kind, line FROM radio_log WHERE session_id IN ({marks}) AND seat = ? AND status = 'spoken' ORDER BY id",
            sessions + [seat]).fetchall()
        real = [(kind, line) for kind, line in rows if kind not in NOT_REAL]
        seats[seat] = {"calls": len(real), "example": real[0][1] if real else None}
        if not real:
            problems.append(f"the {seat} seat made no real call")

    # D3: qualifying in the same launch
    quali = 0
    if launch_id is not None:
        quali = conn.execute("SELECT COUNT(*) FROM sessions WHERE launch_id = ? AND session_type BETWEEN 5 AND 8",
                             (launch_id,)).fetchone()[0]
    if quali == 0:
        problems.append("no qualifying session in the same launch (D3)")

    # D4: what the LLM cost, and how fast the reflective lines were
    cost = conn.execute("SELECT COALESCE(SUM(cost_rs), 0) FROM llm_calls WHERE session_id = ?",
                        (session_id,)).fetchone()[0]
    if cost > COST_CAP_RS:
        problems.append(f"LLM cost Rs {cost:.2f} is over the Rs {COST_CAP_RS:.0f} cap (D4)")
    latencies = [row[0] for row in conn.execute(
        "SELECT latency_ms FROM radio_log WHERE session_id = ? AND status = 'spoken' AND urgent = 0 AND latency_ms IS NOT NULL",
        (session_id,))]
    latency = None
    if latencies:
        latencies.sort()
        latency = {"p50_ms": statistics.median(latencies), "p95_ms": latencies[int(len(latencies) * 0.95) - 1] if len(latencies) >= 20 else max(latencies)}

    refused = conn.execute(
        "SELECT COUNT(*) FROM radio_log WHERE session_id = ? AND reason IS NOT NULL AND reason != 'ok' AND reason != 'over budget'",
        (session_id,)).fetchone()[0]
    quiet = conn.execute("SELECT COUNT(*) FROM radio_log WHERE session_id = ? AND status = 'quiet'",
                         (session_id,)).fetchone()[0]

    # his own verdict: did he switch it off, and was any seat wrong (D1)
    if answers.get("switched_off"):
        problems.append("you switched it off")
    ratings = answers.get("ratings", {})
    for seat, rating in ratings.items():
        if rating == "wrong":
            problems.append(f"you rated the {seat} seat wrong (D1)")
    missing = [seat for seat in SEATS if seat not in ratings]
    if missing:
        problems.append("seats not rated yet: " + ", ".join(missing))

    return {
        "session_id": session_id,
        "verdict": "DONE" if not problems else "NOT YET",
        "problems": problems,
        "seats": seats,
        "cost_rs": round(cost, 2),
        "latency": latency,
        "lines_refused_by_the_gate": refused,
        "quiet_used": quiet,
        "qualifying_in_launch": quali,
    }


def save_result(conn, result):
    conn.executescript(CHECKS_TABLE)
    conn.execute("INSERT INTO done_checks (session_id, checked_at, verdict, details) VALUES (?,?,?,?)",
                 (result["session_id"], datetime.now().isoformat(timespec="seconds"), result["verdict"],
                  json.dumps(result)))
    conn.commit()


def print_report(result):
    print(f"\nAPEX v2 DONE CHECK - session {result['session_id']}")
    print("-" * 60)
    for seat, found in result["seats"].items():
        mark = "PASS" if found["calls"] else "FAIL"
        example = found["example"] or ""
        print(f"  {mark}  {seat:14s} {found['calls']:3d} calls   {example[:60]}")
    print(f"  cost of the race: Rs {result['cost_rs']}  (cap Rs 5)")
    if result["latency"]:
        print(f"  reflective line latency: p50 {result['latency']['p50_ms']} ms, p95 {result['latency']['p95_ms']} ms")
    print(f"  lines the gate refused: {result['lines_refused_by_the_gate']}   'quiet' used: {result['quiet_used']}")
    print("-" * 60)
    print(f"  VERDICT: {result['verdict']}")
    for problem in result["problems"]:
        print(f"    - {problem}")


def ask_him():
    switched_off = input("Did you switch Apex off or mute it during the race? (y/n) ").strip().lower() == "y"
    ratings = {}
    for seat in SEATS:
        while True:
            rating = input(f"  the {seat} seat was: useful / noise / wrong? ").strip().lower()
            if rating in ("useful", "noise", "wrong"):
                ratings[seat] = rating
                break
    return {"switched_off": switched_off, "ratings": ratings}


if __name__ == "__main__":
    conn = connect_db("apex.db")
    session_id = int(sys.argv[1]) if len(sys.argv) > 1 else latest_race(conn)
    if session_id is None:
        print("No race session recorded yet.")
    else:
        result = check(conn, session_id, ask_him())
        save_result(conn, result)
        print_report(result)
    conn.close()
