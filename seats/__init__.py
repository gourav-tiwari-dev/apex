"""The seats of the race team. Each one looks at the same Moment every frame and may raise
Calls; the radio decides what actually goes on air."""
from dataclasses import dataclass


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

    @property
    def now(self):
        return self.frame.elapsed_time
