"""The setup wizard's decisions (product, 30 Sep), without opening its window."""

from driver_profile import Profile
from setup_wizard import find_lmu, profile_from_answers, steam_libraries


def write_vdf(tmp_path, libraries):
    body = "\n".join(
        f'\t"{i}"\n\t{{\n\t\t"path"\t\t"{lib}"\n\t}}' for i, lib in enumerate(libraries)
    )
    vdf = tmp_path / "libraryfolders.vdf"
    vdf.write_text('"libraryfolders"\n{\n' + body + "\n}\n", encoding="utf-8")
    return str(vdf)


def test_lmu_is_found_in_a_second_steam_library(tmp_path):
    games = tmp_path / "games"
    exe = games / "steamapps" / "common" / "Le Mans Ultimate" / "Le Mans Ultimate.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    vdf = write_vdf(
        tmp_path,
        [
            str(tmp_path / "empty").replace("\\", "\\\\"),
            str(games).replace("\\", "\\\\"),
        ],
    )
    assert len(steam_libraries(vdf)) == 2
    assert find_lmu(vdf) == str(exe.parent)


def test_no_steam_means_no_lmu_and_no_crash(tmp_path):
    assert find_lmu(str(tmp_path / "missing.vdf")) is None


def test_answers_become_a_profile_and_keep_an_old_habit_note():
    old = Profile(
        name="Gourav", habit="in close racing he gets hasty", voice="standard"
    )
    answers = {
        "name": "  Sam ",
        "car": "a Hypercar",
        "input": "a controller",
        "spicy": True,
        "clips": True,
    }
    profile = profile_from_answers(answers, old)
    assert profile == Profile(
        name="Sam",
        car="a Hypercar",
        input="a controller",
        habit="in close racing he gets hasty",
        spicy=True,
        voice="standard",
        clips=True,
    )


def test_blank_or_odd_answers_fall_back_to_safe_defaults():
    answers = {
        "name": "",
        "car": "a spaceship",
        "input": "a keyboard",
        "spicy": False,
        "clips": False,
    }
    profile = profile_from_answers(answers)
    assert (profile.name, profile.car, profile.input) == (
        "the driver",
        "a GT3",
        "a wheel",
    )
