"""Apex: one command for a whole race night.

    python apex.py                     live: brief, then every session you drive, then the debrief
    python apex.py --clean             the same, with no swearing (for recordings)
    python apex.py --replay            replay the 3-lap test tape
    python apex.py --replay TAPE --speed 1

One launch covers practice, qualifying and the race: each LMU session becomes its own
Apex session, and the debrief runs after a race. Ctrl+C when you are done for the night.
"""
import argparse

from memory import latest_session_id, load_latest_contract, connect_db
from tts import speak
from debrief import for_speaking, run_debrief
from live_telemetry import run_session, TAPE_PATH
from team_memory import build_profile

RACE_SESSIONS = range(10, 14)      # mSession 10-13 are race sessions


def brief(conn):
    session = latest_session_id(conn)
    contract = load_latest_contract(conn, session+1)
    if contract is not None:
        line = f"Today's job: {contract['corner']}. Minimum speed from {contract['baseline']} km/h up to {contract['target']} km/h. {contract['focus']}"
    else:
        line = "No job yet. Just Drive I m Watching"
    print(line)
    speak(for_speaking(line))


def how_it_ended(conn, session_id):
    row = conn.execute("SELECT end_reason, session_type FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row[0], row[1]


def race_night(clean):
    conn = connect_db("apex.db")
    brief(conn)
    while True:
        session_id = run_session(False, None, clean=clean)
        # every drive teaches the team memory, before anything reads from it
        build_profile(conn)
        end_reason, session_type = how_it_ended(conn, session_id)
        if session_type in RACE_SESSIONS and end_reason == "session_over":
            run_debrief()
        if end_reason == "stopped_by_driver":
            break
        print("[waiting for the next session - Ctrl+C when you are done]")
    conn.close()


def replay_night(tape, speed, clean):
    conn = connect_db("apex.db")
    brief(conn)
    conn.close()
    run_session(True, speed, tape, clean=clean)
    conn = connect_db("apex.db")
    build_profile(conn)
    conn.close()
    run_debrief()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apex race engineer")
    parser.add_argument("--replay", nargs="?", const=TAPE_PATH, default=None,
                        help="replay a tape instead of reading the game")
    parser.add_argument("--speed", type=float, default=None,
                        help="replay speed: 1 = real time, leave out for max speed")
    parser.add_argument("--clean", action="store_true", help="no swearing")
    args = parser.parse_args()
    if args.replay:
        replay_night(args.replay, args.speed, args.clean)
    else:
        race_night(args.clean)
