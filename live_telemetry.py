import time
import math
import threading
import json,gzip,hashlib



from sharedmemory import MMapControl
from lmu_data import LMUObjectOut, LMUConstants
from openai import OpenAI
from tts import speak
from dataclasses import dataclass,asdict
from queue import Full, Empty, Queue
from datetime import datetime
from coach import phrase_event
from coach import radio_check
from memory import connect_db,start_session,save_event,save_spoken,finish_session,save_lap,save_corner_stat,print_corner_report

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




info = MMapControl(LMUConstants.LMU_SHARED_MEMORY_FILE, LMUObjectOut)
info.create(0)

scoring   = info.data.scoring.vehScoringInfo       # timing / position
telemetry = info.data.telemetry.telemInfo  

def match_opponents():
    opponents=[]
    telemetry_by_id= {t.mID:t for t in telemetry if t.mID!=0}
    for s in scoring:
        if s.mID !=0:
            telemetry_info=telemetry_by_id.get(s.mID)
            opponents.append((s,telemetry_info))

    return opponents


MONZA_CORNERS = [
    {"name": "T1 Rettifilo",   "start":  760, "end":  970},
    {"name": "T3 Curva Grande","start": 1250, "end": 1760},
    {"name": "T4 Roggia",      "start": 1995, "end": 2140},
    {"name": "T6 Lesmo 1",     "start": 2450, "end": 2520},
    {"name": "T7 Lesmo 2",     "start": 2785, "end": 2850},
    {"name": "T8 Ascari",      "start": 3805, "end": 4280},
    {"name": "T11 Parabolica", "start": 5000, "end": 5400},
]

radii = [FRONT_RADIUS, FRONT_RADIUS, REAR_RADIUS, REAR_RADIUS]
class LiveSource:
    def __init__(self,info):
        self.info=info
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
            elapsed_time=my_car.mElapsedTime
        )
    


    def __iter__(self):
        last_time= None
        try:
            
            while True:
                self.info.update()
                state = self.read_state()
                if state.elapsed_time!=last_time:
                    last_time= state.elapsed_time
                    yield state
                time.sleep(0.002)

        except KeyboardInterrupt:
            print("\nStopping...")
            self.info.close()
            print("Closed connection.")


REPLAY = True
REPLAY_SPEED=None
TAPE_PATH = "tape_60hz_clean.jsonl.gz"
class ReplaySource:
    def __init__(self):
        print("Connected.")
        print("Press Ctrl+C to stop.\n")

    def __iter__(self):
        try:
            with gzip.open(TAPE_PATH,"rt") as f:
                start_wall=time.perf_counter()
                start_sim=None
                for line in f:
                    as_dict = json.loads(line)
                    as_data = CarState(**as_dict)
                    if start_sim is None:
                        start_sim=as_data.elapsed_time
                    if REPLAY_SPEED:
                        target=start_wall+(as_data.elapsed_time-start_sim)/REPLAY_SPEED
                        delay = target - time.perf_counter()
                        if delay<0:
                            yield as_data
                            continue
                        else:
                            time.sleep(delay)
                    yield as_data
                    

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

@dataclass
class CornerStat:
    lap_count:int
    corner:str 
    brake_onset: float|None
    min_speed: float|None 

BRAKE_ON = 0.4

class CornerStats:
    def __init__(self):
        self.corner = None
        self.lap_count = None
        self.brake_onset = None
        self.min_speed = None
        self.prev_brake = 0.0

    def current_corner(self, frame):
        corner = None
        for x in MONZA_CORNERS:
            if x["start"] <= frame.lap_dist < x["end"]:
                corner = x["name"]
        return corner

    def update(self, frame, lap_count):
        now = self.current_corner(frame)
        was = self.corner
        stat = None

        if now is None and was is not None:
            # LEAVING - hand back the row, then forget everything
            stat = CornerStat(self.lap_count, self.corner,
                              self.brake_onset, self.min_speed)
            self.corner = None
            self.lap_count = None
            self.brake_onset = None
            self.min_speed = None

        elif now is not None:
            if now != was:
                # just arrived - start a fresh corner
                self.corner = now
                self.lap_count = lap_count
                self.brake_onset = None
                self.min_speed = None
            # measure - runs on the arrival frame too
            if self.min_speed is None or frame.speed_kmh < self.min_speed:
                self.min_speed = frame.speed_kmh
            if self.brake_onset is None and self.prev_brake < BRAKE_ON <= frame.brake:
                self.brake_onset = frame.lap_dist

        self.prev_brake = frame.brake
        return stat


class Detector:
    def __init__(self):
        self.armed=False
        self.last_fire_time=0.0
        self.consecutive_true=0
        self.cooldown=3.0
        self.debounce_frames=2

    def current_corner(self,frame):
        corner= "the straight"
        for x in MONZA_CORNERS:
            if x["start"]<=frame.lap_dist<x["end"]:
                corner = x["name"]
        return corner

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

class lapCounter:
    def __init__(self):
        self.previous_lap_dist=0
        self.lap_count=0
        self.wrapped=False
    def update(self,frame):
        self.wrapped=False
        if self.previous_lap_dist-frame.lap_dist>1000:
            self.lap_count+=1
            self.wrapped=True
        self.previous_lap_dist=frame.lap_dist
        return self.lap_count
        

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
        current_corner= None
        event = None
        for x in MONZA_CORNERS:
            if x["start"]<=frame.lap_dist<x["end"]:
                current_corner = x["name"]
        
        
        if current_corner is not None and current_corner!=self.previous_corner:
            event=  Event(kind="CORNER_ENTRY",sim_time=frame.elapsed_time, speed_kmh=frame.speed_kmh,corner=current_corner,lap_dist=frame.lap_dist,conclusion = f"entering {current_corner} at {frame.speed_kmh:.0f}km/h")
        self.previous_corner = current_corner

        return event

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

class SpinDetector(Detector):
    def __init__(self):
        super().__init__()
        self.kind="SPIN"
    def build_event(self, frame):
        e= super().build_event(frame)
        corner = e.corner or "the straight "
        e.conclusion = f"rear stepped out at {corner}"
        return e
    def is_triggered(self,frame):
        return abs( frame.yaw_rate)>1.7 
    

 
class Recorder:
    def __init__(self):
        self.q = Queue()
        self.writer_thread = threading.Thread(target=self._writer_loop)
        self.writer_thread.start()

    def record(self,frame):
        self.q.put(frame)

    def _writer_loop(self):
        with gzip.open("tape.jsonl.gz","wt") as f:
            while True:
                item = self.q.get()
                if item is None:
                    break
                
                as_dict = asdict(item)
                as_text= json.dumps(as_dict)
                f.write(as_text + "\n")

    
    def stop(self):
        self.q.put(None)
        self.writer_thread.join()


if(REPLAY):
    source = ReplaySource()
else:
    source= LiveSource(info)
    tele_recorder= Recorder()

detector_priority = {"SPIN": 1, "OFF_TRACK": 2, "LOCKUP": 3, "THROTTLE_LIFT": 4}
INCIDENTS = {"SPIN", "OFF_TRACK", "LOCKUP"}
SPEAK_COOLDOWN = 5.0

detectors=[
    HardBrakingDetector(),
     LockUpDetector(),
     CornerEntryDetection(),
     ThrottleLift(),
     OffTrackDetector(),
     SpinDetector()]

lap_counter = lapCounter()
corner_stats = CornerStats()

speak_queue = Queue(maxsize=1)
spoken_results = Queue()
def worker_function():
    STALE_THRESHOLD=6.0
    while True:
        item = speak_queue.get()
        if item is None:
            break
        event, event_id = item
        line = phrase_event(event)
        if line:
            spoken_at = latest_sim_time
            if spoken_at - event.sim_time < STALE_THRESHOLD:
                if not REPLAY_SPEED and REPLAY:
                    print(line)
                else:
                    speak(line)
                    spoken_results.put((event_id, spoken_at, line))

            else:
                print(f"[stale line dropped: {event.kind}@{event.corner}]")


def drain_spoken(conn):
    while True:
        try:
            event_id, spoken_at, line = spoken_results.get_nowait()
        except Empty:
            break
        save_spoken(conn, event_id, spoken_at, line)


latest_sim_time=0
worker=threading.Thread(target= worker_function,daemon=True)
worker.start()
dropped_events=0
conn = None
session_id = None

try:
    conn = connect_db()
    session_started = datetime.now().isoformat(timespec="seconds")
    session_id = start_session(conn,session_started,TAPE_PATH,REPLAY_SPEED)
    hash_events=[]
    if radio_check() is None:
       print("[radio check failed — driving without coach]")
    else:
        print("[coach is online]")
    
    last_spoken_time = 0.0
    # GUESSED — mLapInvalidated never observed True (n=33485)
    validity = 1                      # 1 = valid, 0 = invalidated
    for frame in source:
        lap_count = lap_counter.update(frame)

        stat = corner_stats.update(frame, lap_count)
        if stat:
            print(stat)
            save_corner_stat(conn, session_id, stat)

        if lap_counter.wrapped:
            save_lap(conn,session_id,lap_count-1,validity)
            validity = 1

        if frame.lap_invalidated:
            validity = 0

        latest_sim_time=frame.elapsed_time
        event_list = []
        for detector in detectors:
            event = detector.update(frame)
            if event:
                event.lap_count = lap_count
                print(event)
                event_id = save_event(conn,session_id,event)                             
                if event.kind in detector_priority: 
                        
                    event_list.append((event, event_id))            

        spoken_event = None
        spoken_event_id = None
        max_priority = 10                               
        for event, eid in event_list:
            if detector_priority[event.kind] < max_priority:
                spoken_event = event
                spoken_event_id = eid
                max_priority = detector_priority[event.kind]
        
        if spoken_event:                                
            is_incident = spoken_event.kind in INCIDENTS
            cooldown_open = frame.elapsed_time - last_spoken_time > SPEAK_COOLDOWN
            if is_incident or cooldown_open:
                
                hash_events.append(spoken_event) 
                 
                try:
                    
                    speak_queue.put_nowait((spoken_event, spoken_event_id))
                    last_spoken_time = frame.elapsed_time
                except Full:
                    dropped_events+=1
                    print(f"[queue full line dropped: {spoken_event.kind}@{spoken_event.corner}]")
                    pass
        
        drain_spoken(conn)

        if not REPLAY:
            tele_recorder.record(frame)

except KeyboardInterrupt:
    pass

finally:
    SHUTDOWN_GRACE=3*5
    speak_queue.put(None)
    worker.join(timeout=SHUTDOWN_GRACE)
    if conn:
        drain_spoken(conn)
        
    print(dropped_events)
    
    
    if not REPLAY:
        tele_recorder.stop()
    serialized = json.dumps([asdict(e) for e in hash_events])
    event_hash = hashlib.sha256(serialized.encode()).hexdigest()
    if session_id:
        finish_session(conn,session_id,event_hash)
        print_corner_report(conn,session_id)
        conn.close()
    else:
        conn.close()
    print(event_hash)

