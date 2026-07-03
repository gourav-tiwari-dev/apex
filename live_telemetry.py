import time
import math
import threading


from sharedmemory import MMapControl
from lmu_data import LMUObjectOut, LMUConstants
from openai import OpenAI
from tts import speak

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama"
)


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

print("Connected.")
print("Press Ctrl+C to stop.\n")

loop_count = 0
coach_thread = None

try:
    while True:
        info.update()

        player_index = info.data.telemetry.playerVehicleIdx
        my_car = info.data.telemetry.telemInfo[player_index]

        throttle = my_car.mUnfilteredThrottle
        brake = my_car.mUnfilteredBrake

        vx = my_car.mLocalVel.x
        vy = my_car.mLocalVel.y
        vz = my_car.mLocalVel.z

        speed_ms = math.sqrt(vx**2 + vy**2 + vz**2)
        speed_kmh = speed_ms * 3.6

        print(
            f"Throttle: {throttle:.2f} | "
            f"Brake: {brake:.2f} | "
            f"Speed: {speed_kmh:.1f} km/h"
        )
        

        if loop_count % 20 == 0:
            print("Checking...", coach_thread.is_alive() if coach_thread else None)

            if coach_thread is None or not coach_thread.is_alive():
                print("Starting coach thread")

                coach_thread = threading.Thread(
                    target=ask_coach,
                    args=(throttle, brake, speed_kmh),
                    daemon=True
                )
                coach_thread.start()

        loop_count += 1
        time.sleep(0.5)

except KeyboardInterrupt:
    print("\nStopping...")
    info.close()
    print("Closed connection.")