"""The seats of the race team. Each one looks at the same Moment every frame and may raise
Calls; the radio decides what actually goes on air."""
from dataclasses import dataclass, field


@dataclass
class Moment:
    frame: object            # CarState: my car, this frame
    race: object | None      # the latest RaceSnapshot (None on old tapes)
    new_race: bool           # True on the frame a new snapshot arrived
    near: object | None      # NearCars this frame, or None when nobody is close
    lap_count: int
    lap_wrapped: bool        # True on the frame I crossed the line
    corner: str | None       # the corner I am in, or None on a straight
    track: str | None
    corner_stat: object | None = None   # the CornerStat of a corner I just left, this frame only
    session_type: int | None = None     # mSession: 5-8 qualifying, 10-13 race
    corners: list | None = None         # this track's corner map, to place other cars too
    events: list = field(default_factory=list)   # events detected this frame (contacts, offs...)
    model: object | None = None         # the race model (race_model.py): the one picture, read-only

    @property
    def now(self):
        return self.frame.elapsed_time
