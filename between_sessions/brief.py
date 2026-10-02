"""The brief, said before he drives: tonight's job (the focus contract), what the team remembers
about him, and the setup engineer's one call. The lines are logged under the night's first
session."""

from between_sessions.debrief import for_speaking
from between_sessions.setup_engineer import advice_for
from memory.contracts import load_latest_contract
from memory.db import latest_session_id, save_radio
from memory.team_memory import facts as memory_facts
from radio.calls import Call, MEMORY
from radio.tts import speak


def brief(conn):
    """Before driving: the job, what the team remembers, and the setup call.
    Returns the lines said, so they can be logged under the first session of the night."""
    said = []
    session = latest_session_id(conn) or 0
    contract = load_latest_contract(conn, session + 1)
    if contract is not None:
        # no speeds on the radio: he drives by feel (24 Sep). Jobs written before that day
        # carry a speed in their text, so those get the plain version.
        line = f"Today's job: {contract['corner']}. Carry more speed through it."
        if "km/h" not in contract["focus"]:
            line = f"Today's job: {contract['corner']}. {contract['focus']}"
    else:
        line = "No job yet. Just Drive I m Watching"
    said.append(("performance", "BRIEF_CONTRACT", line))

    habits = memory_facts(conn, "lap_one") + memory_facts(conn, "pass_attempts")
    if habits:
        said.append(
            ("memory", "BRIEF_HABIT", f"From your last races: {habits[0]['summary']}.")
        )

    last_race = conn.execute(
        "SELECT MAX(id) FROM sessions WHERE session_type BETWEEN 10 AND 13"
    ).fetchone()[0]
    if last_race is not None:
        for advice in advice_for(conn, last_race)[:1]:
            said.append(("setup", "BRIEF_" + advice["kind"], advice["conclusion"]))

    for seat, kind, text in said:
        print(text)
        speak(for_speaking(text))
    return said


def log_brief(conn, session_id, said):
    for seat, kind, text in said:
        call = Call(
            seat=seat,
            kind=kind,
            sim_time=0.0,
            priority=MEMORY,
            ttl=0.0,
            conclusion=text,
        )
        save_radio(conn, session_id, call, "spoken", {"line": text})
