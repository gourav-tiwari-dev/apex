from between_sessions import debrief
from seats.performance import CornerPass
import memory
from test_team_memory import add_session, add_event


def race_on_a_new_track(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    conn = memory.connect_db("apex.db")
    session = add_session(conn, "tape_spa.jsonl.gz", track="Spa-Francorchamps")
    for lap in range(1, 7):
        memory.save_lap(conn, session, lap, 1)
        memory.save_corner_stat(
            conn,
            session,
            type(
                "S",
                (),
                {
                    "lap_count": lap,
                    "corner": "Turn 1",
                    "brake_onset": 300.0,
                    "min_speed": 70.0,
                    "slow_zone": 40.0,
                    "coast": 5.0,
                },
            )(),
        )
    memory.save_opponent_corners(
        conn,
        session,
        [
            CornerPass(
                who="111",
                driver="Ann",
                car_class="GT3",
                car_model=None,
                corner="Turn 1",
                lap=lap,
                min_speed=76.0,
            )
            for lap in (1, 2, 3)
        ],
    )
    add_event(conn, session, "CONTACT", "Turn 1", lap=1, other_car="111")
    memory.save_pass_attempts(conn, session, [("111", "Ann", "Turn 1", 1, "contact")])
    conn.commit()
    conn.close()
    return session


def test_debrief_on_a_track_with_no_reference_lap_uses_the_fastest_car(
    tmp_path, monkeypatch
):
    session = race_on_a_new_track(tmp_path, monkeypatch)
    packs = []

    def fake_llm(pack):
        packs.append(pack)
        return {
            "analysis": "log",
            "spoken": "Turn 1 is 6.0 km/h down. Carry more speed to the apex.",
        }

    monkeypatch.setattr(debrief, "debrief", fake_llm)
    monkeypatch.setattr(debrief, "speak", lambda text: None)
    debrief.run_debrief(session)

    pack = packs[0]
    assert (
        pack["reference"]["source"] == "the fastest car in your class in this race: Ann"
    )
    corner = pack["corners"][0]
    assert corner["hymo_min_kmh"] == 76.0
    assert (
        corner["braking_pt_difference_m"] is None
    )  # not measured on this track, not guessed

    conn = memory.connect_db("apex.db")
    said = conn.execute(
        "SELECT seat, kind, line FROM radio_log WHERE session_id = ? ORDER BY id",
        (session,),
    ).fetchall()
    conn.close()
    seats = [row[0] for row in said]
    assert "performance" in seats and "memory" in seats
    incidents = [row[2] for row in said if row[1] == "DEBRIEF_INCIDENTS"][0]
    assert incidents.startswith("1 contact: lap 1 Turn 1.")
    assert "1 passing attempts, 0 made it, 1 ended in contact." in incidents


def test_no_reference_means_no_coach_debrief_but_the_review_still_runs(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    conn = memory.connect_db("apex.db")
    session = add_session(conn, "tape_x.jsonl.gz", track="Imola")
    conn.commit()
    conn.close()
    monkeypatch.setattr(debrief, "debrief", lambda pack: 1 / 0)  # must never be called
    monkeypatch.setattr(debrief, "speak", lambda text: None)
    debrief.run_debrief(session)
    conn = memory.connect_db("apex.db")
    kinds = [row[0] for row in conn.execute("SELECT kind FROM radio_log")]
    conn.close()
    assert kinds == ["DEBRIEF_INCIDENTS"]
