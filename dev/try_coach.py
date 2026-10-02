"""Talk to the race engineer without the game running.

    python dev/try_coach.py                 the 23 Sep Le Mans race, frozen at lap 4
    python dev/try_coach.py --lap 2         frozen at another lap
    python dev/try_coach.py --tape TAPE     another race tape

Set the situation you want to ask about (24 Sep: "make the situation exactly match what I'm
saying"). The real race stays underneath - your corners, balance, habits, rivals - and only
the cars around you are moved:
    --situation dive       car behind 0.3 s back, 1.0 s a lap quicker
    --situation fast       car behind 0.5 s back, 2.0 s a lap quicker (a much faster car)
    --situation defend     car ahead 0.4 s up, you 0.6 s a lap quicker, it is blocking
    --situation sandwich   car ahead 0.5 s up and car behind 0.4 s back
    --situation lastlap    last lap, car behind 0.2 s back
    --situation closedive  car behind 0.3 s back, diving, only 0.2 s a lap quicker
  where the team's fixed call should be overridden (the model must notice):
    --situation damaged    car behind 0.4 s back, 0.4 quicker (team: defend) and your car is damaged
    --situation contact    car behind 0.3 s back, 0.4 quicker (team: defend), and it has hit you twice
    --situation hypercar   car behind 0.6 s back (team: defend) and a Hypercar 200 m behind, lapping you
    --situation deadtyres  car behind 0.4 s back, 0.5 quicker (team: defend), your tyres at 118 C
    --situations defend,sandwich,lastlap   several: say "next situation" on the radio to move on
    --behind 0.5 --behind-pace 2.0 --ahead 0.4 --ahead-pace -0.6 --laps-left 3
      (pace = how much quicker a lap that car is than you; negative = slower)

It replays the race silently up to that lap (about a minute), freezes it, and then it is a
radio: hold R1, ask anything, let go. Short everyday questions get the fixed answer, anything
else goes to the race agent, and the answer is spoken, like in a race. Every agent question
costs about Rs 0.2. Nothing is written to apex.db: the replay goes into a throwaway database.
"""

import os
import sys

# run as `python dev/try_coach.py` from the project folder: Apex's modules are one folder up
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import contextlib
import io
import os
import tempfile
import time

from dataclasses import replace

import session
import memory
from game.race_snapshot import identity
from race.facts import same_class_neighbours
from talk import ptt as push_to_talk
from coach.agent import RaceAgent
from coach.snapshot import Records, Snapshot, Team, Where
from talk.answers import Answers
from talk.hearing import needs_agent
from coach.llm import Budget
from memory.team_memory import facts as memory_facts
from radio.voice import Voice

DEFAULT_TAPE = "tape_20260923_201605.jsonl.gz"

SITUATIONS = {
    "dive": {"behind": 0.3, "behind_pace": 1.0, "laps_left": 3},
    "fast": {"behind": 0.5, "behind_pace": 2.0, "laps_left": 3},
    "defend": {"ahead": 0.4, "ahead_pace": -0.6, "laps_left": 3},
    "sandwich": {
        "ahead": 0.5,
        "ahead_pace": -0.3,
        "behind": 0.4,
        "behind_pace": 0.5,
        "laps_left": 3,
    },
    "lastlap": {"behind": 0.2, "behind_pace": 0.3, "laps_left": 1},
    # diving at him, but not much quicker: the one to hold (his question, 24 Sep)
    "closedive": {"behind": 0.3, "behind_pace": 0.2, "laps_left": 3},
    # the cases a fixed call cannot see (24 Sep): the model may override, with the reason
    "damaged": {"behind": 0.4, "behind_pace": 0.4, "laps_left": 3, "damage": True},
    "contact": {"behind": 0.3, "behind_pace": 0.4, "laps_left": 3, "contacts": 2},
    "hypercar": {"behind": 0.6, "behind_pace": 0.3, "laps_left": 3, "hypercar_m": 200},
    "deadtyres": {"behind": 0.4, "behind_pace": 0.5, "laps_left": 3, "tyres_c": 118},
}
NEXT_SITUATION = ("next situation", "next scenario", "next one")


def set_situation(frozen, seats, situation):
    """Move the cars just ahead and behind to the gaps and pace asked for, and rebuild the
    picture the coach sees. situation: a SITUATIONS entry; pace = seconds a lap quicker
    than him (negative = slower)."""
    race = frozen["base_race"]  # always from the real moment, not the last situation
    engineer = seats["RaceEngineer"]
    engineer.gaps_at_line = dict(frozen["base_gaps"])
    engineer.to_go_at_line = frozen["base_to_go"]
    me = race.me
    ahead, _, behind, _ = same_class_neighbours(race)
    opponents = moved_cars(race, engineer, ahead, behind, situation)
    if situation.get("hypercar_m") is not None:
        opponents.append(
            a_hypercar(
                opponents, ahead, behind, frozen["lap_dist"] - situation["hypercar_m"]
            )
        )
    changes = {}
    if situation.get("damage", False):
        changes["dents"] = [0, 2, 0, 1, 0, 0, 0, 0]
    if situation.get("tyres_c") is not None:
        changes["tyre_temps"] = [[situation["tyres_c"]] * 3] * 4
    race = replace(race, opponents=opponents, me=replace(me, **changes))
    if situation.get("laps_left") is not None:
        engineer.to_go_at_line = situation["laps_left"]
    contacts_this_race = {}
    if situation.get("contacts", 0) and behind is not None:
        contacts_this_race[identity(behind)] = situation["contacts"]
    frozen["race"] = race
    where = Where(frozen["lap"], frozen["lap_dist"], frozen["corners"])
    team = Team(
        engineer,
        seats["Strategist"],
        seats["PerformanceEngineer"],
        seats["Racecraft"],
        seats["Governor"],
    )
    records = Records(frozen["habits"], contacts_this_race)
    frozen["snapshot"] = Snapshot(race, where, team, records)


def moved_cars(race, engineer, ahead, behind, situation):
    """Every opponent, the cars just ahead and behind moved to the situation's gaps and pace;
    the engineer's gaps at the line set to what they were a lap ago."""
    me = race.me
    my_lap = me.last_lap if me.last_lap > 0 else me.best_lap
    ahead_gap = situation.get("ahead")
    ahead_pace = situation.get("ahead_pace", 0.0)
    behind_gap = situation.get("behind")
    behind_pace = situation.get("behind_pace", 0.0)
    opponents = []
    for opponent in race.opponents:
        if ahead is not None and opponent is ahead and ahead_gap is not None:
            opponent = replace(
                opponent,
                time_behind_leader=me.time_behind_leader - ahead_gap,
                last_lap=round(my_lap - ahead_pace, 3),
            )
            # a lap ago the gap was bigger by whatever he gained on it
            engineer.gaps_at_line["ahead"] = (
                identity(opponent),
                round(ahead_gap - ahead_pace, 2),
            )
        if behind is not None and opponent is behind and behind_gap is not None:
            opponent = replace(
                opponent,
                time_behind_leader=me.time_behind_leader + behind_gap,
                last_lap=round(my_lap - behind_pace, 3),
            )
            engineer.gaps_at_line["behind"] = (
                identity(opponent),
                round(behind_gap + behind_pace, 2),
            )
        opponents.append(opponent)
    return opponents


def a_hypercar(opponents, ahead, behind, lap_dist):
    """A faster-class car on the road just behind: a copy of a far-away car, reclassed."""
    spare = [o for o in opponents if o is not ahead and o is not behind][0]
    return replace(
        spare,
        id=999,
        driver="a Hypercar",
        steam_id=999,
        car_class="Hypercar",
        lap_dist=lap_dist,
        in_pits=False,
    )


def frozen_race(tape, lap):
    """Replay the tape silently and keep a still picture of the race just after the line of
    that lap, plus the seats as they were then."""
    made = {}
    keep_seats(made)
    habits = his_habits()
    frozen = {}
    session.RaceEngineer = freezing_engineer(lap, made, frozen, habits)
    throwaway = os.path.join(tempfile.mkdtemp(), "try_coach.db")
    session.connect_db = lambda: memory.connect_db(throwaway)
    with contextlib.redirect_stdout(io.StringIO()):
        session.run_replay(tape, out_loud=False)
    if "snapshot" not in frozen:
        raise SystemExit(f"that tape never reached lap {lap}")
    return frozen, made


def keep_seats(made):
    """The replay builds its own seats inside run_replay: these subclasses keep a hand on
    them, in made, by name."""
    for name in ("Strategist", "PerformanceEngineer", "Racecraft", "Governor"):
        setattr(session, name, kept_seat(name, getattr(session, name), made))


def kept_seat(name, seat_class, made):
    """seat_class, which puts each one it builds in made[name]."""

    class Kept(seat_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made[name] = self

    return Kept


def his_habits():
    """Every habit team memory knows about him, from his real apex.db."""
    real = memory.connect_db("apex.db")
    habits = []
    for kind in ("lap_one", "corner_habit", "pass_attempts", "clean_race", "rival"):
        for fact in memory_facts(real, kind):
            habits.append(fact["summary"])
    real.close()
    return habits


def freezing_engineer(lap, made, frozen, habits):
    """The race engineer, which takes the still picture into frozen 20 s into the lap (at
    the line itself the game has not posted the lap times yet)."""
    lap_started = {}

    class FreezingEngineer(session.RaceEngineer):
        def __init__(self):
            super().__init__()
            made["RaceEngineer"] = self

        def update(self, moment):
            calls = super().update(moment)
            ready = moment.race is not None and moment.race.me is not None
            if moment.lap_count >= lap and "at" not in lap_started:
                lap_started["at"] = moment.now
            settled = "at" in lap_started and moment.now - lap_started["at"] >= 20.0
            if "snapshot" not in frozen and ready and settled:
                freeze(self, moment, made, frozen, habits)
            return calls

    return FreezingEngineer


def freeze(engineer, moment, made, frozen, habits):
    """The coach's snapshot at this moment, and everything a situation is built from."""
    where = Where(moment.lap_count, moment.frame.lap_dist, moment.corners)
    team = Team(
        engineer,
        made["Strategist"],
        made["PerformanceEngineer"],
        made["Racecraft"],
        made["Governor"],
        moment.model,
    )
    frozen["snapshot"] = Snapshot(moment.race, where, team, Records(habits, {}))
    frozen["model"] = moment.model
    frozen["race"] = moment.race
    frozen["lap"] = moment.lap_count
    frozen["lap_dist"] = moment.frame.lap_dist
    frozen["corners"] = moment.corners
    frozen["habits"] = habits
    frozen["base_race"] = moment.race
    frozen["base_gaps"] = dict(engineer.gaps_at_line)
    frozen["base_to_go"] = engineer.to_go_at_line


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lap", type=int, default=4)
    parser.add_argument("--tape", default=DEFAULT_TAPE)
    parser.add_argument("--clean", action="store_true", help="no swearing")
    parser.add_argument("--situation", choices=sorted(SITUATIONS))
    parser.add_argument(
        "--situations", help="several, comma separated; 'next situation' moves on"
    )
    parser.add_argument("--ahead", type=float, help="gap to the car ahead, seconds")
    parser.add_argument(
        "--ahead-pace",
        type=float,
        default=0.0,
        help="how much quicker a lap the car ahead is",
    )
    parser.add_argument("--behind", type=float, help="gap to the car behind, seconds")
    parser.add_argument(
        "--behind-pace",
        type=float,
        default=0.0,
        help="how much quicker a lap the car behind is",
    )
    parser.add_argument("--laps-left", type=int)
    return parser.parse_args()


def situation_queue(args):
    """The situations to step through: the one or several named, then "custom" when the gap
    flags make one."""
    queue = []
    if args.situations:
        for name in args.situations.split(","):
            if name.strip() in SITUATIONS:
                queue.append(name.strip())
    elif args.situation:
        queue = [args.situation]
    custom = {}
    for key, value in (
        ("ahead", args.ahead),
        ("behind", args.behind),
        ("laps_left", args.laps_left),
    ):
        if value is not None:
            custom[key] = value
    if args.ahead_pace:
        custom["ahead_pace"] = args.ahead_pace
    if args.behind_pace:
        custom["behind_pace"] = args.behind_pace
    if custom:
        SITUATIONS["custom"] = custom
        queue.append("custom")
    return queue


def show_situation(frozen, seats, queue, index):
    """Set the situation at index of the queue and print what the coach now sees. Returns
    its name."""
    name = queue[index]
    set_situation(frozen, seats, SITUATIONS[name])
    picture = frozen["snapshot"].picture
    print(f"\n=== Situation {index + 1} of {len(queue)}: {name} ===")
    print(f"P{picture['place']}, {picture['laps_to_go']} laps to go.")
    for side in ("ahead", "behind"):
        car = picture.get(side)
        if car:
            print(
                f"  {side}: {car['driver']}, {car['gap_s']} s, {car.get('their_pace', '')}, {car.get('fight', '')}"
            )
            if car.get("team_call"):
                print(f"    team call: {car['team_call'].split(':')[0]}")
    extras = frozen["snapshot"].override_evidence()
    if extras:
        print(f"  the data would back an override for: {', '.join(sorted(extras))}")
    return name


class CoachRadio:
    """The radio of the frozen race: short everyday questions get the fixed answer, anything
    else goes to the race agent, "next situation" moves on, and every answer is spoken."""

    def __init__(self, frozen, seats, queue, clean):
        self.frozen = frozen
        self.seats = seats
        self.queue = queue
        self.index = 0
        self.voice = Voice(out_loud=True)
        self.budget = Budget()
        self.agent = RaceAgent(self.budget, clean)
        self.answers = Answers(
            seats["Governor"],
            seats["RaceEngineer"],
            seats["Strategist"],
            seats["PerformanceEngineer"],
            clean,
        )

    def hear(self, heard):
        """One thing he said."""
        if not heard.text:
            print("  (heard nothing)")
            return
        print(f"you: {heard.text}")
        said = heard.text.lower()
        if any(words in said for words in NEXT_SITUATION):
            self.next_situation()
            return
        if needs_agent(heard.text):
            self.voice.play_bank_if_free("STAND_BY", "Copy. Stand by.")
            self.agent.ask(heard.text, self.frozen["snapshot"], 0.0)
            return
        answer = self.answers.answer(
            heard.text, self.frozen["race"], self.frozen["lap"], 0.0
        )
        print(f"apex: {answer.template}")
        self.voice.say(answer.template)

    def next_situation(self):
        if self.index + 1 < len(self.queue):
            self.index += 1
            name = show_situation(self.frozen, self.seats, self.queue, self.index)
            self.voice.say(f"Next situation: {name}.")
        else:
            self.voice.say("That was the last situation, mate.")

    def say_answers(self):
        """The agent's answers that have come back: printed with the call and cost, spoken."""
        for result in self.agent.finished():
            call = result["call"]
            cost = sum(spent["cost_rs"] for spent in result["costs"])
            decided = call.facts.get("call") or "-"
            if call.facts.get("override"):
                decided += f" (OVERRIDE: {call.facts['override']})"
            print(
                f"apex [{decided}]: {call.template}   ({call.facts['seconds']} s, Rs {cost:.2f})"
            )
            self.voice.say(call.template)


def main():
    args = parse_args()
    print(f"Replaying {args.tape} silently up to lap {args.lap}...")
    frozen, seats = frozen_race(args.tape, args.lap)
    # the queue of situations: one from the flags, or several to step through by voice
    queue = situation_queue(args)
    if queue:
        show_situation(frozen, seats, queue, 0)

    talk = push_to_talk.start_if_set_up(
        verbose=True
    )  # every step shows: a silent failure shows where
    if talk is None:
        return
    radio = CoachRadio(frozen, seats, queue, args.clean)
    print("\nHold R1 and ask anything. Ctrl+C to stop.\n")
    try:
        while True:
            for heard in talk.poll():
                radio.hear(heard)
            radio.say_answers()
            time.sleep(0.01)
    except KeyboardInterrupt:
        talk.close()
        print(f"\nSpent Rs {radio.budget.spent_rs:.2f} on the agent.")


if __name__ == "__main__":
    main()
