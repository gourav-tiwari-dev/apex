from dataclasses import dataclass
from dotenv import load_dotenv
load_dotenv()   
import os
from openai import OpenAI

@dataclass
class Event:
    kind: str
    sim_time:float
    speed_kmh:float
    detail:str|None=None
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
)      
client = OpenAI(base_url="https://aicredits.in/v1", api_key=os.environ["AICREDITS_API_KEY"])


def phrase_event(event):
    context = event.conclusion or f"{event.kind} · {event.detail} · {event.speed_kmh:.0f}km/h"   
    resp = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
    )
    return resp.choices[0].message.content                                

if __name__ == "__main__":
    print(phrase_event(Event(kind="LOCKUP", sim_time=123.4, speed_kmh=180.0,  detail="T8 Ascari",conclusion="Front lockup under heavy braking into ascari at 180 km/h")))