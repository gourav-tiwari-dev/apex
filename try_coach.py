"""Talk to the race engineer without the game running.

    python try_coach.py                 the 23 Sep Le Mans race, frozen at lap 4
    python try_coach.py --lap 2         frozen at another lap
    python try_coach.py --tape TAPE     another race tape

Set the situation you want to ask about (24 Sep: "make the situation exactly match what I'm
saying"). The real race stays underneath - your corners, balance, habits, rivals - and only
the cars around you are moved:
    --situation dive       car behind 0.3 s back, 1.0 s a lap quicker
    --situation fast       car behind 0.5 s back, 2.0 s a lap quicker (a much faster car)
    --situation defend     car ahead 0.4 s up, you 0.6 s a lap quicker, it is blocking
    --situation sandwich   car ahead 0.5 s up and car behind 0.4 s back
    --situation lastlap    last lap, car behind 0.2 s back
    --situation closedive  car behind 0.3 s back, diving, only 0.2 s a lap quicker
    --situations defend,sandwich,lastlap   several: say "next situation" on the radio to move on
    --behind 0.5 --behind-pace 2.0 --ahead 0.4 --ahead-pace -0.6 --laps-left 3
      (pace = how much quicker a lap that car is than you; negative = slower)

It replays the race silently up to that lap (about a minute), freezes it, and then it is a
radio: hold R1, ask anything, let go. Short everyday questions get the fixed answer, anything
else goes to the race agent, and the answer is spoken, like in a race. Every agent question
costs about Rs 0.2. Nothing is written to apex.db: the replay goes into a throwaway database.
"""
import argparse
import contextlib
import io
import os
import tempfile
import time

from dataclasses import replace

import live_telemetry
import memory
from race_state import identity, same_class_neighbours
import ptt as push_to_talk
from agent import RaceAgent, Snapshot
from answers import Answers, needs_agent
from lmu_import import NoVoice
from radio import Budget
from team_memory import facts as memory_facts
from voice import Voice

DEFAULT_TAPE = "tape_20260923_201605.jsonl.gz"

SITUATIONS = {
    "dive": {"behind": 0.3, "behind_pace": 1.0, "laps_left": 3},
    "fast": {"behind": 0.5, "behind_pace": 2.0, "laps_left": 3},
    "defend": {"ahead": 0.4, "ahead_pace": -0.6, "laps_left": 3},
    "sandwich": {"ahead": 0.5, "ahead_pace": -0.3, "behind": 0.4, "behind_pace": 0.5, "laps_left": 3},
    "lastlap": {"behind": 0.2, "behind_pace": 0.3, "laps_left": 1},
    # diving at him, but not much quicker: the one to hold (his question, 24 Sep)
    "closedive": {"behind": 0.3, "behind_pace": 0.2, "laps_left": 3},
}
NEXT_SITUATION = ("next situation", "next scenario", "next one")


def set_situation(frozen, seats, ahead_gap=None, ahead_pace=0.0, behind_gap=None, behind_pace=0.0,
                  laps_left=None):
    """Move the cars just ahead and behind to the gaps and pace asked for, and rebuild the
    picture the coach sees. pace = seconds a lap quicker than him (negative = slower)."""
    race = frozen["base_race"]                  # always from the real moment, not the last situation
    engineer = seats["RaceEngineer"]
    engineer.gaps_at_line = dict(frozen["base_gaps"])
    engineer.to_go_at_line = frozen["base_to_go"]
    me = race.me
    my_lap = me.last_lap if me.last_lap > 0 else me.best_lap
    ahead, _, behind, _ = same_class_neighbours(race)
    opponents = []
    for opponent in race.opponents:
        if ahead is not None and opponent is ahead and ahead_gap is not None:
            opponent = replace(opponent, time_behind_leader=me.time_behind_leader - ahead_gap,
                               last_lap=round(my_lap - ahead_pace, 3))
            # a lap ago the gap was bigger by whatever he gained on it
            engineer.gaps_at_line["ahead"] = (identity(opponent), round(ahead_gap - ahead_pace, 2))
        if behind is not None and opponent is behind and behind_gap is not None:
            opponent = replace(opponent, time_behind_leader=me.time_behind_leader + behind_gap,
                               last_lap=round(my_lap - behind_pace, 3))
            engineer.gaps_at_line["behind"] = (identity(opponent), round(behind_gap + behind_pace, 2))
        opponents.append(opponent)
    race = replace(race, opponents=opponents)
    if laps_left is not None:
        engineer.to_go_at_line = laps_left
    frozen["race"] = race
    frozen["snapshot"] = Snapshot(race, frozen["lap"], frozen["lap_dist"], frozen["corners"], engineer,
                                  seats["Strategist"], seats["PerformanceEngineer"], seats["Racecraft"],
                                  seats["Governor"], frozen["habits"], {})


def frozen_race(tape, lap):
    """Replay the tape silently and keep a still picture of the race just after the line of
    that lap, plus the seats as they were then."""
    made = {}

    # the replay builds its own seats inside run_session: these subclasses keep a hand on them
    def keep(name, seat_class):
        class Kept(seat_class):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                made[name] = self
        return Kept

    for name in ("Strategist", "PerformanceEngineer", "Racecraft", "Governor"):
        setattr(live_telemetry, name, keep(name, getattr(live_telemetry, name)))

    real = memory.connect_db("apex.db")
    habits = []
    for kind in ("lap_one", "corner_habit", "pass_attempts", "clean_race", "rival"):
        habits += [fact["summary"] for fact in memory_facts(real, kind)]
    real.close()

    frozen = {}
    lap_started = {}
    Engineer = live_telemetry.RaceEngineer

    class FreezingEngineer(Engineer):
        def __init__(self):
            super().__init__()
            made["RaceEngineer"] = self

        def update(self, moment):
            calls = super().update(moment)
            ready = moment.race is not None and moment.race.me is not None
            if moment.lap_count >= lap and "at" not in lap_started:
                lap_started["at"] = moment.now
            # 20 s into the lap: at the line itself the game has not posted the lap times yet
            settled = "at" in lap_started and moment.now - lap_started["at"] >= 20.0
            if "snapshot" not in frozen and ready and settled:
                frozen["snapshot"] = Snapshot(moment.race, moment.lap_count, moment.frame.lap_dist, moment.corners,
                                              self, made["Strategist"], made["PerformanceEngineer"],
                                              made["Racecraft"], made["Governor"], habits, {})
                frozen["race"] = moment.race
                frozen["lap"] = moment.lap_count
                frozen["lap_dist"] = moment.frame.lap_dist
                frozen["corners"] = moment.corners
                frozen["habits"] = habits
                frozen["base_race"] = moment.race
                frozen["base_gaps"] = dict(self.gaps_at_line)
                frozen["base_to_go"] = self.to_go_at_line
            return calls

    live_telemetry.RaceEngineer = FreezingEngineer
    throwaway = os.path.join(tempfile.mkdtemp(), "try_coach.db")
    live_telemetry.connect_db = lambda: memory.connect_db(throwaway)
    with contextlib.redirect_stdout(io.StringIO()):
        live_telemetry.run_session(True, None, tape, out_loud=False, persona=NoVoice())
    if "snapshot" not in frozen:
        raise SystemExit(f"that tape never reached lap {lap}")
    return frozen, made


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lap", type=int, default=4)
    parser.add_argument("--tape", default=DEFAULT_TAPE)
    parser.add_argument("--clean", action="store_true", help="no swearing")
    parser.add_argument("--situation", choices=sorted(SITUATIONS))
    parser.add_argument("--situations", help="several, comma separated; 'next situation' moves on")
    parser.add_argument("--ahead", type=float, help="gap to the car ahead, seconds")
    parser.add_argument("--ahead-pace", type=float, default=0.0, help="how much quicker a lap the car ahead is")
    parser.add_argument("--behind", type=float, help="gap to the car behind, seconds")
    parser.add_argument("--behind-pace", type=float, default=0.0, help="how much quicker a lap the car behind is")
    parser.add_argument("--laps-left", type=int)
    args = parser.parse_args()

    print(f"Replaying {args.tape} silently up to lap {args.lap}...")
    frozen, seats = frozen_race(args.tape, args.lap)
    # the queue of situations: one from the flags, or several to step through by voice
    queue = []
    if args.situations:
        queue = [name.strip() for name in args.situations.split(",") if name.strip() in SITUATIONS]
    elif args.situation:
        queue = [args.situation]
    custom = {}
    for key, value in (("ahead", args.ahead), ("behind", args.behind), ("laps_left", args.laps_left)):
        if value is not None:
            custom[key] = value
    if args.ahead_pace:
        custom["ahead_pace"] = args.ahead_pace
    if args.behind_pace:
        custom["behind_pace"] = args.behind_pace
    if custom:
        SITUATIONS["custom"] = custom
        queue.append("custom")
    step = {"index": 0}

    def go_to(index):
        name = queue[index]
        chosen = SITUATIONS[name]
        set_situation(frozen, seats, chosen.get("ahead"), chosen.get("ahead_pace", 0.0),
                      chosen.get("behind"), chosen.get("behind_pace", 0.0), chosen.get("laps_left"))
        picture = frozen["snapshot"].picture
        print(f"\n=== Situation {index + 1} of {len(queue)}: {name} ===")
        print(f"P{picture['place']}, {picture['laps_to_go']} laps to go.")
        for side in ("ahead", "behind"):
            car = picture.get(side)
            if car:
                print(f"  {side}: {car['driver']}, {car['gap_s']} s, {car.get('their_pace', '')}, {car.get('fight', '')}")
        return name

    if queue:
        go_to(0)

    talk = push_to_talk.start_if_set_up()
    if talk is None:
        return
    voice = Voice(out_loud=True)
    budget = Budget(cap_rs=5.0)
    agent = RaceAgent(budget, args.clean)
    answers = Answers(seats["Governor"], seats["RaceEngineer"], seats["Strategist"], seats["PerformanceEngineer"],
                      args.clean)
    print("\nHold R1 and ask anything. Ctrl+C to stop.\n")
    try:
        while True:
            for heard in talk.poll():
                print(f"you: {heard.text}")
                said = heard.text.lower()
                if any(words in said for words in NEXT_SITUATION):
                    if step["index"] + 1 < len(queue):
                        step["index"] += 1
                        name = go_to(step["index"])
                        voice.say(f"Next situation: {name}.")
                    else:
                        voice.say("That was the last situation, mate.")
                    continue
                if needs_agent(heard.text):
                    voice.play_bank_if_free("STAND_BY", "Copy. Stand by.")
                    agent.ask(heard.text, frozen["snapshot"], 0.0)
                else:
                    answer = answers.answer(heard.text, frozen["race"], frozen["lap"], 0.0)
                    print(f"apex: {answer.template}")
                    voice.say(answer.template)
            for result in agent.finished():
                call = result["call"]
                cost = sum(spent["cost_rs"] for spent in result["costs"])
                print(f"apex: {call.template}   ({call.facts['seconds']} s, Rs {cost:.2f})")
                voice.say(call.template)
            time.sleep(0.01)
    except KeyboardInterrupt:
        talk.close()
        print(f"\nSpent Rs {budget.spent_rs:.2f} on the agent.")


if __name__ == "__main__":
    main()
