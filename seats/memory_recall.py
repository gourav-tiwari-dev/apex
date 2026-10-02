"""Team memory, on the radio: what past races say about the moment you are in now.

  - on the formation lap: your lap-1 habit, if the evidence says you have one
  - on the straight before a corner where something keeps happening: a reminder, once
    per race per corner

It only ever repeats a team-memory fact, and every fact already carries its evidence.
"""

from radio.calls import Call, MEMORY
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
        """Team memory on the radio: the lap-1 habit on the formation lap, and a corner habit
        just before that corner, one a lap."""
        calls = []
        lap_one = self.lap_one_call(moment)
        if lap_one is not None:
            calls.append(lap_one)
        habit = self.corner_habit_call(moment)
        if habit is not None:
            calls.append(habit)
        return calls

    def lap_one_call(self, moment):
        """His lap-1 habit, once, on the formation lap."""
        race = moment.race
        if (
            self.lap_one is None
            or self.lap_one_said
            or race is None
            or race.session.game_phase != FORMATION_LAP
        ):
            return None
        self.lap_one_said = True
        return Call(
            seat="memory",
            kind="LAP_ONE_HABIT",
            sim_time=moment.now,
            priority=MEMORY,
            ttl=RECALL_TTL_S,
            conclusion=f"From your last races: {self.lap_one['summary']}. Lap 1 is for surviving, not for winning.",
            facts={"habit": self.lap_one["summary"]},
            template="Lap 1: survive it. That's where you've been losing it.",
            evidence={"fact_id": self.lap_one["fact_id"]},
        )

    def corner_habit_call(self, moment):
        """A corner where he keeps having trouble, said just before he gets there: one of his
        worst corners, each once a race, one a lap, never in a corner or the pits."""
        race = moment.race
        corners = moment.corners or []
        in_pits = race is not None and race.me is not None and race.me.in_pits
        if (
            moment.corner is not None
            or not corners
            or in_pits
            or moment.lap_count < 1
            or self.reminded_lap == moment.lap_count
        ):
            return None
        worst = self.worst_corners()
        distance = moment.frame.lap_dist
        for corner in sorted(corners, key=corner_start):
            name = corner["name"]
            ahead = corner["start"] - distance
            if (
                0 < ahead <= REMIND_BEFORE_M
                and name in worst
                and name not in self.reminded
            ):
                self.reminded.add(name)
                self.reminded_lap = moment.lap_count
                return self.habit_reminder(name, moment.now)
        return None

    def worst_corners(self):
        """The corners of his worst habits, HABIT_CORNERS_PER_RACE of them."""
        ranked = sorted(self.corner_habits, key=self.worst_first)
        return set(ranked[:HABIT_CORNERS_PER_RACE])

    def worst_first(self, name):
        """Sort key: the habit with the highest value first."""
        return -(self.corner_habits[name].get("value") or 0)

    def habit_reminder(self, name, now):
        """The call that reminds him of his habit at this corner."""
        fact = self.corner_habits[name]
        return Call(
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


def corner_start(corner):
    """Sort key: where the corner starts on the lap."""
    return corner["start"]
