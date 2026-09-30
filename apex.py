"""Apex: one command for a whole race night.

    python apex.py                     live: brief, then every session you drive, then the debrief
    python apex.py --spicy             the same, and the engineer swears (never slurs, in any mode)
    python apex.py --clips             also records the game window and makes a short of every race
    python apex.py --replay            replay the 3-lap test tape
    python apex.py --replay TAPE --speed 1

One launch covers practice, qualifying and the race: each LMU session becomes its own
Apex session, and the debrief runs after a race. Ctrl+C when you are done for the night.
"""

import argparse
import os

from driver_profile import load_profile
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


def race_night(clean, record=False, clips=None):
    conn = connect_db("apex.db")
    # one voice for the whole launch: Azure's voices with emotion when .env has a key,
    # edge-tts otherwise
    voice = Voice(out_loud=True)
    said = brief(conn)
    launch_id = datetime.now().isoformat(timespec="seconds")
    try:
        race_sessions(conn, clean, launch_id, voice, said, clips)
    finally:
        conn.close()


def race_sessions(conn, clean, launch_id, voice, said, clips=None):
    first = True
    while True:
        if clips is not None:
            clips.session_starting()
        session_id = run_session(
            False, None, clean=clean, launch_id=launch_id, voice=voice
        )
        if first:
            log_brief(conn, session_id, said)
            first = False
        # every drive teaches the team memory, before anything reads from it
        build_profile(conn)
        end_reason, session_type = how_it_ended(conn, session_id)
        finished_race = session_type in RACE_SESSIONS and end_reason == "session_over"
        if clips is not None:
            clips.session_over(session_id, finished_race)
        if finished_race:
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


def main(argv=None):
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
    # Apex is a product now (30 Sep): clean is the default a new driver gets, swearing is opt-in
    parser.add_argument("--spicy", action="store_true", help="the engineer swears")
    parser.add_argument(
        "--clean", action="store_true", help="no swearing (the default; kept for old commands)"
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="recording a clip: the standard voice only, never the cloned one",
    )
    parser.add_argument(
        "--clips",
        action="store_true",
        help="record the game window and make a short of every race (Videos/Apex)",
    )
    args = parser.parse_args(argv)
    # the setup wizard's choices (profile.json); a flag on the command line switches one on
    profile = load_profile()
    clean = not (args.spicy or profile.spicy)
    if args.replay:
        replay_night(args.replay, args.speed, clean)
    else:
        clips = None
        if args.clips or profile.clips:
            from clips.auto import AutoClips

            clips = AutoClips(os.path.abspath("apex.db"))
        race_night(clean, args.record, clips)


if __name__ == "__main__":
    main()
