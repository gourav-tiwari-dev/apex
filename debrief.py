import os,json,sqlite3,time
from dotenv import load_dotenv
load_dotenv()
from openai import OpenAI
from memory import build_evidence_pack
from tts import speak

DEBRIEF_PROMPT = (
    "You are a race engineer debriefing your driver after a session. "
    "You are given a JSON box of measured telemetry and one reference lap. "
    "That box is the ONLY thing you know. "

    "WHAT IS MEASURED. For each corner you have exactly these measurements: "
    "time_lost_s, your_min_kmh, hymo_min_kmh, gap_kmh, your_slow_zone_m, your_coast_m, "
    "braking_pt_difference_m. "
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

    "BRAKING_PT_DIFFERENCE_M IS A DIFFERENCE, NOT A POSITION. It is the reference "
    "driver's brake point minus this driver's, in metres along the lap. Positive means "
    "the reference brakes further down the road; negative means this driver brakes later. "
    "It was measured by finding the reference driver's described landmarks in the "
    "simulator and is accurate to about 15 metres. Treat any value smaller than 15 as "
    "'both brake in the same place'. It says nothing about brake pressure or release. "

    "CHECK THE SIGN BEFORE YOU DIAGNOSE. If time_lost_s is zero or negative, or gap_kmh "
    "is positive, this driver is level with or ahead of the reference at that corner. "
    "Say that plainly. Do not invent a problem the numbers do not show. "

    "DO NOT RANK OR ORDER THE CORNERS. Never say a corner has the largest, smallest, "
    "highest, lowest or second-anything value, and never call two corners similar. You are "
    "reading six rows and you will get the ordering wrong. Compare only by quoting both "
    "numbers side by side and letting them speak: 'T1 is 6.3 km/h down, T8 is 14.4 km/h down'. "
    "The corners array is already sorted by time lost, largest first, so the focus corner is "
    "the biggest loss - that one ordering you may state, and no other. "

    "IF THE NUMBERS DO NOT EXPLAIN IT, SAY SO. When the measurements for the focus corner "
    "do not account for its time loss, state that the data does not explain it, and name "
    "the single measurement Apex would need to find out. That is a complete and correct "
    "answer. Never fill the gap with a plausible cause. "

    "OTHER LIMITS. Never suggest setup changes - brake bias, wing, tyre pressures - you do "
    "not have that data. Never mention gears, racing line or kerbs as facts about this "
    "driver: they appear only in the reference driver's technique text. Never guess what "
    "the driver felt or was trying to do. No praise, no filler, no greetings. "

    "YOUR ANSWER. Reply with JSON only, no code fences, with exactly two keys: "
    "'analysis' and 'spoken'. "

    "'analysis' is for the engineering log, not for the driver. Write about the corner named "
    "in 'focus' only. First what the numbers show, then either the one thing to change or, if "
    "the numbers do not explain it, what Apex needs to measure next. Under 150 words. "

    "'spoken' is read aloud to the driver as he takes his helmet off. Exactly two sentences. "
    "Write numbers as plain digits exactly as they appear in the box, with their unit, for "
    "example '0.35 s' or '6.3 km/h'. Do not spell numbers out as words and do not round them. "
    "No markdown, no brackets, no dashes, no other symbols. Name the corner by its tag, for "
    "example 'Turn 1'. Include exactly one number, so he has a sense of the scale, and no "
    "more. Say what to do in terms of the track and the car, never in terms of a measurement: "
    "'carry more speed to the apex', never 'raise your minimum speed through the slow zone'. "
    "No praise, no greeting, no jargon."
)

client = OpenAI(
    base_url = "https://aicredits.in/v1",
    api_key = os.environ["AICREDITS_API_KEY"],
    timeout = 120,
    max_retries = 0
)

def ask_once(pack):
    text = ""
    finish = None
    stream = client.chat.completions.create(
        model = "deepseek-v4.1-flash",
        max_tokens = 16000,
        stream = True,
        messages = [
            {"role": "system", "content": DEBRIEF_PROMPT},
            {"role": "user", "content": json.dumps(pack)},
        ],
    )
    for chunk in stream:
        
        if chunk.choices:
            
            if chunk.choices[0].finish_reason:
                finish = chunk.choices[0].finish_reason
            piece = chunk.choices[0].delta.content
            if piece:
                text += piece

    return text, finish


def debrief(pack):
    for attempt in range(3):
        try:
            text, finish = ask_once(pack)
        except Exception as e:
            print(f"[debrief failed: {e.__class__.__name__}]")
            time.sleep(3)
            continue
        if finish != "stop":
            print(f"[debrief cut off: {finish}]")
            time.sleep(3)
            continue
        if text == "":
            print("[debrief came back empty]")
            time.sleep(3)
            continue
        try:
            answer = json.loads(text)
            
        except ValueError:
            print("[debrief dont give valid json]")
            time.sleep(3)
            continue
        return answer
    return None

def for_speaking(text):
    split_answer = text.split(" ")
    out = []
    for i in split_answer:
        tail = ""
        if i and i[-1] in ".,!?":        
            tail = i[-1]
            i = i[:-1]
        i = i.strip("()*-")
        if i == "m":
            out.append("meters"+ tail)
        elif i == "s":
            out.append("seconds"+ tail)
        elif i == "km/h":
            out.append("kilometers an hour"+tail)
        else:
            out.append(i+tail)
    return " ".join(out)

if __name__ == "__main__":
    conn = sqlite3.connect("apex.db")
    pack = build_evidence_pack(conn, "reference_hymo.json", 11)
    answer = debrief(pack)
    if answer is None:
        print("[no debrief]")
    else:
        print(answer["analysis"])
        speak(for_speaking(answer["spoken"]))
