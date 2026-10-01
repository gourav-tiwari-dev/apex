"""The open beta's feedback path (1 Oct 2026), with the door faked."""

import gzip
import json

import beta
import memory
from test_team_memory import add_session


def write_door(tmp_path, **door):
    path = tmp_path / "server.json"
    path.write_text(json.dumps({"url": "https://door.example", **door}))
    return str(path)


def test_a_developer_machine_is_not_a_beta_install(tmp_path):
    assert beta.ensure_registered(str(tmp_path / "server.json")) is False
    assert beta.ask_after_race(None, 1, str(tmp_path / "server.json")) is None


def test_a_beta_install_registers_once_and_keeps_its_token(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(beta, "post", lambda url, body, token=None, **kw: calls.append(url) or {"token": "apx_new"})
    path = write_door(tmp_path)
    assert beta.ensure_registered(path) is True
    assert json.loads(open(path).read())["token"] == "apx_new"
    assert beta.ensure_registered(path) is True
    assert calls == ["https://door.example/v1/register"]          # not again on the next start


def test_the_tape_leaves_with_no_driver_names(tmp_path):
    tape = tmp_path / "tape.jsonl.gz"
    frames = [
        {"me": {"driver": "Gourav Tiwari"}, "opponents": [{"driver": "seojin Yoon"}, {"driver": "Ann B"}]},
        {"me": {"driver": "Gourav Tiwari"}, "opponents": [{"driver": "Ann B"}]},
        {"t": "race", "sim_time": 1.0},
    ]
    with gzip.open(tape, "wt", encoding="utf-8") as f:
        for frame in frames:
            f.write(json.dumps(frame) + "\n")
    sent = gzip.decompress(beta.anonymised_tape(str(tape))).decode("utf-8")
    assert "Gourav" not in sent and "seojin" not in sent and "Ann" not in sent
    rows = [json.loads(line) for line in sent.splitlines()]
    assert rows[0]["me"]["driver"] == "Me"
    assert rows[0]["opponents"][1]["driver"] == rows[1]["opponents"][0]["driver"] == "Driver 2"
    assert rows[2] == frames[2]


def test_feedback_carries_the_race_and_its_radio_and_the_tape_only_when_ticked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    conn = memory.connect_db("apex.db")
    session = add_session(conn, "tape_x.jsonl.gz", track="Circuit de Spa-Francorchamps")
    conn.execute("INSERT INTO radio_log (session_id, sim_time, seat, kind, priority, urgent, status, line) "
                 "VALUES (?, 12.0, 'spotter', 'CAR_LEFT', 1, 1, 'spoken', 'Car left.')", (session,))
    conn.commit()
    with gzip.open(tmp_path / "tape_x.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(json.dumps({"me": {"driver": "Sam"}}) + "\n")
    sent = []
    monkeypatch.setattr(beta, "post", lambda url, body, token=None, raw=False, **kw: sent.append((url, body, raw)) or {"id": "f1"})
    path = write_door(tmp_path, token="apx_sam")

    assert beta.send_feedback(conn, session, 2, " spotter was late ", send_tape=False, path=path) == "f1"
    url, body, _ = sent[0]
    assert url == "https://door.example/v1/feedback"
    assert body["rating"] == 2 and body["comment"] == "spotter was late"
    assert body["race"]["track"] == "Circuit de Spa-Francorchamps"
    assert body["race"]["radio"] == [{"t": 12.0, "seat": "spotter", "kind": "CAR_LEFT", "line": "Car left."}]
    assert len(sent) == 1                                          # no tape when not ticked

    beta.send_feedback(conn, session, 2, "again", send_tape=True, path=path)
    assert sent[-1][0] == "https://door.example/v1/feedback/f1/tape" and sent[-1][2] is True
