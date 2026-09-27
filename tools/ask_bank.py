"""The question bank against a frozen race, for real (v3 step 5b).
Usage: ask_bank.py OUT_JSON [--agent N] [--cap RS] [--tape TAPE] [--lap LAP]

Freezes the race (like try_coach.py), then:
  - every fixed-lane question is answered by code (free), and checked it does not crash
  - N agent questions, spread evenly over the categories, go to the real model
Writes every answer with its tools, time and cost to OUT_JSON, and prints a summary.
Nothing touches apex.db: the replay goes into a throwaway database."""

import argparse
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)

import session
from question_bank import QUESTIONS
from try_coach import frozen_race, DEFAULT_TAPE
from coach.agent import RaceAgent
from talk.answers import Answers
from radio import Budget

GAVE_UP = (
    "No clean answer",
    "Radio's lagging",
    "Lost the data",
    "Over the radio budget",
)


def spread(questions, n):
    """n questions, every category in turn, so a small run still covers everything."""
    by_category = {}
    for q in questions:
        by_category.setdefault(q[0], []).append(q)
    picked = []
    while len(picked) < n and any(by_category.values()):
        for category in sorted(by_category):
            if by_category[category] and len(picked) < n:
                picked.append(by_category[category].pop(0))
    return picked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--agent", type=int, default=100)
    parser.add_argument("--cap", type=float, default=25.0)
    parser.add_argument("--tape", default=DEFAULT_TAPE)
    parser.add_argument("--lap", type=int, default=4)
    parser.add_argument("--only", help="just these questions, separated by |")
    args = parser.parse_args()

    print("replaying the race to freeze it ...")
    frozen, seats = frozen_race(args.tape, args.lap)
    snapshot = frozen["snapshot"]
    conn = session.connect_db()
    snapshot.db_path = session.database_file(conn)
    snapshot.session_id = conn.execute("SELECT MAX(id) FROM sessions").fetchone()[0]
    conn.close()
    race, lap = frozen["race"], frozen["lap"]

    results = []
    lane = Answers(
        seats["Governor"],
        seats["RaceEngineer"],
        seats["Strategist"],
        seats["PerformanceEngineer"],
    )
    fixed = [q for q in QUESTIONS if q[1] != "agent"]
    for category, route, question, _ in fixed:
        started = time.perf_counter()
        call = lane.answer(question, race, lap, 0.0)
        results.append(
            {
                "category": category,
                "lane": "fixed",
                "question": question,
                "answer": call.template,
                "ms": round((time.perf_counter() - started) * 1000, 2),
            }
        )
    print(f"fixed lane: {len(fixed)} answered by code")

    budget = Budget(cap_rs=args.cap)
    agent = RaceAgent(budget, client=None)
    chosen = spread([q for q in QUESTIONS if q[1] == "agent"], args.agent)
    if args.only:
        wanted = args.only.split("|")
        chosen = [q for q in QUESTIONS if q[2] in wanted]
    for n, (category, route, question, expected_tools) in enumerate(chosen, 1):
        if not budget.allows_llm():
            print(f"budget cap Rs {args.cap} reached after {n - 1} questions")
            break
        snapshot.actions = []
        started = time.perf_counter()
        answer, info = agent.think(question, snapshot)
        seconds = round(time.perf_counter() - started, 2)
        cost = round(sum(c["cost_rs"] for c in info.get("costs", [])), 3)
        used = info.get("tools", [])
        hit = (not expected_tools) or any(tool in used for tool in expected_tools)
        results.append(
            {
                "category": category,
                "lane": "agent",
                "question": question,
                "answer": answer,
                "tools": used,
                "expected_tools": list(expected_tools),
                "tool_hit": hit,
                "seconds": seconds,
                "cost_rs": cost,
                "rounds": info.get("rounds"),
                "refused": info.get("refused"),
                "gave_up": answer.startswith(GAVE_UP),
            }
        )
        print(
            f"  {n:3d} {seconds:5.1f}s Rs{cost:.2f} {'  ' if hit else 'T!'} {question}\n        -> {answer}"
        )
        with open(args.out, "w", encoding="utf8") as f:
            json.dump(results, f, indent=1)

    asked = [r for r in results if r["lane"] == "agent"]
    if asked:
        times = sorted(r["seconds"] for r in asked)
        print(
            f"\nagent: {len(asked)} asked, {sum(r['gave_up'] for r in asked)} gave up, "
            f"{sum(not r['tool_hit'] for r in asked)} without an expected tool, "
            f"{sum(1 for r in asked if r['refused'])} needed a rewrite"
        )
        print(
            f"time: median {statistics.median(times)} s, 90% under {times[int(len(times) * 0.9) - 1]} s, "
            f"cost Rs {round(budget.spent_rs, 2)} (Rs {round(budget.spent_rs / len(asked), 3)} a question)"
        )
    with open(args.out, "w", encoding="utf8") as f:
        json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
