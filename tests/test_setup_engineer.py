from dataclasses import replace

import memory
from driving.detectors import WheelspinDetector
from between_sessions.setup_engineer import advice_for
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
    conn, session, ids = db_with(
        tmp_path, [("REAR_SNAP", "T7 Lesmo 2")] * 3 + [("REAR_SNAP", "T1 Rettifilo")]
    )
    advice = advice_for(conn, session)
    assert [a["kind"] for a in advice] == ["BIAS_FORWARD"]
    assert "T7 Lesmo 2 (3)" in advice[0]["conclusion"]
    assert advice[0]["evidence"] == ids


def add_timed(conn, session, kind, corner, sim_time):
    cur = conn.execute(
        "INSERT INTO events (session_id, kind, sim_time, speed_kmh, corner, lap_dist, lap_count) "
        "VALUES (?, ?, ?, 150.0, ?, 100.0, 1)",
        (session, kind, sim_time, corner),
    )
    return cur.lastrowid


def test_the_car_that_hit_him_is_not_a_setup_problem(tmp_path):
    # replay of 25 Sep night (27 Sep): punted at the Porsche Curves; the snaps, lock-up and rejoin
    # wheelspin of that one incident told him to move the brake bias and go up on TC
    conn, session, _ = db_with(tmp_path, [])
    add_timed(conn, session, "CONTACT", "Porsche Curves", 397.8)
    add_timed(conn, session, "REAR_SNAP", "Porsche Curves", 398.2)
    add_timed(conn, session, "LOCKUP", "Porsche Curves", 400.6)
    add_timed(conn, session, "REAR_SNAP", "Porsche Curves", 402.1)
    add_timed(conn, session, "SPIN", "Porsche Curves", 402.4)
    add_timed(conn, session, "WHEELSPIN", "Porsche Curves", 410.4)
    add_timed(conn, session, "REAR_SNAP", "Mulsanne Chicane 1", 521.0)
    add_timed(conn, session, "REAR_SNAP", "Mulsanne Chicane 1", 764.8)
    assert [a["kind"] for a in advice_for(conn, session)] == [
        "NO_CHANGE"
    ]  # 2 real snaps


def test_a_snap_that_became_his_own_spin_still_counts(tmp_path):
    conn, session, _ = db_with(tmp_path, [])
    for when in (100.0, 400.0, 700.0):
        add_timed(conn, session, "REAR_SNAP", "Arnage", when)
        add_timed(
            conn, session, "SPIN", "Arnage", when + 0.3
        )  # no car, no wall: the car did it
    assert [a["kind"] for a in advice_for(conn, session)] == ["BIAS_FORWARD"]


def test_the_advice_names_the_setting_he_is_on(tmp_path):
    # 27 Sep: "Go 1 step up on TC" after each of his 5 races while he stayed on TC 4, ABS 9 the
    # whole time: the advice never said what he was on, or what to go to
    conn, session, _ = db_with(
        tmp_path, [("WHEELSPIN", "Mulsanne Corner")] * 3 + [("LOCKUP", "Arnage")] * 3
    )
    memory.save_car_settings(conn, session, (4, 9, 0.525, 1))
    advice = {}
    for item in advice_for(conn, session):
        advice[item["kind"]] = item
    assert "Go 1 step up on TC, 4 to 5." in advice["TC_UP"]["conclusion"]
    assert "Go 1 step up on ABS, 9 to 10," in advice["ABS_UP"]["conclusion"]
    assert advice["TC_UP"]["facts"]["tc_now"] == 4


def test_two_of_anything_is_not_a_setup_problem(tmp_path):
    conn, session, _ = db_with(tmp_path, [("LOCKUP", "T1 Rettifilo")] * 2)
    assert [a["kind"] for a in advice_for(conn, session)] == ["NO_CHANGE"]


def test_all_clear_needs_enough_laps(tmp_path):
    conn, session, _ = db_with(tmp_path, [], laps=2)
    assert advice_for(conn, session) == []


def test_wheelspin_advice_and_detector():
    detector = WheelspinDetector()
    exit_frame = replace(
        frame(0.0),
        speed_kmh=100.0,
        throttle=1.0,
        steering=0.1,
        wheel_rot=[-81.7, -81.7, -94.0, -94.0],
    )  # rears ~22% faster than the car
    assert detector.is_triggered(exit_frame) is True
    assert (
        detector.is_triggered(replace(exit_frame, steering=None)) is False
    )  # old tapes


def test_wheelspin_becomes_tc_advice(tmp_path):
    conn, session, _ = db_with(tmp_path, [("WHEELSPIN", "T4 Roggia")] * 3)
    assert [a["kind"] for a in advice_for(conn, session)] == ["TC_UP"]
