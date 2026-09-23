from memory import latest_session_id, load_latest_contract
from tts import speak
from debrief import for_speaking
def brief(conn):
    session = latest_session_id(conn)
    contract = load_latest_contract(conn, session+1)
    if contract is not None:
        line = f"Today's job: {contract['corner']}. Minimum speed from {contract['baseline']} km/h up to {contract['target']} km/h. {contract['focus']}"
    else:
        line = "No job yet. Just Drive I m Watching"
    print(line)
    speak(for_speaking(line))