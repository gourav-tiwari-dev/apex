import time
import math
import threading
import json,gzip


from sharedmemory import MMapControl
from lmu_data import LMUObjectOut, LMUConstants
from openai import OpenAI
from tts import speak
from dataclasses import dataclass,asdict
from queue import Queue

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

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama"
)

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


def ask_coach(throttle, brake, speed_kmh):
    print("Coach thread started")

    response = client.chat.completions.create(
        model="llama3.2:3b",
        messages=[
            {
                "role": "system",
                "content": "Give some racing advice under 10 words."
            },
            {
                "role": "user",
                "content": f"Throttle:{throttle}, Brake:{brake}, Speed:{speed_kmh}, corner: Ascari Monza"
            }
        ],
        temperature=0.7
    )

    advice = response.choices[0].message.content

    print("LLM responded:", advice)

    
    print("Before speech")

    speak(advice)
    
    print("After speech")

    print("Coach thread finished")


info = MMapControl(LMUConstants.LMU_SHARED_MEMORY_FILE, LMUObjectOut)
info.create(0)
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


REPLAY = False

class ReplaySource:
    def __init__(self):
        print("Connected.")
        print("Press Ctrl+C to stop.\n")

    def __iter__(self):
        try:
            with gzip.open("tape.jsonl.gz","rt") as f:
                for line in f:
                    as_dict = json.loads(line)
                    as_data = CarState(**as_dict)
                    yield as_data

        except KeyboardInterrupt:
            print("\nStopping...")
            print("Closed connection.")




@dataclass
class Event:
    
    kind: str
    sim_time:float
    speed_kmh:float
    detail:str|None=None

class HardBrakingDetector:
        def __init__(self,threshold=0.8):
            self.threshold = threshold
            self.previous_brake=0.0
            self.last_fire_time=0.0
            

        def update(self,frame):
            
            if(self.previous_brake<self.threshold and frame.brake>=self.threshold and frame.speed_kmh>30 and frame.elapsed_time-self.last_fire_time>3):
                event= Event(kind="HARD_BRAKING",sim_time=frame.elapsed_time,speed_kmh=frame.speed_kmh)
                self.last_fire_time= frame.elapsed_time
            else:
                event= None
            self.previous_brake= frame.brake
            return event


class LockUpDetector:
    def __init__(self, threshold=-0.3):
        self.threshold = threshold
        self.previously_locked = False     
        self.last_fire_time = 0.0

    def update(self, frame, slips):
        is_locked = (
            (slips[0] < self.threshold or slips[1] < self.threshold)   
            and frame.brake > 0.2                                      
            and frame.speed_kmh > 30                                   
        )
       
        event = None
        
        if is_locked and not self.previously_locked and (frame.elapsed_time - self.last_fire_time > 3):
            event = Event(kind="LOCKUP", sim_time=frame.elapsed_time, speed_kmh=frame.speed_kmh)
            self.last_fire_time = frame.elapsed_time
        self.previously_locked = is_locked
        return event


class CornerEntryDetection:
    def __init__(self):
        self.previous_corner=None

    def update(self,frame):
        current_corner= None
        for x in MONZA_CORNERS:
            if x["start"]<=frame.lap_dist<x["end"]:
                current_corner = x["name"]

        event = None
        if current_corner is not None and current_corner!=self.previous_corner:
            event=  Event(kind="CORNER_ENTRY",sim_time=frame.elapsed_time, speed_kmh=frame.speed_kmh,detail=current_corner)
        self.previous_corner = current_corner

        return event

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

braking_detector = HardBrakingDetector()
lockup_detector = LockUpDetector()
corner_detection = CornerEntryDetection()

MONZA_CORNERS = [
    {"name": "T1 Rettifilo",   "start":  760, "end":  970},
    {"name": "T4 Roggia",      "start": 1995, "end": 2140},
    {"name": "T6 Lesmo 1",     "start": 2450, "end": 2520},
    {"name": "T7 Lesmo 2",     "start": 2785, "end": 2850},
    {"name": "T8 Ascari",      "start": 3805, "end": 3940},
    {"name": "T11 Parabolica", "start": 5000, "end": 5130},
]

radii = [FRONT_RADIUS, FRONT_RADIUS, REAR_RADIUS, REAR_RADIUS]

try:
    for frame in source:
        slips = [slip_ratio(frame.wheel_rot[i], radii[i],frame.speed_kmh / 3.6) for i in range(4)]
        hard_braking = braking_detector.update(frame)
        lockup = lockup_detector.update(frame,slips)
        corner= corner_detection.update(frame)
        if not REPLAY:
            tele_recorder.record(frame)
        if corner:
            print(corner)
        if hard_braking:
            print(hard_braking)
        if lockup:
            print(lockup)
        print(frame)

except KeyboardInterrupt:
    pass

finally:
    if not REPLAY:
        tele_recorder.stop()

