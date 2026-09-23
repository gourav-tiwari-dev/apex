import live_telemetry
import memory

# The fingerprint of every line the v1 coach chose to say on the 3-lap tape.
# If this changes, something in the 60 Hz path changed what Apex decides to say.
V1_EVENT_HASH = "1c8fd3525291514836383b1509b5af39a78792e5fd8149b8218404c8ef25058d"


def test_v1_event_hash_unchanged(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")

    # a throwaway database, so the test never writes into the real apex.db
    monkeypatch.setattr(live_telemetry, "connect_db", lambda: memory.connect_db(db_path))
    # no LLM calls in tests: the hash is taken before any line is phrased
    monkeypatch.setattr(live_telemetry, "phrase_event", lambda event: None)
    monkeypatch.setattr(live_telemetry, "radio_check", lambda: None)

    live_telemetry.run_session(True, None)

    conn = memory.connect_db(db_path)
    row = conn.execute("SELECT event_hash FROM sessions").fetchone()
    conn.close()
    assert row[0] == V1_EVENT_HASH
