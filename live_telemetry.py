import time
import math
import threading
import json,gzip,hashlib,zlib



from sharedmemory import MMapControl
from lmu_data import LMUObjectOut, LMUConstants
from dataclasses import dataclass,asdict
from queue import Full, Empty, Queue
from datetime import datetime
from memory import connect_db,start_session,save_event,finish_session,save_lap,save_corner_stat,print_corner_report,set_session_track,save_radio,save_llm_call,save_session_result,save_car_settings,save_rivals,save_opponent_corners,save_pass_attempts
from radio import Governor, Budget, Call, RACE_CONTROL
from seats.settle import RaceSettle
from seats.track_awareness import TrackAwareness
from seats.qualifying import QualifyingEngineer
from orders import StandingOrders
from persona import Persona
from voice import Voice, RadioDesk
from seats.performance import call_from_event, PerformanceEngineer
from seats import Moment
from seats.spotter import Spotter
from seats.race_engineer import RaceEngineer
from seats.strategist import Strategist, RACE_SESSIONS, GREEN_PHASE
from seats.racecraft import Racecraft
from seats.memory_recall import MemoryRecall
from answers import Answers, needs_agent, intent_of, fix_mishearing, garbled, is_mark
from agent import RaceAgent, Snapshot
from race_model import RaceModel
import ptt as push_to_talk
from team_memory import facts as memory_facts
from race_state import read_race_snapshot, read_near_cars, race_snapshot_from_dict, near_cars_from_dict, identity
from track_map import MONZA_CORNERS, corner_at, corners_for_track, TrackMapLearner, save_map, TURNING

@dataclass
class CarState:
    speed_kmh: float
    throttle: float          # mFilteredThrottle
    brake: float             # mFilteredBrake
    gear: int
    rpm: float
    max_rpm: float
    lap_dist: float          # from SCORING array
    lap_invalidated: bool    # from SCORING array
    #grip: list               # daed
    wheel_rot: list   # 4× mRotation (rad/s) — VERIFIED alive
    accel_long: float # mLocalAccel.z — braking/accel G
    accel_lat:  float # mLocalAccel.x — cornering G
    surface: list            # 4x mWheels[i].mSurfaceType
    yaw_rate: float          # mLocalRot
    elapsed_time: float
    # v2 fields. They default to None so tapes recorded before 23 Sep 2026 still load.
    steering: float | None = None           # mUnfilteredSteering, -1 left .. 1 right (my input)
    steering_filtered: float | None = None  # mFilteredSteering (what the car got)
    pos: list | None = None                 # mPos, world x/y/z in metres (the spotter needs it)
    ori: list | None = None                 # mOri, 3 rows of the orientation matrix
    delta_best: float | None = None         # mDeltaBest, seconds against my best lap
    last_impact_time: float | None = None   # mLastImpactET
    last_impact_magnitude: float | None = None

# Wheel radii in metres (from mStaticUndeflectedRadius: 34cm front, 36cm rear).
# Constant — no need to read them every frame.
FRONT_RADIUS = 0.34
REAR_RADIUS  = 0.36



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


def match_opponents():
    opponents=[]
    telemetry_by_id= {t.mID:t for t in telemetry if t.mID!=0}
    for s in scoring:
        if s.mID !=0:
            telemetry_info=telemetry_by_id.get(s.mID)
            opponents.append((s,telemetry_info))

    return opponents


# Corners come from track_map.py, the one place that decides where they are.
# Monza uses the hand-measured windows; other tracks are learned from my laps.
current_corners = MONZA_CORNERS

radii = [FRONT_RADIUS, FRONT_RADIUS, REAR_RADIUS, REAR_RADIUS]
class LiveSource:
    def __init__(self,info):
        self.info=info
        self.race = None        # the latest RaceSnapshot
        self.new_race = False   # True on the frame a new snapshot arrived
        self.near = None        # NearCars for this frame, or None when nobody is close
        print("Connected.")
        print("Press Ctrl+C to stop.\n")
        
    def find_player_scoring(self):
        for veh in self.info.data.scoring.vehScoringInfo:
                if veh.mIsPlayer:
                    return veh
        return None

    def read_state(self)-> CarState:

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
            #grip=[my_car.mWheels[i].mGripFract for i in range(4)],   dead
            wheel_rot= [my_car.mWheels[i].mRotation for i in range(4)],
            accel_lat= my_car.mLocalAccel.x,
            accel_long= my_car.mLocalAccel.z,
            surface=[my_car.mWheels[i].mSurfaceType for i in range(4)],
            yaw_rate=my_car.mLocalRot.y,
            elapsed_time=my_car.mElapsedTime,
            steering=round(my_car.mUnfilteredSteering, 4),
            steering_filtered=round(my_car.mFilteredSteering, 4),
            pos=[round(my_car.mPos.x, 3), round(my_car.mPos.y, 3), round(my_car.mPos.z, 3)],
            ori=[round(v, 4) for row in my_car.mOri for v in (row.x, row.y, row.z)],
            delta_best=round(my_car.mDeltaBest, 3),
            last_impact_time=round(my_car.mLastImpactET, 3),
            last_impact_magnitude=round(my_car.mLastImpactMagnitude, 2),
        )
    


    def __iter__(self):
        last_time= None
        last_scoring_time = None
        try:

            while True:
                self.info.update()
                state = self.read_state()
                if state is None:
                    time.sleep(0.05)
                    continue
                if state.elapsed_time!=last_time:
                    last_time= state.elapsed_time
                    # scoring updates about 5 times a second; only take a snapshot when it did
                    scoring_time = self.info.data.scoring.scoringInfo.mCurrentET
                    self.new_race = scoring_time != last_scoring_time
                    if self.new_race:
                        last_scoring_time = scoring_time
                        self.race = read_race_snapshot(self.info.data)
                    self.near = read_near_cars(self.info.data, state.pos)
                    yield state
                time.sleep(0.002)

        except KeyboardInterrupt:
            print("\nStopping...")
            self.info.close()
            print("Closed connection.")


TAPE_PATH = "tape_60hz_clean.jsonl.gz"
# Gourav's cap: Rs 5 (23 Sep 2026), raised to Rs 10 (25 Sep, "increase the budget a bit").
# Past it, template lines only. Push-to-talk is NOT capped (his call, 25 Sep: "I don't want it
# to stop"): its spend is still charged here and logged, it just never refuses a question.
BUDGET_PER_SESSION_RS = 10.0
class ScriptedTalk:
    """His voice for a replay: what he says and when, so orders can be tested on a real tape without
    a race (26 Sep). script = [(laps_done, seconds_into_that_lap, words)], said once each, in order,
    at the first race snapshot past that point. The game's lap count, the same one his follows.
    laps_done = None: seconds is the sim time itself (tools/replay_orders.py aims at a known call)."""
    def __init__(self, source, script):
        self.source = source
        self.script = list(script)   # said in the order written
        self.lap_started = {}        # laps done -> sim time the game first showed it

    def poll(self):
        race = self.source.race
        if race is None or race.me is None or not self.source.new_race:
            return []
        self.lap_started.setdefault(race.me.laps, race.sim_time)
        heard = []
        while self.script:
            laps, into, words = self.script[0]
            if laps is None:
                if race.sim_time < into:
                    break
            else:
                start = self.lap_started.get(laps)
                if race.me.laps < laps or start is None or race.sim_time < start + into:
                    break
            self.script.pop(0)
            heard.append(push_to_talk.Heard(words, 1.5, 0, confidence=-0.2))
        return heard

    def set_track_words(self, corner_names):
        pass                         # no Whisper to prime: the words are already written

    def close(self):
        pass


class ReplaySource:
    def __init__(self, speed, tape_path=TAPE_PATH):
        self.speed = speed
        self.tape_path = tape_path
        self.race = None
        self.new_race = False
        self.near = None
        print("Connected.")
        print("Press Ctrl+C to stop.\n")

    def __iter__(self):
        try:
            with gzip.open(self.tape_path,"rt") as f:
                start_wall=time.perf_counter()
                start_sim=None
                for line in f:
                    as_dict = json.loads(line)
                    # v2 tapes interleave race lines with the car frames; each one belongs
                    # to the car frame written right after it. Old tapes have only car frames.
                    kind = as_dict.get("t")
                    if kind == "race":
                        self.race = race_snapshot_from_dict(as_dict)
                        self.new_race = True
                        continue
                    if kind == "near":
                        self.near = near_cars_from_dict(as_dict)
                        continue
                    as_data = CarState(**as_dict)
                    if start_sim is None:
                        start_sim=as_data.elapsed_time
                    if self.speed:
                        target=start_wall+(as_data.elapsed_time-start_sim)/self.speed
                        delay = target - time.perf_counter()
                        # running late: never skip the frame, just don't wait for it
                        if delay > 0:
                            time.sleep(delay)
                    yield as_data
                    # a snapshot is "new" for one frame only, and near cars belong to one frame
                    self.new_race = False
                    self.near = None


        except (EOFError, zlib.error):
            # a tape cut off when Apex was killed (24 Sep): everything up to the cut is real
            print("[the tape ends early - it was cut off; replayed up to the cut]")
        except KeyboardInterrupt:
            print("\nStopping...")
            print("Closed connection.")




@dataclass
class Event:
    
    kind: str
    sim_time:float
    speed_kmh:float
    corner:str|None=None
    conclusion: str | None = None
    lap_dist: float=0.0
    lap_count: int=0
    other_car: str | None = None     # steam id of the other car in a contact
    magnitude: float | None = None   # how hard an impact was

@dataclass
class CornerStat:
    lap_count:int
    corner:str 
    brake_onset: float|None
    min_speed: float|None 
    slow_zone: float|None
    coast: float|None
    time_s: float|None = None        # time from entering the corner window to leaving it
    throttle_on: float|None = None   # lap distance where he is back on the power after the slowest point

BRAKE_ON = 0.4
THROTTLE_ON = 0.5     # "on the power": half throttle after the slowest point (a controller trigger)
SLOW_ZONE_X = 10
PEDAL_OFF = 0.05

class CornerStats:
    def __init__(self):
        self.corner = None
        self.lap_count = None
        self.brake_onset = None
        self.min_speed = None
        self.prev_brake = 0.0
        self.frames = []
        self.prev_time = None

    def current_corner(self, real_lap_distance):
        return corner_at(current_corners, real_lap_distance)

    def update(self, frame, lap_count,real_lap_distance):
        now = self.current_corner(real_lap_distance)
        was = self.corner
        stat = None
        step = 0.0
        seconds = 0.0
        coast = 0.0
        slow_zone = 0
        if self.prev_time is not None:
            seconds = frame.elapsed_time - self.prev_time
            step = frame.speed_kmh/3.6 * seconds
        if now is None and was is not None:
            # LEAVING - hand back the row, then forget everything
            time_s = 0.0
            slowest = 0
            for index, (speed,meters,throttle,brake,seconds,distance) in enumerate(self.frames):
                if speed <= self.min_speed + SLOW_ZONE_X:
                    slow_zone+= meters
                if brake <PEDAL_OFF and throttle < PEDAL_OFF:
                    coast+=meters
                time_s += seconds
                if speed == self.min_speed:
                    slowest = index
            throttle_on = None
            for speed,meters,throttle,brake,seconds,distance in self.frames[slowest:]:
                if throttle >= THROTTLE_ON:
                    throttle_on = distance
                    break
            stat = CornerStat(self.lap_count, self.corner,
                              self.brake_onset, self.min_speed,slow_zone,coast,
                              round(time_s, 3), throttle_on)
            self.corner = None
            self.lap_count = None
            self.brake_onset = None
            self.min_speed = None
            self.frames = []

        elif now is not None:
            if now != was:
                # just arrived - start a fresh corner
                self.corner = now
                self.lap_count = lap_count
                self.brake_onset = None
                self.min_speed = None
                self.frames = []
           
            # measure - runs on the arrival frame too
            if self.min_speed is None or frame.speed_kmh < self.min_speed:
                self.min_speed = frame.speed_kmh
            
            self.frames.append((frame.speed_kmh, step,frame.throttle,frame.brake,seconds,real_lap_distance))
            # brake just crossed BRAKE_ON this frame: below it last frame, at or above it now
            brake_crossed = self.prev_brake < BRAKE_ON and frame.brake >= BRAKE_ON
            if self.brake_onset is None and brake_crossed:
                self.brake_onset = real_lap_distance

        self.prev_brake = frame.brake
        self.prev_time = frame.elapsed_time
        return stat


class Detector:
    def __init__(self):
        self.armed=False
        self.last_fire_time=0.0
        self.consecutive_true=0
        self.cooldown=3.0
        self.debounce_frames=2

    def current_corner(self,frame):
        return corner_at(current_corners, frame.lap_dist) or "the straight"

    def build_event(self,frame):
        return Event(kind=self.kind,sim_time=frame.elapsed_time,speed_kmh=frame.speed_kmh,corner=self.current_corner(frame),lap_dist=frame.lap_dist)

    def is_triggered(self,frame)->bool:
        raise NotImplementedError

    def update(self,frame):
        triggered= self.is_triggered(frame)
        event = None
        if not triggered:
            self.armed=False
            self.consecutive_true=0
            event=None

        elif triggered and self.consecutive_true<self.debounce_frames:
            self.consecutive_true+=1
            event=None

        elif triggered and self.consecutive_true>=self.debounce_frames and frame.elapsed_time-self.last_fire_time>self.cooldown and not self.armed:
            event=self.build_event(frame)
            self.last_fire_time=frame.elapsed_time
            self.armed=True
            self.consecutive_true+=1
        
        return event

class HardBrakingDetector(Detector):
        def __init__(self):
            super().__init__()
            self.kind="HARD_BRAKING"
        def build_event(self, frame):
            e= super().build_event(frame)
            corner = e.corner or "the straight" 
            e.conclusion = f"hard on the brakes into {corner}"
            return e
        def is_triggered(self, frame):
            return frame.brake>0.8 and frame.speed_kmh>30

SCORING_LAG_S = 0.5           # the game's lap count trails the line by up to a scoring update (5 a second)
REAL_LAP_S = 30.0             # green running before a crossing that makes it a lap finished, not the start


class LapCounter:
    def __init__(self):
        self.previous_lap_dist=0
        self.lap_count=0
        self.wrapped=False
        self.green_time = 0.0          # green running since his last line crossing
        self.last_time = None
        self.finished_at = None        # when he last crossed the line finishing a green lap
        self.laps_at_finish = None     # the game's laps done at that moment
    def update(self,frame):
        self.wrapped=False
        if self.previous_lap_dist-frame.lap_dist>1000:
            self.lap_count+=1
            self.wrapped=True
        self.previous_lap_dist=frame.lap_dist
        return self.lap_count

    def race_lap(self, game_laps, now, green):
        """The lap he is on as the game counts it: its laps done + 1. His own crossings (lap_count)
        made the formation lap "lap 1", so race lap 1 was "lap 2" for the coach, his reminders and
        the debrief (replay of 25 Sep night, 27 Sep). A lap he has just finished counts before the
        game's count catches up; the start line at lights out is not a lap finished."""
        if self.last_time is not None and green:
            self.green_time += max(0.0, now - self.last_time)
        self.last_time = now
        if self.wrapped:
            if self.green_time >= REAL_LAP_S:
                self.finished_at = now
                self.laps_at_finish = game_laps
            self.green_time = 0.0
        on_lap = game_laps + 1
        just_finished = self.finished_at is not None and now - self.finished_at < SCORING_LAG_S
        if just_finished and game_laps == self.laps_at_finish:
            on_lap += 1
        return on_lap

class LapDistance:
    def __init__(self):
        self.previous_lap_dist=None
        self.travelled_distance=0
        self.previous_time=0

    def update(self,frame):
        if frame.lap_dist!= self.previous_lap_dist:
            self.previous_lap_dist=frame.lap_dist
            self.travelled_distance = 0
        else:
            self.travelled_distance += frame.speed_kmh/3.6 * (frame.elapsed_time - self.previous_time)
        self.previous_time=frame.elapsed_time
        final_distance = self.previous_lap_dist + self.travelled_distance
        return final_distance
        

class LockUpDetector(Detector):
    def __init__(self, threshold=-0.3):
        self.threshold=threshold
        super().__init__()
        self.kind="LOCKUP"

    def build_event(self, frame):
            e=super().build_event(frame)
            corner = e.corner or "the straight" 
            e.conclusion = f"Front lockup under heavy braking into {corner} at {e.speed_kmh:.0f}km/h"
            return e
    def is_triggered (self, frame):
        slips = [slip_ratio(frame.wheel_rot[i], radii[i],frame.speed_kmh / 3.6) for i in range(4)]
        is_locked = (
            (slips[0] < self.threshold or slips[1] < self.threshold)   
            and frame.brake > 0.2                                      
            and frame.speed_kmh > 30                                   
        )
        return is_locked
        

class ThrottleLift(Detector):
    def __init__(self):
        super().__init__()
        self.kind="THROTTLE_LIFT"
        self.last_start_time = 0.0
        self.lifting=False
    
    def build_event(self, frame):
        e= super().build_event(frame)
        corner = e.corner or "the straight"
        e.conclusion = f"off throttle and coasting at {corner}, no braking"
        return e
    
    def is_triggered(self, frame):
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
    def __init__(self):
        self.previous_corner=None


    def update(self,frame):
        event = None
        current_corner = corner_at(current_corners, frame.lap_dist)
        
        
        if current_corner is not None and current_corner!=self.previous_corner:
            event=  Event(kind="CORNER_ENTRY",sim_time=frame.elapsed_time, speed_kmh=frame.speed_kmh,corner=current_corner,lap_dist=frame.lap_dist,conclusion = f"entering {current_corner} at {frame.speed_kmh:.0f}km/h")
        self.previous_corner = current_corner

        return event

CONTACT_NEAR_M = 10.0   # a car this close at the moment of impact is the car you touched
SAME_INCIDENT_S = 5.0   # a hit within 5 s of the last one is the same incident (LMU: 266, 266.4, 270.9 s)

class ContactDetection:
    """An impact is when the game's last-impact time moves forward.
    With a car within 10 m it is CONTACT (and we know who), otherwise IMPACT (a wall)."""

    def __init__(self):
        self.last_seen = None
        self.first_frame = True

    def nearest_car(self, frame, near):
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
        if self.first_frame:
            # whatever impact the game remembers on the first frame happened before this session
            self.first_frame = False
            self.last_seen = frame.last_impact_time
            return None
        # None = no impact yet (and old tapes carry no impact data at all)
        if frame.last_impact_time is None or frame.last_impact_time == self.last_seen:
            return None
        previous = self.last_seen
        self.last_seen = frame.last_impact_time
        # one incident fires several hits within seconds (LMU logged 266, 266 and 271 s for one
        # moment at Le Mans): hits that close together are the same incident
        if previous is not None and frame.last_impact_time - previous < SAME_INCIDENT_S:
            return None

        corner = corner_at(current_corners, frame.lap_dist) or "the straight"
        magnitude = frame.last_impact_magnitude
        other = self.nearest_car(frame, near)
        if other is None:
            return Event(kind="IMPACT", sim_time=frame.elapsed_time, speed_kmh=frame.speed_kmh,
                         corner=corner, lap_dist=frame.lap_dist, magnitude=magnitude,
                         conclusion=f"hit something at {corner}, no car near")
        driver = "a car"
        steam_id = str(other.id)
        if race is not None:
            for opponent in race.opponents:
                if opponent.id == other.id:
                    driver = opponent.driver
                    steam_id = identity(opponent)
        return Event(kind="CONTACT", sim_time=frame.elapsed_time, speed_kmh=frame.speed_kmh,
                     corner=corner, lap_dist=frame.lap_dist, magnitude=magnitude,
                     other_car=steam_id, conclusion=f"contact with {driver} at {corner}")

class OffTrackDetector(Detector):
    def __init__(self):
        super().__init__()
        self.kind="OFF_TRACK"

    def build_event(self, frame):
        e= super().build_event(frame)
        surface = "Road"
        for s in frame.surface:
            if s==2:
                surface = "grass"
            elif s==4:
                surface= "gravel"
        e.conclusion = f"Ran Wide onto {surface} at {e.corner}, {e.speed_kmh:.0f}km/h"
        return e

    def is_triggered(self,frame):
        off_wheels=0
        for s in frame.surface:
            if s in (2,4):
                off_wheels+=1
        return off_wheels>=2

# Rear snap on the brakes, the problem Gourav cannot work out by feel (23 Sep 2026).
# In a steady corner the car rotates at lateral_g / speed. When it rotates much faster
# than that while braking with lock on, the rear has let go.
# GUESSED thresholds: tune them on his tapes once they carry steering.
SNAP_BRAKE = 0.15            # on the brakes
SNAP_STEERING = 0.05         # with some lock on (trail braking)
SNAP_MIN_SPEED_MS = 15.0     # 54 km/h: slower than this, yaw means nothing
SNAP_RATIO = 1.4             # rotating 40% faster than the corner explains
SNAP_MARGIN = 0.1            # rad/s, so tiny wobbles on a straight never count

class RearSnapDetector(Detector):
    def __init__(self):
        super().__init__()
        self.kind = "REAR_SNAP"

    def build_event(self, frame):
        e = super().build_event(frame)
        e.conclusion = f"rear snapped under braking at {e.corner}"
        return e

    def is_triggered(self, frame):
        if frame.steering is None:
            return False                 # old tapes have no steering channel
        speed_ms = frame.speed_kmh / 3.6
        if frame.brake < SNAP_BRAKE or abs(frame.steering) < SNAP_STEERING or speed_ms < SNAP_MIN_SPEED_MS:
            return False
        explained = abs(frame.accel_lat) / speed_ms
        return abs(frame.yaw_rate) > SNAP_RATIO * explained + SNAP_MARGIN

# Wheelspin on exit: a rear wheel turning much faster than the car moves, on the throttle.
# Only on v2 tapes (steering recorded), so the detector golden of the old tapes stays exact.
SPIN_SLIP = 0.15             # rear tyre 15% faster than the ground (see slip_ratio)
SPIN_THROTTLE = 0.5

class WheelspinDetector(Detector):
    def __init__(self):
        super().__init__()
        self.kind = "WHEELSPIN"

    def build_event(self, frame):
        e = super().build_event(frame)
        e.conclusion = f"wheelspin on the exit of {e.corner}"
        return e

    def is_triggered(self, frame):
        if frame.steering is None or frame.throttle < SPIN_THROTTLE or frame.speed_kmh < 30:
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


SPUN_DEGREES = 90.0           # pointing more than this away from where it is going: spun
SLIDE_DEGREES = 15.0          # a slide worth a word
SLIDE_OVER_DEGREES = 5.0      # back under this: the slide is caught
SLIDE_MIN_KMH = 60.0
SPIN_MIN_KMH = 5.0
HIT_BEFORE_SPIN_S = 5.0       # an impact this soon before the spin: he was hit


class SpinDetector(Detector):
    """Live 25 Sep: he was punted round at Porsche Curves (nose went 6 -> 180 degrees, yaw rate
    never over 0.9) and nothing was said; a 19-degree slide he caught at Mulsanne Chicane 1 was
    called "Spun" (yaw spiked). The old rule was yaw rate over 1.7 rad/s. A spin is now the car
    pointing more than 90 degrees from where it is going. Old tapes without position keep the
    yaw rule."""
    def __init__(self):
        super().__init__()
        self.kind = "SPIN"
        self.cooldown = 10.0          # one incident can swing past 90 degrees twice (25 Sep: 1045 and 1048 s)
        self.last_fire_time = -self.cooldown     # free to fire from the first second
        self.recent = []              # the last ~0.1 s of frames, for the direction of travel
        self.last_car_contact = None  # sim time of the last contact WITH A CAR (set by the race loop)

    def build_event(self, frame):
        e = super().build_event(frame)
        corner = e.corner or "the straight"
        # "you got hit" only when the contact detector saw a car there: a wall is an impact too
        hit = self.last_car_contact is not None and 0 <= frame.elapsed_time - self.last_car_contact <= HIT_BEFORE_SPIN_S
        e.conclusion = f"spun after contact at {corner}" if hit else f"spun at {corner}"
        return e

    def is_triggered(self, frame):
        self.recent.append(frame)
        while len(self.recent) > 1 and frame.elapsed_time - self.recent[0].elapsed_time > 0.1:
            self.recent.pop(0)
        if frame.pos is None or frame.ori is None:
            return abs(frame.yaw_rate) > 1.7
        slip = slip_angle(frame, self.recent[0])
        return slip is not None and slip > SPUN_DEGREES and frame.speed_kmh > SPIN_MIN_KMH


class SlideCaughtDetector:
    """A big moment he saved: more than 15 degrees of slide above 60 km/h that came back
    straight without ever passing 90 (a spin). Fires on the save, so it is praise, not a
    warning. His words, 25 Sep: "tell me mate you caught a big moment", not "you spun"."""
    def __init__(self):
        self.kind = "SLIDE_CAUGHT"
        self.recent = []
        self.worst = None             # the biggest slide angle of the slide in progress
        self.cooldown = 10.0
        self.last_fire_time = -100.0

    def update(self, frame):
        self.recent.append(frame)
        while len(self.recent) > 1 and frame.elapsed_time - self.recent[0].elapsed_time > 0.1:
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
                self.worst = None     # that was a spin, the spin detector has it
            return None
        if slip < SLIDE_OVER_DEGREES:
            worst = self.worst
            self.worst = None
            if frame.elapsed_time - self.last_fire_time < self.cooldown:
                return None
            self.last_fire_time = frame.elapsed_time
            corner = corner_at(current_corners, frame.lap_dist) or "the straight"
            return Event(kind=self.kind, sim_time=frame.elapsed_time, speed_kmh=frame.speed_kmh,
                         corner=corner, lap_dist=frame.lap_dist, magnitude=round(worst),
                         conclusion=f"caught a {round(worst)} degree slide at {corner}")
        return None
    

 
class Recorder:
    def __init__(self,path):
        # path is unique per session - a hardcoded name silently overwrote the
        # previous session's tape every run.
        self.path = path
        self.q = Queue()
        self.writer_thread = threading.Thread(target=self._writer_loop)
        self.writer_thread.start()

    def record(self,frame):
        self.q.put(frame)

    def _writer_loop(self):
        written = 0
        with gzip.open(self.path,"wt") as f:
            while True:
                item = self.q.get()
                if item is None:
                    break

                as_dict = asdict(item)
                as_text= json.dumps(as_dict)
                f.write(as_text + "\n")
                written += 1
                # every few seconds, a sync point: if Apex is killed, everything up to here
                # still reads back (24 Sep: a force-stopped run left a tape cut mid-write)
                if written % 600 == 0:
                    f.flush()

    
    def stop(self):
        self.q.put(None)
        self.writer_thread.join()

GAME_PHASE_OVER = 8      # mGamePhase: the session has finished
# Phase 8 comes when the LEADER takes the flag. On 23 Sep Apex stopped right there, with
# Gourav still 400 m from his own finish line: every non-leader lost the end of the race.
# So Apex waits for my own car to finish, then leaves the engineer time to call the result.
FINISHED_GRACE_S = 10.0
FLAG_TIMEOUT_S = 420.0   # a car that never takes the flag (parked, crashed out): stop anyway


def database_file(conn):
    """The file behind this connection, so the agent can open its own read-only one."""
    return conn.execute("PRAGMA database_list").fetchone()[2] or None


def due_reminders(reminders, lap, now):
    """The reminders he asked for (through the agent) that fall on this lap: said at the line."""
    due = [r for r in reminders if r["remind_lap"] <= lap]
    for reminder in due:
        reminders.remove(reminder)
    return [Call(seat="race_engineer", kind="REMINDER", sim_time=now, priority=RACE_CONTROL, ttl=20.0,
                 conclusion=f"Reminder: {r['what']}.", template=f"Reminder: {r['what']}.", asked=True)
            for r in due]


def contacts_by_car(conn, session_id):
    """Contacts this session, per other car, for the agent's driver tool."""
    counts = {}
    for other_car, count in conn.execute("SELECT other_car, COUNT(*) FROM events WHERE session_id = ? AND kind = 'CONTACT' GROUP BY other_car",
                                         (session_id,)):
        counts[other_car] = count
    return counts


def run_session(replay, replay_speed, tape_path=TAPE_PATH, out_loud=None, clean=False, persona=None, launch_id=None,
                voice=None, script=None):
    """One LMU session, start to finish. Returns the database id of the session.

    script:   replays only: what he says and when (see ScriptedTalk), no microphone, no coach.
    out_loud: speak through the speakers (default) or print lines (fast replays, tests).
    clean:    no swearing, for recordings other people will hear.
    persona:  who phrases the lines; tests pass a fake so no LLM call is ever made."""
    global current_corners
    REPLAY = replay
    REPLAY_SPEED = replay_speed
    if out_loud is None:
        # a replay at max speed prints its lines, like v1 did
        out_loud = not (REPLAY and not REPLAY_SPEED)

    if REPLAY:
        source = ReplaySource(REPLAY_SPEED, tape_path)
        tape_out = tape_path
    else:
        info = MMapControl(LMUConstants.LMU_SHARED_MEMORY_FILE, LMUObjectOut)
        info.create(0)
        source = LiveSource(info)
        # wall-clock is correct HERE: this names a file for a human, it is not
        # telemetry timing. All event timing still comes from mElapsedTime.
        tape_out = f"tape_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl.gz"
        tele_recorder = Recorder(tape_out)
        print(f"Recording to {tape_out}")

    spin_detector = SpinDetector()
    detectors = [
        HardBrakingDetector(),
        LockUpDetector(),
        CornerEntryDetection(),
        ThrottleLift(),
        OffTrackDetector(),
        spin_detector,
        SlideCaughtDetector(),
        RearSnapDetector(),
        WheelspinDetector()]

    lap_counter = LapCounter()
    lap_distance = LapDistance()
    corner_stats = CornerStats()

    # Old tapes carry no track name, so they keep the Monza map they were driven on.
    current_corners = MONZA_CORNERS
    track = None
    session_type = None
    learning_track = False
    track_learner = TrackMapLearner()

    contacts = ContactDetection()
    # the result of the session, read from the race snapshots as they arrive
    grid = None
    final_place = None
    car_settings = None        # (tc, abs, rear bias, motor map) he last ran under green
    first_limit_steps = None
    last_limit_steps = None
    last_opponents = []

    # the seats that watch the whole race (the performance seat rides on the detectors)
    performance = PerformanceEngineer()
    racecraft = Racecraft(performance)
    # the one picture of the race (25 Sep): fed here, before any seat, and only read by them
    model = RaceModel()
    racecraft.share(model)
    recall = MemoryRecall()
    engineer = RaceEngineer()
    strategist = Strategist()
    seats = [Spotter(), engineer, strategist, performance, racecraft, recall, TrackAwareness(), QualifyingEngineer()]
    settle = RaceSettle()

    governor = Governor()
    # push-to-talk (M9): only live, and Apex races on without it if it is not set up
    answers = Answers(governor, engineer, strategist, performance, clean)
    # his standing orders: kept for the race, honoured by every seat and the coach (26 Sep)
    orders = StandingOrders()
    governor.orders = orders
    engineer.orders = orders
    answers.model = model          # the fixed answers read the same gaps as every seat
    reminders = []            # {"remind_lap", "what"}: set by the agent, said at the line
    heard_confidence = {}     # question -> Whisper's confidence, until the coach answers it
    talk = None
    agent = None
    if not REPLAY:
        talk = push_to_talk.start_if_set_up()
    elif script:
        talk = ScriptedTalk(source, script)
    budget = Budget(cap_rs=BUDGET_PER_SESSION_RS)
    if talk is not None and not REPLAY:
        agent = RaceAgent(budget, clean)      # after the budget: it spends from it (24 Sep crash)
        agent.orders = orders
    # one voice for the whole launch when apex.py passes it in: the cloned voice takes about
    # 30 s to load and warm up, which must not happen again between qualifying and the race
    own_voice = voice is None
    if own_voice:
        voice = Voice(out_loud, clone=False)        # the cloned voice is off (25 Sep, his call)
    if persona is None:
        persona = Persona(clean=clean)
    desk = RadioDesk(voice, persona, budget, clean)

    conn = None
    session_id = None
    saw_running = False
    flag_seen_at = None
    finished_at = None
    end_reason = "tape_end" if REPLAY else "stopped_by_driver"

    def log_finished_lines():
        for result in desk.drain():
            call = result["call"]
            if result.get("llm_only"):
                save_llm_call(conn, session_id, call.seat, result["llm"])
                continue
            save_radio(conn, session_id, call, result["status"], result["line"],
                       result["reason"], result["latency_ms"])
            if result["status"] == "spoken" and call.seat != "spotter" and call.kind != "ANSWER_REPEAT":
                answers.last_line = result["line"]      # "say again" repeats the engineer, not "Clear"

    def log_dropped_calls():
        for call, reason in governor.dropped:
            desk.forget(call)
            save_radio(conn, session_id, call, reason)
        governor.dropped = []

    try:
        conn = connect_db()
        session_started = datetime.now().isoformat(timespec="seconds")
        session_id = start_session(conn, session_started, tape_out, REPLAY_SPEED, launch_id)
        orders.load(conn)          # what he said "for good" in earlier races ("never tell me the gaps")
        # what team memory knows about every rival, for the racecraft plans
        lap_one_facts = memory_facts(conn, "lap_one")
        if lap_one_facts:
            recall.lap_one = lap_one_facts[0]
        for rival_fact in memory_facts(conn, "rival"):
            racecraft.rivals[rival_fact["subject"]] = rival_fact["summary"]
        # everything team memory knows about him, for the agent's my_habits tool
        team_habits = []
        for kind in ("lap_one", "corner_habit", "pass_attempts", "clean_race", "rival"):
            for fact in memory_facts(conn, kind):
                team_habits.append(fact["summary"])
        voice.play_urgent("RADIO_CHECK", "Radio check. I'm with you.")

        # GUESSED — mLapInvalidated never observed True (n=33485)
        validity = 1                      # 1 = valid, 0 = invalidated
        for frame in source:
            own_laps = lap_counter.update(frame)      # his own line crossings: where laps begin and end
            lap_count = own_laps
            if session_type in RACE_SESSIONS and source.race is not None and source.race.me is not None:
                green = source.race.session.game_phase == GREEN_PHASE
                lap_count = lap_counter.race_lap(source.race.me.laps, frame.elapsed_time, green)
            real_lap_distance = lap_distance.update(frame)

            # the track decides where the corners are, so it must be settled before
            # anything below tags a corner onto a stat or an event
            if track is None and source.race is not None:
                track = source.race.session.track
                session_type = source.race.session.session
                me = source.race.me
                set_session_track(conn, session_id, track, session_type, source.race.session.game_phase,
                                  me.car_class if me else None, me.car_model if me else None)
                current_corners = corners_for_track(track)
                if talk is not None and current_corners:
                    talk.set_track_words([c["name"] for c in current_corners])
                # team memory for this track: the habits worth a reminder
                for habit in memory_facts(conn, "corner_habit", track) + memory_facts(conn, "contact_corner", track):
                    recall.corner_habits.setdefault(habit["subject"], habit)
                learning_track = current_corners is None
                if learning_track:
                    print(f"[track: {track} - new track, learning its corners from your laps]")
                else:
                    print(f"[track: {track} - corner map loaded]")
            if source.new_race and source.race.me is not None:
                me = source.race.me
                if grid is None:
                    grid = me.grid
                if first_limit_steps is None:
                    first_limit_steps = me.track_limit_steps
                last_limit_steps = me.track_limit_steps
                final_place = me.place
                last_opponents = source.race.opponents
                if source.race.session.game_phase == GREEN_PHASE:
                    car_settings = (me.tc, me.abs, me.brake_bias_rear, me.motor_map)
            track_learner.add(own_laps, real_lap_distance, frame.brake, frame.throttle, frame.accel_lat)
            if learning_track and lap_counter.wrapped:
                learned = track_learner.corners(own_laps)
                if learned is not None:
                    current_corners = learned

            stat = corner_stats.update(frame, lap_count, real_lap_distance)
            if stat:
                print(stat)
                save_corner_stat(conn, session_id, stat)

            if lap_counter.wrapped:
                save_lap(conn, session_id, own_laps - 1, validity)
                validity = 1
                for reminder in due_reminders(reminders, lap_count, frame.elapsed_time):
                    if governor.offer(reminder):
                        desk.prepare(reminder)

            if frame.lap_invalidated:
                validity = 0

            # the seats raise calls ...
            frame_events = []
            contact = contacts.update(frame, source.near, source.race)
            if contact is not None:
                contact.lap_count = lap_count
                print(contact)
                save_event(conn, session_id, contact)
                frame_events.append(contact)
                if contact.kind == "CONTACT":
                    spin_detector.last_car_contact = contact.sim_time
                if contact.kind in ("CONTACT", "IMPACT"):
                    performance.saw_hit(contact.sim_time)
            for detector in detectors:
                event = detector.update(frame)
                if event:
                    event.lap_count = lap_count
                    print(event)
                    event_id = save_event(conn, session_id, event)
                    frame_events.append(event)
                    call = performance.call_for_event(event, event_id)
                    if call is not None and governor.offer(call):
                        desk.prepare(call)

            corner_now = corner_at(current_corners, real_lap_distance)
            if source.new_race and source.race is not None:
                model.see_race(source.race, frame.elapsed_time)
            model.see_me(frame.lap_dist, frame.elapsed_time)
            moment = Moment(frame=frame, race=source.race, new_race=source.new_race, near=source.near,
                            lap_count=lap_count, lap_wrapped=lap_counter.wrapped, corner=corner_now,
                            track=track, corner_stat=stat, session_type=session_type,
                            corners=current_corners, events=frame_events, model=model)
            # first: is the start still chaos? Then nothing but the spotter, flags and answers
            for call in settle.update(moment):
                governor.settled = settle.settled
                if governor.offer(call):
                    desk.prepare(call)
            governor.settled = settle.settled
            for seat in seats:
                for call in seat.update(moment):
                    if governor.offer(call):
                        desk.prepare(call)

            # ... and the radio decides what goes on air, on sim time only
            # quiet while actually braking or cornering hard. The map windows were the rule
            # before, and at Le Mans they are long (Porsche Curves 1.4 km): 14 calls expired
            # waiting for a straight in the first live race (23 Sep 2026)
            in_corner = frame.brake > 0.2 or abs(frame.accel_lat) >= TURNING
            governor.lap = lap_count
            heard_now = []
            if talk is not None:
                try:
                    heard_now = talk.poll()
                except Exception as error:
                    # push-to-talk must never end a race: it switches itself off instead
                    print(f"[push-to-talk off for this session: {error.__class__.__name__}: {error}]")
                    talk = None
            if talk is not None or heard_now:
                for heard in heard_now:
                    heard.text = fix_mishearing(heard.text)
                    if garbled(heard.text, getattr(heard, "confidence", None)) and intent_of(heard.text) is None:
                        say_again = Call(seat="race_engineer", kind="ANSWER_UNHEARD", sim_time=frame.elapsed_time,
                                         priority=RACE_CONTROL, ttl=10.0, conclusion="Didn't catch that, mate. Say again.",
                                         template="Didn't catch that, mate. Say again.", asked=True,
                                         facts={"heard": heard.text, "confidence": heard.confidence})
                        print(f"[garbled ({heard.confidence}): {heard.text!r} -> say again]")
                        if governor.offer(say_again):
                            desk.prepare(say_again)
                        continue
                    heard_order = None if is_mark(heard.text) else orders.hear(heard.text, lap_count, frame.elapsed_time)
                    if heard_order is not None:
                        # an order: kept for the race and said back, no model needed
                        words, given = heard_order
                        ack = Call(seat="race_engineer", kind="ANSWER_ORDER", sim_time=frame.elapsed_time,
                                   priority=RACE_CONTROL, ttl=10.0, conclusion=words, template=words, asked=True,
                                   facts={"heard": heard.text, "orders": [f"{o.topic}={o.stance}" for o in given],
                                          "confidence": getattr(heard, "confidence", None)})
                        orders.save(conn)
                        print(f"[order: {heard.text!r} -> {ack.facts['orders'] or 'back to normal'}]")
                        if governor.offer(ack):
                            desk.prepare(ack)
                        continue
                    if (agent is not None and needs_agent(heard.text) and source.race is not None
                            and source.race.me is not None):
                        # a real question: the agent looks at a still picture of the race
                        snapshot = Snapshot(source.race, lap_count, real_lap_distance, current_corners,
                                            engineer, strategist, performance, racecraft, governor,
                                            team_habits, contacts_by_car(conn, session_id),
                                            db_path=database_file(conn), session_id=session_id, model=model)
                        agent.ask(heard.text, snapshot, frame.elapsed_time)
                        # logged with the answer: the "say again" threshold is tuned from these
                        heard_confidence[heard.text] = getattr(heard, "confidence", None)
                        voice.play_bank_if_free("STAND_BY", "Copy. Stand by.")
                        print(f"[asked the agent: {heard.text!r}]")
                        continue
                    answer = answers.answer(heard.text, source.race, lap_count, frame.elapsed_time)
                    answer.facts["transcribe_ms"] = heard.transcribe_ms
                    answer.facts["confidence"] = getattr(heard, "confidence", None)
                    print(f"[asked: {heard.text!r} -> {answer.kind}: {answer.template}]")
                    if governor.offer(answer):
                        desk.prepare(answer)
            if agent is not None:
                for result in agent.finished():
                    for spent in result["costs"]:
                        save_llm_call(conn, session_id, "agent", spent)
                    heard_text = result["call"].facts.get("heard", "")
                    result["call"].facts["confidence"] = heard_confidence.pop(heard_text, None)
                    gave_up = result["call"].template.startswith(("No clean answer", "Radio's lagging, mate. Ask me again"))
                    if gave_up and intent_of(heard_text) is not None and source.race is not None:
                        # live 24 Sep: "what times do I need to catch the car ahead?" got "no clean
                        # answer" while the quick lane had the lap time. It speaks instead.
                        quick = answers.answer(heard_text, source.race, lap_count, frame.elapsed_time)
                        quick.facts["agent_gave_up"] = result["call"].template
                        result["call"] = quick
                    for action in result.get("actions", []):
                        reminders.append(action)             # "remind me to box on lap 12"
                    for topic, stance in result.get("orders", []):
                        try:
                            orders.set(topic, stance, heard_text, lap_count, frame.elapsed_time, "coach")
                            print(f"[order from the coach: {topic}={stance}]")
                        except ValueError:
                            print(f"[coach gave an order that does not exist: {topic}={stance}]")
                    if result.get("orders"):
                        orders.save(conn)
                    # .get: when the coach gave up, the quick answer stands in and has no timing
                    # (live 25 Sep: KeyError 'seconds' ended the session)
                    print(f"[agent: {result['call'].template}  ({result['call'].facts.get('seconds', '?')} s)]")
                    if governor.offer(result["call"]):
                        desk.prepare(result["call"])
            on_air = governor.step(frame.elapsed_time, in_corner)
            if on_air is not None:
                if on_air.urgent:
                    played = voice.play_urgent(on_air.kind, on_air.template)
                    save_radio(conn, session_id, on_air, "spoken" if played else "no_bank_line",
                               on_air.template, latency_ms=0)
                elif not desk.submit(on_air):
                    save_radio(conn, session_id, on_air, "queue_full")
            desk.latest_sim_time = frame.elapsed_time
            log_dropped_calls()
            log_finished_lines()

            if not REPLAY:
                # race lines go first: on replay each one belongs to the car frame after it
                if source.new_race:
                    tele_recorder.record(source.race)
                if source.near is not None:
                    tele_recorder.record(source.near)
                tele_recorder.record(frame)

            # the session ends itself: no Ctrl+C needed at the chequered flag. Only a session
            # Apex saw running: started on a results screen, it would end, restart and end again
            if source.race is not None:
                if source.race.session.game_phase < GAME_PHASE_OVER:
                    saw_running = True
                if source.race.session.game_phase == GAME_PHASE_OVER and saw_running:
                    if flag_seen_at is None:
                        flag_seen_at = frame.elapsed_time
                    me = source.race.me
                    my_race_done = me is None or me.finish_status != 0 or me.in_pits
                    if my_race_done and finished_at is None:
                        finished_at = frame.elapsed_time
                    grace_over = finished_at is not None and frame.elapsed_time - finished_at >= FINISHED_GRACE_S
                    if grace_over or frame.elapsed_time - flag_seen_at >= FLAG_TIMEOUT_S:
                        end_reason = "session_over"
                        break
                if session_type is not None and source.race.session.session != session_type:
                    end_reason = "session_changed"
                    break

    except KeyboardInterrupt:
        end_reason = "stopped_by_driver"

    finally:
        if talk is not None:
            talk.close()
        desk.stop()
        if own_voice:
            voice.close()
        if not REPLAY:
            tele_recorder.stop()
        if learning_track and track:
            learned = track_learner.corners(lap_counter.lap_count)
            if learned is not None:
                laps_used = len(track_learner.complete_laps(lap_counter.lap_count))
                save_map(track, learned, laps_used)
                print(f"[saved the corner map for {track}, learned from {laps_used} laps]")
        decision_hash = governor.decision_hash()
        if conn is not None and session_id:
            log_dropped_calls()
            log_finished_lines()
            ended_at = datetime.now().isoformat(timespec="seconds")
            finish_session(conn, session_id, decision_hash, end_reason, ended_at)
            if final_place is not None:
                strikes = None
                if first_limit_steps is not None:
                    strikes = last_limit_steps - first_limit_steps
                save_session_result(conn, session_id, grid, final_place, strikes)
                save_rivals(conn, session_id, last_opponents)
            if car_settings is not None:
                save_car_settings(conn, session_id, *car_settings)
            save_opponent_corners(conn, session_id, performance.opponents.rows)
            save_pass_attempts(conn, session_id, racecraft.attempts)
            print_corner_report(conn, session_id)
            print(f"session {session_id} ended: {end_reason}   LLM spend ~Rs {budget.spent_rs:.2f}")
        if conn is not None:
            conn.close()
        print(decision_hash)
    return session_id


if __name__ == "__main__":
    run_session(True,1)

