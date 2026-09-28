"""Apex: one command for a whole race night.

    python apex.py                     live: brief, then every session you drive, then the debrief
    python apex.py --clean             the same, with no swearing (for recordings)
    python apex.py --replay            replay the 3-lap test tape
    python apex.py --replay TAPE --speed 1

One launch covers practice, qualifying and the race: each LMU session becomes its own
Apex session, and the debrief runs after a race. Ctrl+C when you are done for the night.
"""

import argparse

from memory.db import connect_db
from between_sessions.debrief import run_debrief
from session import run_session
from game.tape import TAPE_PATH
from memory.team_memory import build_profile
from datetime import datetime
from radio.voice import Voice
from game.constants import RACE_SESSIONS
from between_sessions.brief import brief, log_brief


def how_it_ended(conn, session_id):
    row = conn.execute(
        "SELECT end_reason, session_type FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return row[0], row[1]


def race_night(clean, record=False):
    conn = connect_db("apex.db")
    # one voice for the whole launch: Azure's voices with emotion when .env has a key,
    # edge-tts otherwise
    voice = Voice(out_loud=True)
    said = brief(conn)
    launch_id = datetime.now().isoformat(timespec="seconds")
    try:
        race_sessions(conn, clean, launch_id, voice, said)
    finally:
        conn.close()


def race_sessions(conn, clean, launch_id, voice, said):
    first = True
    while True:
        session_id = run_session(
            False, None, clean=clean, launch_id=launch_id, voice=voice
        )
        if first:
            log_brief(conn, session_id, said)
            first = False
        # every drive teaches the team memory, before anything reads from it
        build_profile(conn)
        end_reason, session_type = how_it_ended(conn, session_id)
        if session_type in RACE_SESSIONS and end_reason == "session_over":
            run_debrief()
        if end_reason == "stopped_by_driver":
            break
        print("[waiting for the next session - Ctrl+C when you are done]")


def replay_night(tape, speed, clean):
    conn = connect_db("apex.db")
    said = brief(conn)
    conn.close()
    session_id = run_session(True, speed, tape, clean=clean)
    conn = connect_db("apex.db")
    log_brief(conn, session_id, said)
    build_profile(conn)
    conn.close()
    run_debrief()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apex race engineer")
    parser.add_argument(
        "--replay",
        nargs="?",
        const=TAPE_PATH,
        default=None,
        help="replay a tape instead of reading the game",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=None,
        help="replay speed: 1 = real time, leave out for max speed",
    )
    parser.add_argument("--clean", action="store_true", help="no swearing")
    parser.add_argument(
        "--record",
        action="store_true",
        help="recording a clip: the standard voice only, never the cloned one",
    )
    args = parser.parse_args()
    if args.replay:
        replay_night(args.replay, args.speed, args.clean)
    else:
        race_night(args.clean, args.record)
