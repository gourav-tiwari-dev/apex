"""His standing orders on real races, no racing needed (26 Sep). Each tape is replayed as it was,
then again with him giving each order a few seconds before the first call that order should stop,
so every check is aimed at a real line. Every line on air is checked against what he ordered.
No model, no speaker, no microphone.

Usage: replay_orders.py [TAPE ...]   (default: every race tape in tools/tapes.py)
Exit code 0 = no order was broken. A check with nothing to stop is shown as "not tested", not passed."""
import collections
import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
import live_telemetry
import memory
from orders import COACHING_KINDS
from tapes import RACE_TAPES

LEAD = 5.0      # seconds between his order and the call it should stop

# (name, what he says, the calls it stops, the broken ones among them)
ORDERS = [
    ("no coaching", "No more coaching, I know the corners.", COACHING_KINDS,
     lambda kind, line: kind in COACHING_KINDS),
    ("we push", "Don't give me that bullshit, we push, no holding back.", {"FUEL"},
     lambda kind, line: kind == "FUEL" and not line.startswith(("You said push", "Box"))),
    ("fight everyone", "Fight everyone, nobody gets past.", {"FIGHT_COST"},
     lambda kind, line: kind == "FIGHT_COST" and "settle" in line.lower()),
    ("no gaps", "Don't tell me the gaps.", {"GAP_REPORT"},
     lambda kind, line: kind == "GAP_REPORT"),
]
# bring it home is a pace order like push: its own run
BRING_HOME = ("bring it home", "Bring it home, no risks.", {"ATTACK_PLAN"},
              lambda kind, line: kind == "ATTACK_PLAN")


class NoModel:
    clean = False
    def online(self): return False
    def phrase(self, call): return None, 0, 0, 0.0


def replay(tape, script, name):
    out_db = os.path.join(tempfile.gettempdir(), f"apex_orders_{name}.db")
    shutil.copy(os.path.join(HERE, "apex.db"), out_db)
    # a clean slate: orders said "for good" in real races would muddle the before/after
    conn = sqlite3.connect(out_db)
    conn.execute("DROP TABLE IF EXISTS standing_orders")
    conn.commit()
    conn.close()
    live_telemetry.connect_db = lambda db_path=None: memory.connect_db(out_db)
    with contextlib.redirect_stdout(io.StringIO()):
        sid = live_telemetry.run_session(True, None, tape, out_loud=False, persona=NoModel(), script=script)
    conn = sqlite3.connect(out_db)
    rows = conn.execute("select sim_time, kind, status, reason, coalesce(line, ''), facts from radio_log "
                        "where session_id=? order by sim_time, id", (sid,)).fetchall()
    conn.close()
    return rows


def asked(row):
    try:
        return bool(json.loads(row[5] or "{}").get("heard"))
    except (ValueError, AttributeError):
        return False


def spoken_unasked(rows):
    return [r for r in rows if r[2] == "spoken" and not asked(r) and r[1] not in ("ANSWER_ORDER",)]


def check_tape(tape, orders, tag):
    """One scripted run: each order aimed at the first line it should stop, back to normal after the
    last of them. Returns (report rows, failures)."""
    base = replay(tape, None, "base")
    said = spoken_unasked(base)
    end = said[-1][0] if said else 0.0
    script, aimed = [], []
    last = 0.0
    for name, words, kinds, broken in orders:
        first = next((r[0] for r in said if r[1] in kinds and r[0] - LEAD > last + 1.0), None)
        if first is None:
            aimed.append((name, None, kinds, broken))
            continue
        last = first - LEAD
        script.append((None, last, words))
        aimed.append((name, last, kinds, broken))
    if not script:
        return [(tag, name, "not tested", "", "") for name, *_ in aimed], []
    # back to normal once the silenced calls have had time to show; before the race ends
    normal = max(last + 60.0, last + (end - last) * 0.5)
    script.append((None, normal, "Back to normal."))
    ordered = replay(tape, script, "orders")
    acks = [r for r in ordered if r[1] == "ANSWER_ORDER"]
    failures = []
    if len(acks) != len(script) or any(r[2] != "spoken" for r in acks):
        failures.append(f"{tape}: {len(script)} orders said, {sum(r[2] == 'spoken' for r in acks)} said back")
    heard_at = [r[0] for r in acks] + [float("inf")] * len(script)
    normal_at = heard_at[len(script) - 1]
    report = []
    i = 0
    for name, at, kinds, broken in aimed:
        if at is None:
            report.append((tag, name, "not tested", "", ""))
            continue
        start = heard_at[i]
        i += 1
        would = [r for r in said if r[1] in kinds and start <= r[0] < normal_at]
        stood = [r for r in ordered if start <= r[0] < normal_at]
        bad = [r for r in spoken_unasked(stood) if broken(r[1], r[4])]
        stopped = sum(1 for r in stood if r[3] == "his_order" and r[1] in kinds)
        boxes = [r for r in spoken_unasked(stood) if r[1] == "FUEL"]
        if name == "we push" and len(boxes) > 1:
            failures.append(f"{tape}: box said {len(boxes)} times under 'we push'")
        for r in bad:
            failures.append(f"{tape}: {name}: {r[0]:.1f}s {r[1]} {r[4]!r}")
        report.append((tag, name, f"{len(would)} as raced", f"{stopped} stopped", f"{len(bad)} broken"))
    # back to normal: what the race said after it comes back
    back = collections.Counter(r[1] for r in spoken_unasked(base) if r[0] >= normal_at + 15)
    now = collections.Counter(r[1] for r in spoken_unasked(ordered) if r[0] >= normal_at + 15)
    watched = set().union(*(k for _, _, k, _ in aimed))
    gone = sorted(k for k in back if k in watched and now[k] == 0)
    report.append((tag, "back to normal", f"{sum(back[k] for k in watched)} as raced",
                   f"{sum(now[k] for k in watched)} said", "silent: " + ",".join(gone) if gone else "ok"))
    if gone:
        failures.append(f"{tape}: still silent after 'back to normal': {gone}")
    # before the first order nothing changes
    first = heard_at[0]
    b0 = collections.Counter(r[1] for r in said if r[0] < first)
    o0 = collections.Counter(r[1] for r in spoken_unasked(ordered) if r[0] < first)
    if b0 != o0:
        failures.append(f"{tape}: lines changed before any order: {dict(b0 - o0)} / {dict(o0 - b0)}")
    return report, failures


def main():
    tapes = sys.argv[1:] or RACE_TAPES
    report, failures = [], []
    for tape in tapes:
        for orders, tag in ((ORDERS, "orders"), ([BRING_HOME], "bring home")):
            rows, bad = check_tape(tape, orders, tag)
            report += [(tape[5:20],) + row for row in rows]
            failures += bad
    for row in report:
        print("  ".join(f"{str(c):16s}" for c in row))
    tested = sum(1 for r in report if r[3].endswith("as raced") and not r[3].startswith("0 "))
    print(f"\n{tested} order checks had a real call to stop")
    print("PASS" if not failures else "FAIL:\n  " + "\n  ".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
