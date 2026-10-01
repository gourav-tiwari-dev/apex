"""The driver Apex works for (product, 30 Sep 2026).

Apex began as one driver's engineer: the coach was told "You are APEX, Gourav's race engineer...
He drives a GT3 on a controller". A stranger's engineer must not call them Gourav, so who the
driver is lives here, in profile.json (written by the setup wizard, never committed: it is the
user's own). Without the file, Apex uses NEW_DRIVER.

The coach's prompt reads name, car, input and habit; the radio reads spicy; apex.py reads clips.
"""

import json
import os
from dataclasses import asdict, dataclass

PROFILE_FILE = "profile.json"


@dataclass
class Profile:
    name: str = "the driver"
    car: str = "a GT3"
    input: str = "a wheel"  # "a wheel" or "a controller"
    habit: str = ""  # the driver's own words about a weakness, or ""
    spicy: bool = False  # the engineer swears (never slurs, in any mode)
    voice: str = "standard"
    clips: bool = False  # record the game window and make a short of every race


NEW_DRIVER = Profile()

# his profile as it was hard-coded until 30 Sep; tests check the coach's prompt is unchanged with it
GOURAV = Profile(
    name="Gourav",
    car="a GT3",
    input="a controller",
    habit="in close racing he gets hasty and commits too early",
    spicy=True,
)


def load_profile(path=PROFILE_FILE):
    if not os.path.exists(path):
        return NEW_DRIVER
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    known = {
        key: value
        for key, value in saved.items()
        if key in Profile.__dataclass_fields__
    }
    return Profile(**known)


def save_profile(profile, path=PROFILE_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(profile), f, indent=1)
