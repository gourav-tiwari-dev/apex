"""The 60 Hz path must not change by accident, and the radio must decide the same things
at any replay speed. Both run on the 3-lap tape, with no LLM and no sound."""

import hashlib
import json

import session
import memory

# What the v1 detectors produced on the 3-lap tape (measured 23 Sep 2026, before the radio
# rewrite). If this changes, the 60 Hz detection changed.
V1_EVENTS = (48, "9b0dd8993af42a701e97586e3ce517c2fa53cefaf62411626dc06e941260aa40")
V1_CORNER_STATS = (
    21,
    "7b4a527c0c6164abfe9176e2b294eacfcd61d21047978b6328af4c6cdfde596c",
)


class FakePersona:
    """Stands in for the LLM: every line is the call's own conclusion, costing nothing."""

    def phrase(self, call):
        return None, 0, 0, 0.0


def run(tmp_path, monkeypatch, name, speed):
    db_path = str(tmp_path / f"{name}.db")
    monkeypatch.setattr(session, "connect_db", lambda: memory.connect_db(db_path))
    session_id = session.run_session(True, speed, out_loud=False, persona=FakePersona())
    conn = memory.connect_db(db_path)
    return conn, session_id


def fingerprint(rows):
    return len(rows), hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def test_detectors_still_find_exactly_what_v1_found(tmp_path, monkeypatch):
    conn, session_id = run(tmp_path, monkeypatch, "events", None)
    events = conn.execute(
        "SELECT kind, round(sim_time, 4), corner, lap_count FROM events ORDER BY id"
    ).fetchall()
    stats = conn.execute(
        "SELECT lap_count, corner, round(brake_onset,3), round(min_speed,3) FROM corner_stats ORDER BY id"
    ).fetchall()
    conn.close()
    assert fingerprint([list(r) for r in events]) == V1_EVENTS
    assert fingerprint([list(r) for r in stats]) == V1_CORNER_STATS


def test_radio_decides_the_same_at_any_replay_speed(tmp_path, monkeypatch):
    conn, session_id = run(tmp_path, monkeypatch, "max_speed", None)
    fast = conn.execute(
        "SELECT event_hash FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()[0]
    conn.close()
    conn, session_id = run(tmp_path, monkeypatch, "paced", 50)
    paced = conn.execute(
        "SELECT event_hash FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()[0]
    conn.close()
    assert fast == paced


def test_session_records_how_it_ended(tmp_path, monkeypatch):
    conn, session_id = run(tmp_path, monkeypatch, "ending", None)
    reason = conn.execute(
        "SELECT end_reason FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()[0]
    spoken = conn.execute(
        "SELECT COUNT(*) FROM radio_log WHERE status = 'spoken'"
    ).fetchone()[0]
    conn.close()
    assert reason == "tape_end"
    assert spoken > 0
