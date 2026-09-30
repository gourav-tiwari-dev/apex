"""Where Apex asks the model (product, 30 Sep): the developer's key goes straight to the
provider; a tester's machine goes through the Apex AI door with its own token; neither is a
plain error the radio survives. The door itself is tested in server/worker.test.mjs."""

import json

import pytest

from coach.llm import PROVIDER_URL, where_to_ask


def test_a_developer_key_goes_straight_to_the_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("AICREDITS_API_KEY", "sk-dev")
    door = tmp_path / "server.json"
    door.write_text(json.dumps({"url": "https://door.example", "token": "apx_1"}))
    assert where_to_ask(str(tmp_path / "none.env"), str(door)) == (PROVIDER_URL, "sk-dev")


def test_a_tester_goes_through_the_door_with_their_own_token(tmp_path, monkeypatch):
    monkeypatch.delenv("AICREDITS_API_KEY", raising=False)
    door = tmp_path / "server.json"
    door.write_text(json.dumps({"url": "https://apex-ai-door.example.workers.dev/", "token": "apx_sam"}))
    assert where_to_ask(str(tmp_path / "none.env"), str(door)) == (
        "https://apex-ai-door.example.workers.dev/v1", "apx_sam")


def test_no_access_at_all_is_a_plain_error(tmp_path, monkeypatch):
    monkeypatch.delenv("AICREDITS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="no AI access"):
        where_to_ask(str(tmp_path / "none.env"), str(tmp_path / "none.json"))


def test_the_debrief_can_be_imported_without_ai_access(monkeypatch):
    # apex.py imports it at start: it used to open the client right there and crash
    import importlib
    import between_sessions.debrief as debrief

    monkeypatch.delenv("AICREDITS_API_KEY", raising=False)
    importlib.reload(debrief)
    assert debrief._client is None
