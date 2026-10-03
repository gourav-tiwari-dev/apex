"""The moments worth a word, found in his car's data frame by frame.

Each detector watches one thing and hands back an Event when it happens: hard braking, a
front lock-up, a lift, entering a corner, a contact (a car within 10 m) or an impact (no
car near), running wide, the rear snapping under braking, wheelspin on the exit, a spin,
and a slide he caught. Most share Detector's rule: the condition must hold for a few
frames, then it fires once, and fires again only after the condition has cleared and its
cooldown has passed. Every detector names the corner from the CornerMap it is given."""

import math
from dataclasses import dataclass

from game.race_snapshot import identity
from driving.track_map import CornerMap


# Wheel radii in metres (from mStaticUndeflectedRadius: 34cm front, 36cm rear).
# Constant — no need to read them every frame.
FRONT_RADIUS = 0.34
REAR_RADIUS = 0.36


def slip_ratio(wheel_rotation, wheel_radius, car_speed_ms):
    """
    How much a wheel is slipping against the road.

       0.0  -> rolling perfectly (wheel speed == car speed)
       < 0  -> wheel turning SLOWER than the car -> LOCKING  (under braking)
       > 0  -> wheel turning FASTER than the car -> WHEELSPIN (under throttle)

    Example: -0.20 = tyre surface moving 20% slower than the ground (big lockup)
             +0.15 = tyre spinning 15% faster than the ground (wheelspin)
    """
    # Guard: near standstill this divides by ~0 and explodes.
    # Below ~3 m/s (~11 km/h) slip ratio is meaningless anyway.
    if car_speed_ms < 3.0:
        return 0.0

    # Speed of the tyre's contact patch, from how fast the wheel spins.
    # rad/s * metres = m/s. abs() because LMU reports forward motion as negative.
    wheel_surface_speed = abs(wheel_rotation) * wheel_radius

    # Compare against how fast the car is actually moving over the ground.
    return (wheel_surface_speed - car_speed_ms) / car_speed_ms


radii = [FRONT_RADIUS, FRONT_RADIUS, REAR_RADIUS, REAR_RADIUS]


@dataclass
class Event:
    """Something that happened to his car on one frame: what, when, how fast, where, and
    in plain words (the conclusion)."""
    kind: str
    sim_time: float
    speed_kmh: float
    corner: str | None = None
    conclusion: str | None = None
    lap_dist: float = 0.0
    lap_count: int = 0
    other_car: str | None = None  # steam id of the other car in a contact
    magnitude: float | None = None  # how hard an impact was


class Detector:
    """The base of the threshold detectors: a condition that has to hold for
    debounce_frames frames fires one event, then waits until it stops (armed) and for
    the cooldown before it can fire again."""
    def __init__(self, corner_map=None):
        self.corner_map = CornerMap() if corner_map is None else corner_map
        self.armed = False
        self.last_fire_time = 0.0
        self.consecutive_true = 0
        self.cooldown = 3.0
        self.debounce_frames = 2

    def current_corner(self, frame):
        """The corner he is in, or "the straight"."""
        return self.corner_map.at(frame.lap_dist) or "the straight"

    def build_event(self, frame):
        """The event for this frame: its kind, time, speed and place."""
        return Event(
            kind=self.kind,
            sim_time=frame.elapsed_time,
            speed_kmh=frame.speed_kmh,
            corner=self.current_corner(frame),
            lap_dist=frame.lap_dist,
        )

    def is_triggered(self, frame) -> bool:
        """Each detector's own condition, true on a frame where it holds."""
        raise NotImplementedError

    def update(self, frame):
        """The event, on the frame the condition has held long enough and the cooldown
        is over; None on every other frame."""
        triggered = self.is_triggered(frame)
        event = None
        if not triggered:
            self.armed = False
            self.consecutive_true = 0
            event = None

        elif triggered and self.consecutive_true < self.debounce_frames:
            self.consecutive_true += 1
            event = None

        elif (
            triggered
            and self.consecutive_true >= self.debounce_frames
            and frame.elapsed_time - self.last_fire_time > self.cooldown
            and not self.armed
        ):
            event = self.build_event(frame)
            self.last_fire_time = frame.elapsed_time
            self.armed = True
            self.consecutive_true += 1

        return event


class HardBrakingDetector(Detector):
    """Hard on the brakes: the pedal past 80% above 30 km/h."""
    def __init__(self, corner_map=None):
        super().__init__(corner_map)
        self.kind = "HARD_BRAKING"

    def build_event(self, frame):
        """The event, with the corner he braked for."""
        e = super().build_event(frame)
        corner = e.corner or "the straight"
        e.conclusion = f"hard on the brakes into {corner}"
        return e

    def is_triggered(self, frame):
        """The brake past 0.8 above 30 km/h."""
        return frame.brake > 0.8 and frame.speed_kmh > 30


class LockUpDetector(Detector):
    """A front lock-up: a front wheel turning much slower than the car moves, under
    braking."""
    def __init__(self, corner_map=None, threshold=-0.3):
        self.threshold = threshold
        super().__init__(corner_map)
        self.kind = "LOCKUP"

    def build_event(self, frame):
        """The event, with the corner and the speed."""
        e = super().build_event(frame)
        corner = e.corner or "the straight"
        e.conclusion = (
            f"Front lockup under heavy braking into {corner} at {e.speed_kmh:.0f}km/h"
        )
        return e

    def is_triggered(self, frame):
        """A front wheel's slip ratio below the threshold, on the brakes, above 30
        km/h."""
        slips = [
            slip_ratio(frame.wheel_rot[i], radii[i], frame.speed_kmh / 3.6)
            for i in range(4)
        ]
        is_locked = (
            (slips[0] < self.threshold or slips[1] < self.threshold)
            and frame.brake > 0.2
            and frame.speed_kmh > 30
        )
        return is_locked


class ThrottleLift(Detector):
    """Coasting: off the throttle and off the brake for over a second (logged, never
    said: coasting before a corner is often right)."""
    def __init__(self, corner_map=None):
        super().__init__(corner_map)
        self.kind = "THROTTLE_LIFT"
        self.last_start_time = 0.0
        self.lifting = False

    def build_event(self, frame):
        """The event, with where he coasted."""
        e = super().build_event(frame)
        corner = e.corner or "the straight"
        e.conclusion = f"off throttle and coasting at {corner}, no braking"
        return e

    def is_triggered(self, frame):
        """Off the throttle without braking for more than a second; a lift that ends in
        braking is a braking zone, not coasting."""
        braking = frame.brake > 0.2
        off_throttle = frame.throttle < 0.5 and frame.speed_kmh > 30

        if not self.lifting:
            if off_throttle and not braking:
                self.lifting = True
                self.last_start_time = frame.elapsed_time
            return False

        if braking:
            self.lifting = False
            return False
        if not off_throttle:
            self.lifting = False
            return False

        return frame.elapsed_time - self.last_start_time > 1.0


class CornerEntryDetection:
    """Which corner he just entered: a segmenter that marks each change of corner (not a
    threshold detector, so it does not use the Detector base)."""
    def __init__(self, corner_map=None):
        self.corner_map = CornerMap() if corner_map is None else corner_map
        self.previous_corner = None

    def update(self, frame):
        """CORNER_ENTRY on the frame he enters a new corner, else None."""
        event = None
        current_corner = self.corner_map.at(frame.lap_dist)

        if current_corner is not None and current_corner != self.previous_corner:
            event = Event(
                kind="CORNER_ENTRY",
                sim_time=frame.elapsed_time,
                speed_kmh=frame.speed_kmh,
                corner=current_corner,
                lap_dist=frame.lap_dist,
                conclusion=f"entering {current_corner} at {frame.speed_kmh:.0f}km/h",
            )
        self.previous_corner = current_corner

        return event


CONTACT_NEAR_M = 10.0  # a car this close at the moment of impact is the car you touched
SAME_INCIDENT_S = 5.0  # a hit within 5 s of the last one is the same incident (LMU: 266, 266.4, 270.9 s)


class ContactDetection:
    """An impact is when the game's last-impact time moves forward.
    With a car within 10 m it is CONTACT (and we know who), otherwise IMPACT (a wall)."""

    def __init__(self, corner_map=None):
        self.corner_map = CornerMap() if corner_map is None else corner_map
        self.last_seen = None
        self.first_frame = True

    def nearest_car(self, frame, near):
        """The car nearest to him within CONTACT_NEAR_M, or None."""
        if near is None or frame.pos is None:
            return None
        nearest = None
        nearest_distance = CONTACT_NEAR_M
        for car in near.cars:
            dx = car.x - frame.pos[0]
            dy = car.y - frame.pos[1]
            dz = car.z - frame.pos[2]
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            if distance <= nearest_distance:
                nearest = car
                nearest_distance = distance
        return nearest

    def update(self, frame, near, race):
        """An impact the game logged since the last frame: CONTACT with the nearest car, or
        IMPACT when no car was near (a wall)."""
        if not self.new_incident(frame):
            return None
        corner = self.corner_map.at(frame.lap_dist) or "the straight"
        magnitude = frame.last_impact_magnitude
        other = self.nearest_car(frame, near)
        if other is None:
            return Event(
                kind="IMPACT",
                sim_time=frame.elapsed_time,
                speed_kmh=frame.speed_kmh,
                corner=corner,
                lap_dist=frame.lap_dist,
                magnitude=magnitude,
                conclusion=f"hit something at {corner}, no car near",
            )
        driver, steam_id = who_it_was(other, race)
        return Event(
            kind="CONTACT",
            sim_time=frame.elapsed_time,
            speed_kmh=frame.speed_kmh,
            corner=corner,
            lap_dist=frame.lap_dist,
            magnitude=magnitude,
            other_car=steam_id,
            conclusion=f"contact with {driver} at {corner}",
        )

    def new_incident(self, frame):
        """The first hit of a new incident: not one the game remembered from before the
        session, and not another hit of the same incident."""
        if self.first_frame:
            # whatever impact the game remembers on the first frame happened before this session
            self.first_frame = False
            self.last_seen = frame.last_impact_time
            return False
        # None = no impact yet (and old tapes carry no impact data at all)
        if frame.last_impact_time is None or frame.last_impact_time == self.last_seen:
            return False
        previous = self.last_seen
        self.last_seen = frame.last_impact_time
        # one incident fires several hits within seconds (LMU logged 266, 266 and 271 s for one
        # moment at Le Mans): hits that close together are the same incident
        if previous is not None and frame.last_impact_time - previous < SAME_INCIDENT_S:
            return False
        return True


def who_it_was(other, race):
    """(driver, steam id) of the car he touched; "a car" and its game id when the race does
    not list it."""
    driver = "a car"
    steam_id = str(other.id)
    if race is not None:
        for opponent in race.opponents:
            if opponent.id == other.id:
                driver = opponent.driver
                steam_id = identity(opponent)
    return driver, steam_id


class OffTrackDetector(Detector):
    """Off the track: two wheels or more on grass or gravel."""
    def __init__(self, corner_map=None):
        super().__init__(corner_map)
        self.kind = "OFF_TRACK"

    def build_event(self, frame):
        """The event, with what he ran onto (grass or gravel), where and how fast."""
        e = super().build_event(frame)
        surface = "Road"
        for s in frame.surface:
            if s == 2:
                surface = "grass"
            elif s == 4:
                surface = "gravel"
        e.conclusion = f"Ran Wide onto {surface} at {e.corner}, {e.speed_kmh:.0f}km/h"
        return e

    def is_triggered(self, frame):
        """Two or more wheels on grass (2) or gravel (4)."""
        off_wheels = 0
        for s in frame.surface:
            if s in (2, 4):
                off_wheels += 1
        return off_wheels >= 2


# Rear snap on the brakes, the problem Gourav cannot work out by feel (23 Sep 2026).
# In a steady corner the car rotates at lateral_g / speed. When it rotates much faster
# than that while braking with lock on, the rear has let go.
# GUESSED thresholds: tune them on his tapes once they carry steering.
SNAP_BRAKE = 0.15  # on the brakes
SNAP_STEERING = 0.05  # with some lock on (trail braking)
SNAP_MIN_SPEED_MS = 15.0  # 54 km/h: slower than this, yaw means nothing
SNAP_RATIO = 1.4  # rotating 40% faster than the corner explains
SNAP_MARGIN = 0.1  # rad/s, so tiny wobbles on a straight never count


class RearSnapDetector(Detector):
    """The rear snapping under braking: the car turning faster than the steering and the
    cornering force explain."""
    def __init__(self, corner_map=None):
        super().__init__(corner_map)
        self.kind = "REAR_SNAP"

    def build_event(self, frame):
        """The event, with where the rear went."""
        e = super().build_event(frame)
        e.conclusion = f"rear snapped under braking at {e.corner}"
        return e

    def is_triggered(self, frame):
        """On the brakes with steering lock, and the yaw rate well past what the lateral
        force explains."""
        if frame.steering is None:
            return False  # old tapes have no steering channel
        speed_ms = frame.speed_kmh / 3.6
        if (
            frame.brake < SNAP_BRAKE
            or abs(frame.steering) < SNAP_STEERING
            or speed_ms < SNAP_MIN_SPEED_MS
        ):
            return False
        explained = abs(frame.accel_lat) / speed_ms
        return abs(frame.yaw_rate) > SNAP_RATIO * explained + SNAP_MARGIN


# Wheelspin on exit: a rear wheel turning much faster than the car moves, on the throttle.
# Only on v2 tapes (steering recorded), so the detector golden of the old tapes stays exact.
SPIN_SLIP = 0.15  # rear tyre 15% faster than the ground (see slip_ratio)
SPIN_THROTTLE = 0.5


class WheelspinDetector(Detector):
    """Wheelspin on a corner exit: a rear wheel spinning faster than the car moves, on
    the throttle."""
    def __init__(self, corner_map=None):
        super().__init__(corner_map)
        self.kind = "WHEELSPIN"

    def build_event(self, frame):
        """The event, with the corner he spun the wheels out of."""
        e = super().build_event(frame)
        e.conclusion = f"wheelspin on the exit of {e.corner}"
        return e

    def is_triggered(self, frame):
        """On the throttle above 30 km/h with a rear slip ratio past SPIN_SLIP."""
        if (
            frame.steering is None
            or frame.throttle < SPIN_THROTTLE
            or frame.speed_kmh < 30
        ):
            return False
        speed_ms = frame.speed_kmh / 3.6
        rear = [slip_ratio(frame.wheel_rot[i], radii[i], speed_ms) for i in (2, 3)]
        return max(rear) > SPIN_SLIP


def slip_angle(frame, before):
    """Degrees between where the car points and where it is going (0 = straight on, 180 =
    backwards), from two frames about 0.1 s apart. None when it cannot be told (old tapes carry
    no position, or the car is barely moving)."""
    if frame.pos is None or frame.ori is None or before is None or before.pos is None:
        return None
    dx = frame.pos[0] - before.pos[0]
    dz = frame.pos[2] - before.pos[2]
    if math.hypot(dx, dz) < 0.2:
        return None
    # the car's nose is -z in its own frame (driving straight on reads 180 degrees otherwise)
    pointing = math.atan2(-frame.ori[2], -frame.ori[8])
    going = math.atan2(dx, dz)
    difference = (pointing - going + math.pi) % (2 * math.pi) - math.pi
    return abs(math.degrees(difference))


SPUN_DEGREES = 90.0  # pointing more than this away from where it is going: spun
SLIDE_DEGREES = 15.0  # a slide worth a word
SLIDE_OVER_DEGREES = 5.0  # back under this: the slide is caught
SLIDE_MIN_KMH = 60.0
SPIN_MIN_KMH = 5.0
HIT_BEFORE_SPIN_S = 5.0  # an impact this soon before the spin: he was hit


class SpinDetector(Detector):
    """Live 25 Sep: he was punted round at Porsche Curves (nose went 6 -> 180 degrees, yaw rate
    never over 0.9) and nothing was said; a 19-degree slide he caught at Mulsanne Chicane 1 was
    called "Spun" (yaw spiked). The old rule was yaw rate over 1.7 rad/s. A spin is now the car
    pointing more than 90 degrees from where it is going. Old tapes without position keep the
    yaw rule."""

    def __init__(self, corner_map=None):
        super().__init__(corner_map)
        self.kind = "SPIN"
        self.cooldown = 10.0  # one incident can swing past 90 degrees twice (25 Sep: 1045 and 1048 s)
        self.last_fire_time = -self.cooldown  # free to fire from the first second
        self.recent = []  # the last ~0.1 s of frames, for the direction of travel
        self.last_car_contact = (
            None  # sim time of the last contact WITH A CAR (set by the race loop)
        )

    def build_event(self, frame):
        """The event: "spun", or "spun after contact" when a car hit him just before."""
        e = super().build_event(frame)
        corner = e.corner or "the straight"
        # "you got hit" only when the contact detector saw a car there: a wall is an impact too
        hit = (
            self.last_car_contact is not None
            and 0 <= frame.elapsed_time - self.last_car_contact <= HIT_BEFORE_SPIN_S
        )
        e.conclusion = f"spun after contact at {corner}" if hit else f"spun at {corner}"
        return e

    def is_triggered(self, frame):
        """The car sliding past SPUN_DEGREES (from its position and heading over the
        last 0.1 s); old tapes without them go on the yaw rate."""
        self.recent.append(frame)
        while (
            len(self.recent) > 1
            and frame.elapsed_time - self.recent[0].elapsed_time > 0.1
        ):
            self.recent.pop(0)
        if frame.pos is None or frame.ori is None:
            return abs(frame.yaw_rate) > 1.7
        slip = slip_angle(frame, self.recent[0])
        return (
            slip is not None and slip > SPUN_DEGREES and frame.speed_kmh > SPIN_MIN_KMH
        )


class SlideCaughtDetector:
    """A big moment he saved: more than 15 degrees of slide above 60 km/h that came back
    straight without ever passing 90 (a spin). Fires on the save, so it is praise, not a
    warning. His words, 25 Sep: "tell me mate you caught a big moment", not "you spun"."""

    def __init__(self, corner_map=None):
        self.corner_map = CornerMap() if corner_map is None else corner_map
        self.kind = "SLIDE_CAUGHT"
        self.recent = []
        self.worst = None  # the biggest slide angle of the slide in progress
        self.cooldown = 10.0
        self.last_fire_time = -100.0

    def update(self, frame):
        """SLIDE_CAUGHT on the frame a big slide is caught (it went past SLIDE_DEGREES,
        never past a spin, and is back under SLIDE_OVER_DEGREES); None otherwise."""
        self.recent.append(frame)
        while (
            len(self.recent) > 1
            and frame.elapsed_time - self.recent[0].elapsed_time > 0.1
        ):
            self.recent.pop(0)
        slip = slip_angle(frame, self.recent[0])
        if slip is None:
            return None
        if self.worst is None:
            if slip > SLIDE_DEGREES and frame.speed_kmh > SLIDE_MIN_KMH:
                self.worst = slip
            return None
        self.worst = max(self.worst, slip)
        if self.worst > SPUN_DEGREES:
            if slip < SLIDE_OVER_DEGREES:
                self.worst = None  # that was a spin, the spin detector has it
            return None
        if slip < SLIDE_OVER_DEGREES:
            worst = self.worst
            self.worst = None
            if frame.elapsed_time - self.last_fire_time < self.cooldown:
                return None
            self.last_fire_time = frame.elapsed_time
            corner = self.corner_map.at(frame.lap_dist) or "the straight"
            return Event(
                kind=self.kind,
                sim_time=frame.elapsed_time,
                speed_kmh=frame.speed_kmh,
                corner=corner,
                lap_dist=frame.lap_dist,
                magnitude=round(worst),
                conclusion=f"caught a {round(worst)} degree slide at {corner}",
            )
        return None
