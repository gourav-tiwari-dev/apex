"""The Monza reference lap is Gourav's notes from a HYMO video and the installed app doesn't
carry it (30 Sep). The packaged app crashed at the debrief looking for it; without the file
Monza is measured like every other track."""

import memory
from between_sessions import debrief
from test_team_memory import add_session


def test_monza_uses_the_hand_checked_lap_when_it_is_there(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "reference_hymo.json").write_text("{}")
    conn = memory.connect_db("apex.db")
    session = add_session(conn, "tape.jsonl.gz", track="Autodromo Nazionale Monza")
    assert debrief.reference_for(conn, session) == "reference_hymo.json"


def test_monza_without_the_file_is_measured_like_any_track(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    conn = memory.connect_db("apex.db")
    session = add_session(conn, "tape.jsonl.gz", track="Autodromo Nazionale Monza")
    measured = []
    monkeypatch.setattr(debrief, "reference_from_race", lambda *a: measured.append("race") or None)
    monkeypatch.setattr(debrief, "reference_from_self", lambda *a: measured.append("self") or None)
    assert debrief.reference_for(conn, session) is None       # no file, no other cars, no laps
    assert measured == ["race", "self"]
