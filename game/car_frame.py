"""One frame of his car, 60 times a second.

Speed, pedals, gear and revs, where the car is on the lap and in the world, how each wheel turns
and on what surface, the g-forces, his steering, and the last impact. Read live from the game's
shared memory (game/live_source.py) or back from a tape (game/tape.py): a tape line is a CarState
written out as it was."""

from dataclasses import dataclass


@dataclass
class CarState:
    speed_kmh: float
    throttle: float  # mFilteredThrottle
    brake: float  # mFilteredBrake
    gear: int
    rpm: float
    max_rpm: float
    lap_dist: float  # from SCORING array
    lap_invalidated: bool  # from SCORING array
    wheel_rot: list  # 4× mRotation (rad/s) — VERIFIED alive
    accel_long: float  # mLocalAccel.z — braking/accel G
    accel_lat: float  # mLocalAccel.x — cornering G
    surface: list  # 4x mWheels[i].mSurfaceType
    yaw_rate: float  # mLocalRot
    elapsed_time: float
    # v2 fields. They default to None so tapes recorded before 23 Sep 2026 still load.
    steering: float | None = None  # mUnfilteredSteering, -1 left .. 1 right (my input)
    steering_filtered: float | None = None  # mFilteredSteering (what the car got)
    pos: list | None = None  # mPos, world x/y/z in metres (the spotter needs it)
    ori: list | None = None  # mOri, 3 rows of the orientation matrix
    delta_best: float | None = None  # mDeltaBest, seconds against my best lap
    last_impact_time: float | None = None  # mLastImpactET
    last_impact_magnitude: float | None = None
