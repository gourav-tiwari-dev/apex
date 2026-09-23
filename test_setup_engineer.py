from dataclasses import replace

import memory
from live_telemetry import WheelspinDetector
from seats.setup_engineer import advice_for
from test_seats import frame
from test_team_memory import add_session, add_event


def db_with(tmp_path, events, laps=6):
    conn = memory.connect_db(str(tmp_path / "s.db"))
    session = add_session(conn, "tape_a.jsonl.gz")
    for lap in range(1, laps + 1):
        memory.save_lap(conn, session, lap, 1)
    ids = [add_event(conn, session, kind, corner) for kind, corner in events]
    return conn, session, ids


def test_repeated_rear_snaps_move_the_bias_forward_with_their_evidence(tmp_path):
    conn, session, ids = db_with(tmp_path, [("REAR_SNAP", "T7 Lesmo 2")] * 3 + [("REAR_SNAP", "T1 Rettifilo")])
    advice = advice_for(conn, session)
    assert [a["kind"] for a in advice] == ["BIAS_FORWARD"]
    assert "T7 Lesmo 2 (3)" in advice[0]["conclusion"]
    assert advice[0]["evidence"] == ids


def test_two_of_anything_is_not_a_setup_problem(tmp_path):
    conn, session, _ = db_with(tmp_path, [("LOCKUP", "T1 Rettifilo")] * 2)
    assert [a["kind"] for a in advice_for(conn, session)] == ["NO_CHANGE"]


def test_all_clear_needs_enough_laps(tmp_path):
    conn, session, _ = db_with(tmp_path, [], laps=2)
    assert advice_for(conn, session) == []


def test_wheelspin_advice_and_detector():
    detector = WheelspinDetector()
    exit_frame = replace(frame(0.0), speed_kmh=100.0, throttle=1.0, steering=0.1,
                         wheel_rot=[-81.7, -81.7, -94.0, -94.0])   # rears ~22% faster than the car
    assert detector.is_triggered(exit_frame) is True
    assert detector.is_triggered(replace(exit_frame, steering=None)) is False   # old tapes


def test_wheelspin_becomes_tc_advice(tmp_path):
    conn, session, _ = db_with(tmp_path, [("WHEELSPIN", "T4 Roggia")] * 3)
    assert [a["kind"] for a in advice_for(conn, session)] == ["TC_UP"]
