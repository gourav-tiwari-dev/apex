from dataclasses import dataclass
from dotenv import load_dotenv
load_dotenv()   
import os
import time
from openai import OpenAI

@dataclass
class Event:
    kind: str
    sim_time:float
    speed_kmh:float
    detail:str|None=None
    lap_dist: float = 0.0
    conclusion: str | None = None

SYSTEM_PROMPT = (
    "You are a terse race engineer talking to your driver over team radio. "
    "Speak only from the information you are given. Never invent or assume data "
    "you were not told — no numbers, no wheel, no setup values. "
    "Describe what happened and the driver's input only. "
    "Never suggest car setup changes (brake bias, wing, tires) — you do not have that data. "
    "This is one-way radio: never ask questions. "
    "No greetings or acknowledgements like 'Copy' or 'Understood'. "
    "One line, maximum 12 words, straight to the point."
    "Never tell the driver to continue or maintain the behavior described - "
    "the event is always a mistake to correct, never an instruction to repeat. "
    "Never invent consequences such as lost time, lost momentum, or tire wear. "
)      
client = OpenAI(base_url="https://aicredits.in/v1", api_key=os.environ["AICREDITS_API_KEY"],timeout=10,max_retries=0)

cooldown=120
failure_counter = 0
circuit_open=False
circuit_open_time = 0

def phrase_event(event):
    state_changed=False
    global failure_counter,circuit_open,circuit_open_time
    if  circuit_open and time.perf_counter()-circuit_open_time>cooldown:
        circuit_open=False
        state_changed=True
    if circuit_open:
        return None
    
    context = event.conclusion or f"{event.kind} · {event.detail} · {event.speed_kmh:.0f}km/h"  
    try: 
        resp = client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
        )
    
        if resp:
            failure_counter=0
            circuit_open=False
            if state_changed:
                print("[coach circuit closed]")

        return resp.choices[0].message.content 
    except Exception as e:
        print(f"[coach offline: {e.__class__.__name__}]")
        failure_counter+=1
        if failure_counter>=3:
            circuit_open=True
            circuit_open_time=time.perf_counter()
            print("[coach circuit open]")
        return None    

def radio_check():
    initial_startup=phrase_event(Event(kind="THROTTLE_LIFT", sim_time=123.4, speed_kmh=180.0,  detail="T8 Ascari",conclusion="Throttle lift into  ascari with no braking at 180 km/h"))
    if initial_startup is None:
        return None   
    else:
        return not None

if __name__ == "__main__":
    for i in range(0,5):
        print(phrase_event(Event(kind="THROTTLE_LIFT", sim_time=123.4, speed_kmh=180.0,  detail="T8 Ascari",conclusion="radio check")))
        i+=1