"""Talk to the race engineer without the game running.

    python try_coach.py                 the 23 Sep Le Mans race, frozen at lap 4
    python try_coach.py --lap 2         frozen at another lap
    python try_coach.py --tape TAPE     another race tape

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

import live_telemetry
import memory
import ptt as push_to_talk
from agent import RaceAgent, Snapshot
from answers import Answers, needs_agent
from lmu_import import NoVoice
from radio import Budget
from team_memory import facts as memory_facts
from voice import Voice

DEFAULT_TAPE = "tape_20260923_201605.jsonl.gz"


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
    args = parser.parse_args()

    print(f"Replaying {args.tape} silently up to lap {args.lap}...")
    frozen, seats = frozen_race(args.tape, args.lap)
    picture = frozen["snapshot"].picture
    print(f"Frozen at lap {frozen['lap']}: P{picture['place']}, {picture['laps_to_go']} laps to go.")
    for side in ("ahead", "behind"):
        car = picture.get(side)
        if car:
            print(f"  {side}: {car['driver']}, {car['gap_s']} s, {car.get('their_pace', '')}")

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
