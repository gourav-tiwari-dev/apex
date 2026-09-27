import memory
from done_check import check, SEATS, save_result
from radio import Call


def a_race(
    tmp_path,
    first_phase=3,
    end_reason="session_over",
    seats=SEATS,
    with_quali=True,
    cost=1.2,
):
    conn = memory.connect_db(str(tmp_path / "d.db"))
    if with_quali:
        conn.execute(
            "INSERT INTO sessions (started_at, tape_path, session_type, launch_id) VALUES ('x', 'q.gz', 6, 'L1')"
        )
    cur = conn.execute(
        "INSERT INTO sessions (started_at, tape_path, session_type, end_reason, first_phase, launch_id) "
        "VALUES ('x', 'r.gz', 10, ?, ?, 'L1')",
        (end_reason, first_phase),
    )
    race = cur.lastrowid
    for seat in seats:
        memory.save_radio(
            conn,
            race,
            Call(seat, "SOMETHING", 1.0, 5, 5.0, "line"),
            "spoken",
            f"{seat} said it",
            latency_ms=1500,
        )
    memory.save_radio(
        conn,
        race,
        Call("race_engineer", "RADIO_CHECK", 0.0, 1, 1.0, "check"),
        "spoken",
        "Radio check",
    )
    conn.execute(
        "INSERT INTO llm_calls (session_id, seat, tokens_in, tokens_out, seconds, cost_rs) VALUES (?, 'x', 1, 1, 1.0, ?)",
        (race, cost),
    )
    conn.commit()
    return conn, race


HAPPY = {"switched_off": False, "ratings": {seat: "useful" for seat in SEATS}}


def test_a_full_race_with_every_seat_is_done(tmp_path):
    conn, race = a_race(tmp_path)
    result = check(conn, race, HAPPY)
    assert result["verdict"] == "DONE", result["problems"]
    save_result(conn, result)
    assert conn.execute("SELECT verdict FROM done_checks").fetchone()[0] == "DONE"


def test_a_silent_seat_is_not_done(tmp_path):
    conn, race = a_race(tmp_path, seats=[s for s in SEATS if s != "setup"])
    result = check(conn, race, HAPPY)
    assert result["verdict"] == "NOT YET"
    assert "the setup seat made no real call" in result["problems"]


def test_the_radio_check_is_not_a_real_call(tmp_path):
    conn, race = a_race(tmp_path, seats=[s for s in SEATS if s != "race_engineer"])
    assert (
        "the race_engineer seat made no real call"
        in check(conn, race, HAPPY)["problems"]
    )


def test_switching_it_off_or_joining_late_or_stopping_early_fails(tmp_path):
    conn, race = a_race(tmp_path, first_phase=5, end_reason="stopped_by_driver")
    problems = check(conn, race, {**HAPPY, "switched_off": True})["problems"]
    assert "Apex was not on before the green flag" in problems
    assert any("did not run to the flag" in p for p in problems)
    assert "you switched it off" in problems


def test_a_wrong_seat_or_no_qualifying_or_over_budget_fails(tmp_path):
    conn, race = a_race(tmp_path, with_quali=False, cost=6.0)
    ratings = {**HAPPY["ratings"], "spotter": "wrong"}
    problems = check(conn, race, {"switched_off": False, "ratings": ratings})[
        "problems"
    ]
    assert "you rated the spotter seat wrong (D1)" in problems
    assert "no qualifying session in the same launch (D3)" in problems
    assert any("over the Rs 5 cap" in p for p in problems)
