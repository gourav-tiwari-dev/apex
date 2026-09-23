import debrief
from memory import latest_session_id, load_latest_contract,connect_db
from tts import speak
from debrief import for_speaking,run_debrief
from live_telemetry import run_session

conn = connect_db("apex.db")
def brief(conn):
    session = latest_session_id(conn)
    contract = load_latest_contract(conn, session+1)
    if contract is not None:
        line = f"Today's job: {contract['corner']}. Minimum speed from {contract['baseline']} km/h up to {contract['target']} km/h. {contract['focus']}"
    else:
        line = "No job yet. Just Drive I m Watching"
    print(line)
    speak(for_speaking(line))

replay = True
replay_speed = None
if __name__ == "__main__":
    brief(conn)
    run_session(replay,replay_speed)
    run_debrief()
