from dotenv import load_dotenv
load_dotenv()   
import os
from openai import OpenAI


class Event:
    def __init__(self,kind,speed,time,corner):
     self.kind=kind
     self.speed=speed
     self.time=time
     self.corner=corner


SYSTEM_PROMPT = "You are a terse race engineer."      
client = OpenAI(base_url="https://aicredits.in/v1", api_key=os.environ["AICREDITS_API_KEY"])


def phrase_event(event):
    context = f"{event.kind} · {event.corner} · {event.speed:.0f}km/h"   
    resp = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
    )
    return resp.choices[0].message.content                                

if __name__ == "__main__":
    print(phrase_event(Event(kind="LOCKUP", speed=180.0, time=123.4, corner="T8 Ascari")))