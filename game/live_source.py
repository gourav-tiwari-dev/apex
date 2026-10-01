"""The game, live: frames of his car read from Le Mans Ultimate's shared memory.

Iterating a LiveSource gives one CarState per new game frame (about 60 a second). Alongside it
keeps the latest race snapshot (every car, the session; it changes about 5 times a second, and
new_race is True on the frame it did) and the cars near him this frame, for the spotter."""

import math
import time

from game.car_frame import CarState
from game.race_snapshot import read_race_snapshot, read_near_cars


class LiveSource:
    def __init__(self, info):
        self.info = info
        self.race = None  # the latest RaceSnapshot
        self.new_race = False  # True on the frame a new snapshot arrived
        self.near = None  # NearCars for this frame, or None when nobody is close
        print("Connected.")
        print("Press Ctrl+C to stop.\n")

    def find_player_scoring(self):
        for veh in self.info.data.scoring.vehScoringInfo:
            if veh.mIsPlayer:
                return veh
        return None

    def read_state(self) -> CarState:

        player_index = self.info.data.telemetry.playerVehicleIdx
        my_car = self.info.data.telemetry.telemInfo[player_index]

        vx = my_car.mLocalVel.x
        vy = my_car.mLocalVel.y
        vz = my_car.mLocalVel.z

        speed_ms = math.sqrt(vx**2 + vy**2 + vz**2)
        speed_kmh = speed_ms * 3.6

        my_scoring = self.find_player_scoring()
        # between sessions (quali loading into the race) the game briefly has no row for
        # me at all: that frame is not a frame (crashed the first live run, 23 Sep 2026)
        if my_scoring is None:
            return None

        return CarState(
            speed_kmh=speed_kmh,
            throttle=my_car.mFilteredThrottle,
            brake=my_car.mFilteredBrake,
            gear=my_car.mGear,
            rpm=my_car.mEngineRPM,
            max_rpm=my_car.mEngineMaxRPM,
            lap_dist=my_scoring.mLapDist,
            lap_invalidated=my_car.mLapInvalidated,
            wheel_rot=[my_car.mWheels[i].mRotation for i in range(4)],
            accel_lat=my_car.mLocalAccel.x,
            accel_long=my_car.mLocalAccel.z,
            surface=[my_car.mWheels[i].mSurfaceType for i in range(4)],
            yaw_rate=my_car.mLocalRot.y,
            elapsed_time=my_car.mElapsedTime,
            steering=round(my_car.mUnfilteredSteering, 4),
            steering_filtered=round(my_car.mFilteredSteering, 4),
            pos=[
                round(my_car.mPos.x, 3),
                round(my_car.mPos.y, 3),
                round(my_car.mPos.z, 3),
            ],
            ori=[round(v, 4) for row in my_car.mOri for v in (row.x, row.y, row.z)],
            delta_best=round(my_car.mDeltaBest, 3),
            last_impact_time=round(my_car.mLastImpactET, 3),
            last_impact_magnitude=round(my_car.mLastImpactMagnitude, 2),
        )

    def __iter__(self):
        last_time = None
        self.last_scoring_time = None
        try:
            while True:
                self.info.update()
                state = self.read_state()
                if state is None:
                    time.sleep(0.05)
                    continue
                if state.elapsed_time != last_time:
                    last_time = state.elapsed_time
                    self.see_race(state)
                    yield state
                time.sleep(0.002)

        except KeyboardInterrupt:
            print("\nStopping...")
            self.info.close()
            print("Closed connection.")

    def see_race(self, state):
        """Scoring updates about 5 times a second: a new race snapshot only when it did. The
        cars near him come with every frame."""
        scoring_time = self.info.data.scoring.scoringInfo.mCurrentET
        self.new_race = scoring_time != self.last_scoring_time
        if self.new_race:
            self.last_scoring_time = scoring_time
            self.race = read_race_snapshot(self.info.data)
        self.near = read_near_cars(self.info.data, state.pos)
