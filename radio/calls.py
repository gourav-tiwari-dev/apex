"""A Call: one thing a seat wants said, with how important it is and how long it may wait.

The priority ladder (lower = more important) decides who wins when two calls want the air:
the spotter, then race control (flags), racecraft, the engineer, strategy, performance, and
memory last."""

from dataclasses import dataclass, field


# Lower number = more important.
SPOTTER = 1  # car alongside, clear, three wide
RACE_CONTROL = 2  # flags, safety car, penalties, physics abort
RACECRAFT = 3  # attack, defend, "not here"
ENGINEER = 4  # gaps, damage, composure
STRATEGY = 5  # fuel, tyres, weather, pits
PERFORMANCE = 6  # corner deltas, lap feedback
MEMORY = 7  # habits recalled, earned praise


@dataclass
class Call:
    seat: str  # "spotter", "race_engineer", "strategist", ...
    kind: str  # what happened, e.g. "CAR_LEFT", "OFF_TRACK", "FUEL_TO_FINISH"
    sim_time: float  # when the seat raised it
    priority: int  # see the ladder above
    ttl: float  # seconds of sim time it stays worth saying
    # the plain-English sentence code wrote (said when there is no template)
    conclusion: str
    facts: dict = field(
        default_factory=dict
    )  # the numbers behind the line, logged with it
    urgent: bool = (
        False  # time-critical: goes out at once, from the pre-rendered voice bank
    )
    template: str | None = None  # exact words; urgent calls always have one
    evidence: dict = field(default_factory=dict)  # database ids this call rests on
    asked: bool = False  # an answer to his push-to-talk question
    # v3: goes out the moment the radio is free, even mid-corner, and is never counted in the
    # talk budget (closing alarms, "stick it", praise at the moment it is earned)
    immediate: bool = False
    voice: str = "engineer"  # "spotter": said in the spotter's own voice
