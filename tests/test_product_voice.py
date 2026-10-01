"""Apex as a product (30 Sep): clean is the default, swearing is opt-in, and a slur never goes
on air in any mode. (The coach is the only model that speaks; every answer passes check_answer.)"""

from coach.answer_checks import check_answer
from session import Session


def test_a_slur_is_refused_even_when_swearing_is_on():
    # the model's own words about a rival on 27 Sep, session 59
    line = "That BMW already hit you once, the cunt's defending aggressive."
    ok, reason = check_answer(line, [], clean=False)
    assert ok is False
    assert "slur" in reason


def test_swearing_at_the_situation_is_still_fine_when_spicy():
    assert check_answer("Fucking send it into the Esses, mate.", [], clean=False) == (
        True,
        "ok",
    )


def test_clean_mode_catches_the_forms_the_old_list_missed():
    for line in (
        "You got fucked there.",
        "Bollocks. Reset.",
        "Stop being a prick about it.",
    ):
        assert check_answer(line, [], clean=True)[0] is False, line


def test_the_session_hands_clean_to_racecraft():
    # racecraft's praise pools swore even with --clean before 30 Sep
    session = Session.__new__(Session)
    session.build_team(True)
    assert session.racecraft.clean is True
    session = Session.__new__(Session)
    session.build_team(False)
    assert session.racecraft.clean is False
