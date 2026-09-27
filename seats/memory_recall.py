"""Team memory, on the radio: what past races say about the moment you are in now.

  - on the formation lap: your lap-1 habit, if the evidence says you have one
  - on the straight before a corner where something keeps happening: a reminder, once
    per race per corner

It only ever repeats a team-memory fact, and every fact already carries its evidence.
"""

from radio import Call, MEMORY
from game.constants import FORMATION_LAP

REMIND_BEFORE_M = 400.0
RECALL_TTL_S = 15.0
# live 25 Sep: his team memory has a habit at nearly every corner, and "you've had trouble there"
# went out at 6 corners in a row in 3 minutes ("it kept saying you were bad at every corner").
# Only the worst ones, one a lap.
HABIT_CORNERS_PER_RACE = 2


class MemoryRecall:
    def __init__(self, lap_one=None, corner_habits=None):
        self.lap_one = lap_one  # the lap_one fact, or None
        self.corner_habits = corner_habits or {}  # corner -> fact summary
        self.lap_one_said = False
        self.reminded = set()
        self.reminded_lap = None

    def update(self, moment):
        calls = []
        now = moment.now
        race = moment.race

        if (
            self.lap_one is not None
            and not self.lap_one_said
            and race is not None
            and race.session.game_phase == FORMATION_LAP
        ):
            self.lap_one_said = True
            calls.append(
                Call(
                    seat="memory",
                    kind="LAP_ONE_HABIT",
                    sim_time=now,
                    priority=MEMORY,
                    ttl=RECALL_TTL_S,
                    conclusion=f"From your last races: {self.lap_one['summary']}. Lap 1 is for surviving, not for winning.",
                    facts={"habit": self.lap_one["summary"]},
                    template="Lap 1: survive it. That's where you've been losing it.",
                    evidence={"fact_id": self.lap_one["fact_id"]},
                )
            )

        corners = moment.corners or []
        in_pits = race is not None and race.me is not None and race.me.in_pits
        worst = sorted(
            self.corner_habits,
            key=lambda name: -(self.corner_habits[name].get("value") or 0),
        )
        worst = set(worst[:HABIT_CORNERS_PER_RACE])
        if (
            moment.corner is None
            and corners
            and not in_pits
            and moment.lap_count >= 1
            and self.reminded_lap != moment.lap_count
        ):
            distance = moment.frame.lap_dist
            for corner in sorted(corners, key=lambda c: c["start"]):
                name = corner["name"]
                ahead = corner["start"] - distance
                if (
                    0 < ahead <= REMIND_BEFORE_M
                    and name in worst
                    and name not in self.reminded
                ):
                    self.reminded.add(name)
                    self.reminded_lap = moment.lap_count
                    fact = self.corner_habits[name]
                    calls.append(
                        Call(
                            seat="memory",
                            kind="CORNER_HABIT",
                            sim_time=now,
                            priority=MEMORY,
                            ttl=RECALL_TTL_S,
                            conclusion=f"Coming up: {fact['summary']}. Clean exit this time.",
                            facts={"corner": name, "habit": fact["summary"]},
                            template=f"{name} next. You've had trouble there. Clean exit.",
                            evidence={"fact_id": fact["fact_id"]},
                        )
                    )
                    break
        return calls
