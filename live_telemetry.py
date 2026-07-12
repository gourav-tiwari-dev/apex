import time
import math
import threading



from sharedmemory import MMapControl
from lmu_data import LMUObjectOut, LMUConstants
from openai import OpenAI
from tts import speak
from dataclasses import dataclass 

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
            yaw_rate=my_car.mLocalRot.y
            
        )
    


    def __iter__(self):

        try:
            while True:
                self.info.update()
                state = self.read_state()
                yield state
                time.sleep(0.5)

        except KeyboardInterrupt:
            print("\nStopping...")
            self.info.close()
            print("Closed connection.")

source = LiveSource(info)


for frame in source:
    radii = [FRONT_RADIUS, FRONT_RADIUS, REAR_RADIUS, REAR_RADIUS] 
    slips = [slip_ratio(frame.wheel_rot[i], radii[i],frame.speed_kmh / 3.6) for i in range(4)]
    print(frame)