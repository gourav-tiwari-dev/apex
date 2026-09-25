"""v3 step 5b: every question in the 300-question bank goes to the right lane (tools/route_check.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))


def test_every_bank_question_is_routed_to_the_right_lane():
    import route_check
    assert route_check.main() == 0


def test_the_bank_has_300_different_questions():
    from question_bank import QUESTIONS
    assert len({q for _, _, q, _ in QUESTIONS}) == len(QUESTIONS) == 300
