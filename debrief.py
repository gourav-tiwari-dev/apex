import os,json,sqlite3
from dotenv import load_dotenv
load_dotenv()
from openai import OpenAI
from memory import build_evidence_pack

DEBRIEF_PROMPT = (
    "You are a race engineer debriefing your driver after a session. "
    "You are given a JSON box of measured telemetry and one reference lap. "
    "That box is the ONLY thing you know. "
    "Never state a number that is not in the box. "
    "Never suggest setup changes - brake bias, wing, tyre pressures - you do not have that data. "
    "Never mention gears, racing line or kerbs: those appear only in the reference driver's "
    "technique text and are not measurements of this driver. "
    "Never guess what the driver felt or was trying to do. "
    "No praise, no filler, no greetings. "
    "Answer only about the corner named in 'focus'. "
    "Say WHY that corner is losing time, then WHAT one thing to change. "
    "Use the other corners only as contrast to test your own explanation."
)

client = OpenAI(
    base_url = "https://aicredits.in/v1",
    api_key = os.environ["AICREDITS_API_KEY"],
    timeout = 120,
    max_retries = 0
)

def debrief(pack):
    resp = client.chat.completions.create(
        model= "deepseek-v4.1-flash",
        messages = [{
            "role":"system", "content": DEBRIEF_PROMPT},
            {"role": "user", "content": json.dumps(pack)
        }],
    )
    choice = resp.choices[0]
    if choice.finish_reason != "stop":
        print(f"[debrief cut off: {choice.finish_reason}]")
        return None
    return choice.message.content

if __name__ == "__main__":
    conn = sqlite3.connect("apex.db")
    pack = build_evidence_pack(conn, "reference_hymo.json", 11)
    print(debrief(pack))