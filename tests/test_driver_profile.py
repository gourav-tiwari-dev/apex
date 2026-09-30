"""The driver profile (product, 30 Sep): a stranger's engineer must not call them Gourav, and
Gourav's own coach must be told exactly what it was told before the profile existed."""

import json
import os
import re

from coach.prompt import AGENT_PROMPT, TOOLS, VOICE_REMINDER, for_driver
from driver_profile import GOURAV, NEW_DRIVER, Profile, load_profile, save_profile

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def before(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def squashed(text):
    return re.sub(r"\s+", " ", text).strip()


def test_gourav_s_coach_is_told_the_same_as_before_the_profile():
    # only line breaks moved (the habit is one sentence now); every word is the same
    assert squashed(for_driver(AGENT_PROMPT, GOURAV)) == squashed(before("agent_prompt_before_profile.txt"))
    assert for_driver(VOICE_REMINDER, GOURAV) == before("voice_reminder_before_profile.txt")


def test_a_new_driver_is_never_called_gourav():
    sam = Profile(name="Sam", car="a Hypercar", input="a wheel")
    told = for_driver(AGENT_PROMPT, sam) + for_driver(VOICE_REMINDER, sam) + json.dumps(TOOLS)
    assert "Gourav" not in told
    assert "Sam's race engineer" in told
    assert "drives a Hypercar on a wheel" in told
    assert "<<" not in told


def test_without_a_habit_the_coach_leans_on_team_memory():
    told = for_driver(AGENT_PROMPT, NEW_DRIVER)
    assert "From his own words" not in told
    assert "my_habits has what team memory has measured" in told


def test_profile_file_round_trip_and_defaults(tmp_path):
    path = str(tmp_path / "profile.json")
    assert load_profile(path) == NEW_DRIVER
    save_profile(GOURAV, path)
    assert load_profile(path) == GOURAV
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": "Sam", "from_a_newer_version": 1}, f)
    assert load_profile(path).name == "Sam"          # unknown keys are ignored, not a crash
    assert load_profile(path).spicy is False          # new drivers get the clean engineer
