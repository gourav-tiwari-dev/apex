"""Offline: does every question in the bank go to the right lane (fixed answer or agent)?
No model, no cost. Usage: route_check.py"""

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from talk.hearing import intent_of, needs_agent
from question_bank import QUESTIONS


def route(question):
    return "agent" if needs_agent(question) else intent_of(question)


def main():
    wrong = []
    by_category = collections.Counter()
    right_by_category = collections.Counter()
    for category, expected, question, _ in QUESTIONS:
        got = route(question)
        by_category[category] += 1
        if got == expected:
            right_by_category[category] += 1
        else:
            wrong.append((category, question, expected, got))
    for category in sorted(by_category):
        print(
            f"  {category:10s} {right_by_category[category]:3d} / {by_category[category]:3d}"
        )
    total = len(QUESTIONS)
    print(f"routed right: {total - len(wrong)} / {total}")
    for category, question, expected, got in wrong:
        print(f"   WRONG [{category}] {question!r}: expected {expected}, got {got}")
    return len(wrong)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
