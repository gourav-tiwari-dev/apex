import sqlite3

import pytest

import memory
from memory.team_memory import (
    Fact,
    build_profile,
    drives,
    save_fact,
    brief_facts,
    habits_at,
    rival,
)


def new_db(tmp_path):
    return memory.connect_db(str(tmp_path / "memory.db"))


def add_session(
    conn,
    tape,
    end_reason="session_over",
    session_type=10,
    track="Monza",
    final_place=None,
):
    cur = conn.execute(
        "INSERT INTO sessions (started_at, tape_path, end_reason, session_type, track, final_place) "
        "VALUES ('2026-09-23', ?, ?, ?, ?, ?)",
        (tape, end_reason, session_type, track, final_place),
    )
    return cur.lastrowid


def add_event(conn, session_id, kind, corner, lap=3, other_car=None):
    cur = conn.execute(
        "INSERT INTO events (session_id, kind, sim_time, speed_kmh, corner, lap_dist, lap_count, other_car) "
        "VALUES (?, ?, 1.0, 150.0, ?, 100.0, ?, ?)",
        (session_id, kind, corner, lap, other_car),
    )
    return cur.lastrowid


def test_a_replayed_tape_counts_as_one_drive(tmp_path):
    conn = new_db(tmp_path)
    add_session(conn, "tape_a.jsonl.gz", end_reason="stopped_by_driver")  # the live run
    replay = add_session(conn, "tape_a.jsonl.gz", end_reason="tape_end")
    other = add_session(conn, "tape_b.jsonl.gz")
    assert drives(conn) == [replay, other]  # tape_a once: its latest finished run


def test_one_bad_lap_is_not_a_habit(tmp_path):
    conn = new_db(tmp_path)
    first = add_session(conn, "tape_a.jsonl.gz")
    add_event(conn, first, "OFF_TRACK", "T11 Parabolica")
    add_event(conn, first, "OFF_TRACK", "T11 Parabolica")
    add_event(conn, first, "OFF_TRACK", "T11 Parabolica")  # 3 times, but one drive
    build_profile(conn)
    assert habits_at(conn, "Monza", "T11 Parabolica") == []


def test_a_habit_across_drives_is_a_fact_with_its_evidence(tmp_path):
    conn = new_db(tmp_path)
    first = add_session(conn, "tape_a.jsonl.gz")
    second = add_session(conn, "tape_b.jsonl.gz")
    ids = [
        add_event(conn, first, "OFF_TRACK", "T11 Parabolica"),
        add_event(conn, first, "OFF_TRACK", "T11 Parabolica"),
        add_event(conn, second, "OFF_TRACK", "T11 Parabolica"),
    ]
    build_profile(conn)
    habits = habits_at(conn, "Monza", "T11 Parabolica")
    assert len(habits) == 1
    assert habits[0]["summary"] == "off track at T11 Parabolica: 3 times in 2 drives"
    evidence = [
        row[0]
        for row in conn.execute(
            "SELECT event_id FROM profile_evidence WHERE fact_id = ? ORDER BY event_id",
            (habits[0]["fact_id"],),
        )
    ]
    assert evidence == ids


def test_replays_do_not_inflate_a_habit(tmp_path):
    conn = new_db(tmp_path)
    for run in range(4):  # the same drive replayed 4 times
        session = add_session(conn, "tape_a.jsonl.gz", end_reason="tape_end")
        add_event(conn, session, "OFF_TRACK", "T11 Parabolica")
        add_event(conn, session, "OFF_TRACK", "T11 Parabolica")
    build_profile(conn)
    assert habits_at(conn, "Monza", "T11 Parabolica") == []


def test_a_fact_without_evidence_is_refused(tmp_path):
    conn = new_db(tmp_path)
    with pytest.raises(ValueError):
        save_fact(
            conn, Fact("corner_habit", "Monza", "T1 Rettifilo", 1, 1, 1, "made up")
        )


def test_evidence_must_point_at_a_real_event(tmp_path):
    conn = new_db(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        ghost = Fact("corner_habit", "Monza", "T1 Rettifilo", 1, 1, 1, "ghost")
        ghost.event_ids = [999]
        save_fact(conn, ghost)


def test_lap_one_trouble_across_races(tmp_path):
    conn = new_db(tmp_path)
    for tape in ("a", "b", "c"):
        race = add_session(conn, f"tape_{tape}.jsonl.gz")
        if tape != "c":
            add_event(conn, race, "CONTACT", "T1 Rettifilo", lap=1, other_car="765")
    build_profile(conn)
    lap_one = brief_facts(conn, "Monza")[0]
    assert (
        lap_one["summary"]
        == "lap 1 trouble (contact, off or spin) in 2 of your last 3 races"
    )


def test_rival_dossier(tmp_path):
    conn = new_db(tmp_path)
    for tape, my_place, their_place in (("a", 3, 5), ("b", 4, 2)):
        race = add_session(conn, f"tape_{tape}.jsonl.gz", final_place=my_place)
        conn.execute(
            "INSERT INTO rivals_seen (session_id, steam_id, driver, final_place) VALUES (?, '765', 'Ann', ?)",
            (race, their_place),
        )
        if tape == "b":
            add_event(conn, race, "CONTACT", "T1 Rettifilo", other_car="765")
    build_profile(conn)
    assert (
        rival(conn, 765)["summary"]
        == "Ann: raced 2 times, you finished ahead 1, contact 1 times"
    )
