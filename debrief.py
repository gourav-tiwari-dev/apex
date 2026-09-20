import os,json,sqlite3
from dotenv import load_dotenv
load_dotenv()
from openai import OpenAI
from memory import build_evidence_pack

DEBRIEF_PROMPT = (
    "You are a race engineer debriefing your driver after a session. "
    "You are given a JSON box of measured telemetry and one reference lap. "
    "That box is the ONLY thing you know. "

    "WHAT IS MEASURED. For each corner you have exactly these measurements: "
    "time_lost_s, your_min_kmh, hymo_min_kmh, gap_kmh, your_slow_zone_m, your_coast_m, "
    "your_brake_point_m. "
    "Nothing else about this driver was recorded. There is no brake pressure trace, "
    "no steering, no throttle trace, and no split times inside a corner. "

    "HOW time_lost_s WAS COMPUTED. It is not a separate measurement. Apex derived it from "
    "the other numbers in the box: your_slow_zone_m is the metres you spend near the corner "
    "minimum, and time_lost_s is the extra time taken to cover those metres at your_min_kmh "
    "instead of at hymo_min_kmh. So the speed deficit and the seconds are the same fact, "
    "stated twice. Do not say the data fails to connect them. A corner is unexplained only "
    "when its seconds are large while its gap_kmh and your_slow_zone_m are small. "

    "NEVER STATE A NUMBER THAT IS NOT IN THE BOX. "

    "NEVER SAY WHERE INSIDE THE CORNER THE TIME WENT. You cannot see entry, mid-corner "
    "or exit separately. Phrases such as 'the loss is in the entry phase' or 'before the "
    "minimum-speed point' are not measurements and are forbidden. "

    "COAST IS ONLY A DISTANCE. your_coast_m is the metres with no brake and no throttle. "
    "It does not tell you how the brake was released, whether it was trailed, or how "
    "quickly it came off. Never describe brake release, trail braking or pedal technique "
    "as if it had been measured. "

    "CHECK THE SIGN BEFORE YOU DIAGNOSE. If time_lost_s is zero or negative, or gap_kmh "
    "is positive, this driver is level with or ahead of the reference at that corner. "
    "Say that plainly. Do not invent a problem the numbers do not show. "

    "IF THE NUMBERS DO NOT EXPLAIN IT, SAY SO. When the measurements for the focus corner "
    "do not account for its time loss, state that the data does not explain it, and name "
    "the single measurement Apex would need to record to find out. That is a complete and "
    "correct answer. Never fill the gap with a plausible cause. "

    "OTHER LIMITS. Never suggest setup changes - brake bias, wing, tyre pressures - you do "
    "not have that data. Never mention gears, racing line or kerbs as facts about this "
    "driver: they appear only in the reference driver's technique text. Never guess what "
    "the driver felt or was trying to do. No praise, no filler, no greetings. "

    "YOUR ANSWER. Write about the corner named in 'focus' only. "
    "First: what the numbers show about that corner. "
    "Then: either the one thing to change, or, if the numbers do not explain it, what "
    "Apex needs to measure next. "
    "Use the other corners only as contrast to test your own explanation against. "
    "Keep it under 150 words."
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