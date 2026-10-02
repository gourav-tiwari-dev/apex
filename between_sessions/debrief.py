import json
import os
import sys
import time
from memory.db import connect_db
from memory.contracts import build_evidence_pack, make_contract, save_contract
from memory.db import latest_session_id, track_of
from memory.contracts import load_latest_contract, evaluate_contract
from memory.db import save_radio
from memory.contracts import reference_from_race, reference_from_self
from radio.calls import Call, MEMORY
from between_sessions.setup_engineer import advice_for
from radio.tts import speak
from coach.llm import DEBRIEF_MODEL, open_client

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
    "When braking_pt_difference_m is null it was not measured on this track: never guess it "
    "and never mention brake points. "
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
    "LAST CONTRACT. 'last_contract' is the one job this driver was given last session, and "
    "how it went this session. It is null when there was no job. Code already graded it: "
    "'verdict' is hit (reached target), moved (improved by at least 1.5 km/h but missed "
    "target), flat (no real change) or insufficient (too few clean laps to judge, so "
    "'result' and 'gain' are null). Never re-grade it and never argue with the verdict. "
    "When the verdict is insufficient, do not compare any speed against its baseline. "
    "Do not say why a verdict happened: the numbers show that the speed changed, not "
    "what the driver did to change it. "
    "OTHER LIMITS. Never suggest setup changes - brake bias, wing, tyre pressures - you do "
    "not have that data. Never mention gears, racing line or kerbs as facts about this "
    "driver: they appear only in the reference driver's technique text. Never guess what "
    "the driver felt or was trying to do. No praise, no filler, no greetings. "
    "YOUR ANSWER. Reply with JSON only, no code fences, with exactly two keys: "
    "'analysis' and 'spoken'. "
    "'analysis' is for the engineering log, not for the driver. If 'last_contract' is not "
    "null, the FIRST sentence of 'analysis' is about it and nothing else: its corner, its "
    "verdict, and its baseline, result and target exactly as given. The SECOND sentence says "
    "whether the focus corner is the same job continuing or the focus has moved on to a new "
    "corner. Then write about the corner named in 'focus' only. First what the numbers show, then either the one thing to change or, if "
    "the numbers do not explain it, what Apex needs to measure next. Under 150 words. "
    "'spoken' is read aloud to the driver as he takes his helmet off. Exactly two sentences. "
    "Write numbers as plain digits exactly as they appear in the box, with their unit, for "
    "example '0.35 s'. Do not spell numbers out as words and do not round them. "
    "NEVER SAY A SPEED in 'spoken': no km/h, no speed values at all. He drives by feel and "
    "never looks at the speedometer, so a speed means nothing to him. The one number is a "
    "time from time_lost_s. "
    "No markdown, no brackets, no dashes, no other symbols. Name the corner by its tag, for "
    "example 'Turn 1'. Include exactly one number, so he has a sense of the scale, and no "
    "more. Say what to do in terms of the track and the car, never in terms of a measurement: "
    "'carry more speed to the apex', never 'raise your minimum speed through the slow zone'. "
    "No praise, no greeting, no jargon."
)

_client = None


def connect():
    """Opened on the first debrief, not on import (30 Sep): apex.py imports this module at start,
    so a machine with no AI access crashed before the race instead of skipping the debrief."""
    global _client
    if _client is None:
        _client = open_client(timeout=120)
    return _client


def ask_once(pack):
    text = ""
    finish = None
    stream = connect().chat.completions.create(
        model=DEBRIEF_MODEL,
        max_tokens=16000,
        stream=True,
        messages=[
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
            out.append("meters" + tail)
        elif i == "s":
            out.append("seconds" + tail)
        elif i == "km/h":
            out.append("kilometers an hour" + tail)
        else:
            out.append(i + tail)
    return " ".join(out)


def verdict_line(contract, grade):
    corner = contract["corner"]
    # no speeds: he drives by feel and never looks at the speedo (24 Sep)
    if grade["verdict"] == "insufficient":
        return f"The job at {corner} has {grade['laps']} clean laps of {contract['min_laps']} so far. It carries over to the next race."
    if grade["verdict"] == "hit":
        return f"The job at {corner} is done. You carry the speed through there now."
    if grade["verdict"] == "moved":
        return f"{corner} is better, not there yet. Keep working it."
    return f"No real change at {corner} yet. Same job."


def say_and_log(conn, session_id, seat, kind, line):
    # every debrief line is logged, so the DONE check can see which seat spoke
    print(line)
    speak(for_speaking(line))
    call = Call(
        seat=seat, kind=kind, sim_time=0.0, priority=MEMORY, ttl=0.0, conclusion=line
    )
    save_radio(conn, session_id, call, "spoken", {"line": line})


MONZA_REFERENCE = "reference_hymo.json"


def reference_for(conn, session_id):
    track = conn.execute(
        "SELECT track FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()[0]
    # Monza keeps the hand-checked reference lap; old sessions have no track and were Monza.
    # The file is Gourav's notes from a HYMO video, so the installed app doesn't carry it
    # (30 Sep): without it Monza is measured like every other track.
    if (track is None or "monza" in track.lower()) and os.path.exists(MONZA_REFERENCE):
        return MONZA_REFERENCE
    if track is None:
        return None
    car_class, car_model = conn.execute(
        "SELECT car_class, car_model FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    reference = reference_from_race(conn, session_id, car_class, car_model)
    if reference is None:
        # no other cars recorded (e.g. LMU's own telemetry): measure against your own best
        reference = reference_from_self(conn, track)
    return reference


def incident_review(conn, session_id):
    """E13: every contact of the race, where and with whom, and the pass attempts."""
    contacts, attempts, offs, strikes, impacts = incident_numbers(conn, session_id)
    parts = []
    if contacts:
        where = ", ".join(f"lap {lap} {corner}" for corner, lap, _ in contacts[:3])
        parts.append(
            f"{len(contacts)} contact{'s' if len(contacts) > 1 else ''}: {where}."
        )
    else:
        parts.append("No contact with a known car.")
    if attempts:
        passes = sum(1 for (o,) in attempts if o == "pass")
        touched = sum(1 for (o,) in attempts if o == "contact")
        parts.append(
            f"{len(attempts)} passing attempts, {passes} made it, {touched} ended in contact."
        )
    if impacts:
        parts.append(
            f"{impacts} other impact{'s' if impacts > 1 else ''}: a wall, or a car Apex could not see."
        )
    parts.append(f"{offs} offs, {strikes} track limit steps.")
    return " ".join(parts)


def incident_numbers(conn, session_id):
    """(contacts, pass attempts, offs and spins, track-limit strikes, other impacts) of the
    session, from its events."""
    contacts = conn.execute(
        "SELECT corner, lap_count, conclusion FROM events WHERE session_id = ? AND kind = 'CONTACT' ORDER BY sim_time",
        (session_id,),
    ).fetchall()
    attempts = conn.execute(
        "SELECT outcome FROM pass_attempts WHERE session_id = ?", (session_id,)
    ).fetchall()
    offs = conn.execute(
        "SELECT COUNT(*) FROM events WHERE session_id = ? AND kind IN ('OFF_TRACK','SPIN')",
        (session_id,),
    ).fetchone()[0]
    strikes = (
        conn.execute(
            "SELECT track_limit_strikes FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()[0]
        or 0
    )
    impacts = conn.execute(
        "SELECT COUNT(*) FROM events WHERE session_id = ? AND kind = 'IMPACT'",
        (session_id,),
    ).fetchone()[0]
    return contacts, attempts, offs, strikes, impacts


def run_debrief(session_id=None):
    """After a session: the last job graded, the coach's debrief and the next job, then the
    setup engineer and the incident review, each said and logged."""
    conn = connect_db("apex.db")

    # Grade the latest session unless one is named, for example: python -m between_sessions.debrief 11
    if session_id is None:
        session_id = latest_session_id(conn)
    print(f"session {session_id}")

    previous = load_latest_contract(conn, session_id, track_of(conn, session_id))
    last_contract, job_still_open = grade_last_job(conn, session_id, previous)
    open_job = previous if job_still_open else None
    coach_debrief(conn, session_id, last_contract, open_job)
    # the setup engineer (M8) and the incident review (E13), after the coach
    for advice in advice_for(conn, session_id):
        say_and_log(
            conn, session_id, "setup", "DEBRIEF_" + advice["kind"], advice["conclusion"]
        )
    say_and_log(
        conn,
        session_id,
        "memory",
        "DEBRIEF_INCIDENTS",
        incident_review(conn, session_id),
    )
    conn.close()


def grade_last_job(conn, session_id, previous):
    """The last race's job graded and said: (what the coach is told about it, still open?).
    A job keeps collecting laps across races until it can be graded: replacing it every
    race meant it could never be judged."""
    if previous is None:
        return None, False
    grade = evaluate_contract(conn, previous, session_id)
    line = verdict_line(previous, grade)
    print(f"verdict: {grade['verdict']} - {line}")
    say_and_log(conn, session_id, "performance", "VERDICT", line)
    job_still_open = grade["verdict"] == "insufficient"
    last_contract = {
        "corner": previous["corner"],
        "baseline": previous["baseline"],
        "target": previous["target"],
        "result": grade.get("result"),
        "gain": grade.get("gain"),
        "verdict": grade["verdict"],
        "laps": grade["laps"],
    }
    return last_contract, job_still_open


def coach_debrief(conn, session_id, last_contract, open_job):
    """The coach's debrief against a reference lap, said and logged, and the next job from
    it. open_job: the last job, when it is still collecting laps (then no new one)."""
    reference = reference_for(conn, session_id)
    if reference is None:
        print("[no reference on this track yet: nobody in your class did 2 clean laps]")
        print("[no debrief]")
        return
    pack = build_evidence_pack(conn, reference, session_id)
    pack["last_contract"] = last_contract
    answer = debrief(pack)
    if answer is None:
        print("[no debrief]")
        return
    print(answer["analysis"])
    say_and_log(conn, session_id, "performance", "DEBRIEF", answer["spoken"])
    next_job(conn, session_id, pack, answer["spoken"], open_job)


def next_job(conn, session_id, pack, spoken, open_job):
    """The next job from the debrief, saved; none while the last one is still open."""
    if open_job is not None:
        print(f"[job at {open_job['corner']} still open: it keeps collecting laps]")
        return
    contract = make_contract(pack, spoken)
    if contract is None:
        print("[no contract: the gap is too small to coach]")
        return
    save_contract(conn, session_id, contract)
    print(
        f"contract: {contract['corner']} {contract['metric']} {contract['baseline']} -> {contract['target']} over {contract['min_laps']} laps"
    )


if __name__ == "__main__":
    # the command line is read here only: apex.py has its own flags
    named_session = None
    if len(sys.argv) > 1:
        named_session = int(sys.argv[1])
    run_debrief(named_session)
