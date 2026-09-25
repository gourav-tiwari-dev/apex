"""Replays a tape through Apex (code in the folder given) into a COPY of apex.db, with no model and
no speaker, and prints what went on air. Usage: replay_radio.py CODE_DIR TAPE OUT_DB"""
import os, shutil, sqlite3, sys, collections
code, tape, out_db = sys.argv[1], os.path.abspath(sys.argv[2]), os.path.abspath(sys.argv[3])
shutil.copy(r"C:\Users\gourav\Downloads\apex_telemetry\apex.db", out_db)
os.chdir(code); sys.path.insert(0, code)
import live_telemetry, memory
live_telemetry.connect_db = lambda db_path=None: memory.connect_db(out_db)

class NoModel:
    clean = False
    def online(self): return False
    def phrase(self, call): return None, 0, 0, 0.0

import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    sid = live_telemetry.run_session(True, None, tape, out_loud=False, persona=NoModel())
conn = sqlite3.connect(out_db)
rows = conn.execute("select seat, kind, status, reason from radio_log where session_id=?", (sid,)).fetchall()
spoken = collections.Counter((s, k) for s, k, st, r in rows if st == "spoken")
dropped = collections.Counter(r or st for s, k, st, r in rows if st != "spoken")
print(f"session {sid}: {sum(spoken.values())} lines on air")
for (seat, kind), n in sorted(spoken.items()):
    print(f"   {seat:14s} {kind:16s} {n}")
print("not said:", dict(dropped))
