"""What he did in each corner, lap by lap.

As he leaves a corner, CornerStats hands back one CornerStat: where he started braking, his
slowest speed, the metres he spent near that speed (the slow zone) and coasting (no pedal), his
time through the corner, and where he was back on the power after the slowest point."""

from dataclasses import dataclass

from driving.track_map import CornerMap


@dataclass
class CornerStat:
    lap_count: int
    corner: str
    brake_onset: float | None
    min_speed: float | None
    slow_zone: float | None
    coast: float | None
    time_s: float | None = None  # time from entering the corner window to leaving it
    throttle_on: float | None = (
        None  # lap distance where he is back on the power after the slowest point
    )


BRAKE_ON = 0.4  # braking starts when the pedal crosses this
# "on the power": half throttle after the slowest point (a controller trigger)
THROTTLE_ON = 0.5
SLOW_ZONE_X = 10  # km/h above his slowest speed that still counts as the slow zone
PEDAL_OFF = 0.05  # both pedals under this: coasting


class CornerStats:
    def __init__(self, corner_map=None):
        self.corner_map = CornerMap() if corner_map is None else corner_map
        self.corner = None
        self.lap_count = None
        self.brake_onset = None
        self.min_speed = None
        self.prev_brake = 0.0
        self.frames = []
        self.prev_time = None

    def current_corner(self, real_lap_distance):
        return self.corner_map.at(real_lap_distance)

    def update(self, frame, lap_count, real_lap_distance):
        """One frame: the CornerStat of a corner he has just left, else None."""
        now = self.current_corner(real_lap_distance)
        was = self.corner
        stat = None
        step = 0.0
        seconds = 0.0
        if self.prev_time is not None:
            seconds = frame.elapsed_time - self.prev_time
            step = frame.speed_kmh / 3.6 * seconds
        if now is None and was is not None:
            stat = self.leave_corner()
        elif now is not None:
            if now != was:
                self.arrive(now, lap_count)
            self.measure(frame, step, seconds, real_lap_distance)
        self.prev_brake = frame.brake
        self.prev_time = frame.elapsed_time
        return stat

    def leave_corner(self):
        """Leaving: hand back the corner's row, then forget everything."""
        slow_zone = 0
        coast = 0.0
        time_s = 0.0
        slowest = 0
        for index, (speed, meters, throttle, brake, seconds, distance) in enumerate(
            self.frames
        ):
            if speed <= self.min_speed + SLOW_ZONE_X:
                slow_zone += meters
            if brake < PEDAL_OFF and throttle < PEDAL_OFF:
                coast += meters
            time_s += seconds
            if speed == self.min_speed:
                slowest = index
        throttle_on = None
        for speed, meters, throttle, brake, seconds, distance in self.frames[slowest:]:
            if throttle >= THROTTLE_ON:
                throttle_on = distance
                break
        stat = CornerStat(
            self.lap_count,
            self.corner,
            self.brake_onset,
            self.min_speed,
            slow_zone,
            coast,
            round(time_s, 3),
            throttle_on,
        )
        self.corner = None
        self.lap_count = None
        self.brake_onset = None
        self.min_speed = None
        self.frames = []
        return stat

    def arrive(self, corner, lap_count):
        """Just arrived: start a fresh corner."""
        self.corner = corner
        self.lap_count = lap_count
        self.brake_onset = None
        self.min_speed = None
        self.frames = []

    def measure(self, frame, step, seconds, real_lap_distance):
        """In the corner (the arrival frame too): the slowest speed, the frame, and where the
        braking began."""
        if self.min_speed is None or frame.speed_kmh < self.min_speed:
            self.min_speed = frame.speed_kmh
        self.frames.append(
            (
                frame.speed_kmh,
                step,
                frame.throttle,
                frame.brake,
                seconds,
                real_lap_distance,
            )
        )
        # brake just crossed BRAKE_ON this frame: below it last frame, at or above it now
        brake_crossed = self.prev_brake < BRAKE_ON and frame.brake >= BRAKE_ON
        if self.brake_onset is None and brake_crossed:
            self.brake_onset = real_lap_distance
