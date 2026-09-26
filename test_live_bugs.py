"""Every bug found in the first live race (Le Mans, 23 Sep 2026), pinned so it cannot return."""
from dataclasses import replace

from persona import gate
import memory
from race_state import identity, laps_to_go
from radio import Call, PERFORMANCE
from seats.performance import PerformanceEngineer, SPOKEN_KINDS
from seats.race_engineer import RaceEngineer
from seats.spotter import Spotter
from seats.racecraft import Racecraft
from test_seats import moment, race, near, kinds, behind_car
from test_team_memory import add_session


def call(kind="OFF_TRACK", facts=None):
    return Call("performance", kind, 0.0, PERFORMANCE, 6.0, "x", facts=facts or {})


def test_A_coasting_is_never_spoken_and_no_line_may_say_do_not_brake():
    assert "THROTTLE_LIFT" not in SPOKEN_KINDS
    for line in ("No fucking braking at Arnage.", "Don't brake into Parabolica.", "No braking, mate."):
        assert gate(line, call())[0] is False, line
    assert gate("Brake later into Arnage, mate.", call(facts={"corner": "Arnage"}))[0] is True


def test_B_eleven_is_no_yellow_and_only_my_sector_or_the_next_one_counts():
    engineer = RaceEngineer()
    engineer.update(moment(0.0, race(0.0, {"sector_flags": [11, 11, 11]}, {"sector": 1})))
    assert engineer.update(moment(1.0, race(1.0, {"sector_flags": [11, 11, 11]}, {"sector": 1}))) == []
    # the flags are in track order (slot 0 = sector 1); checked on the 25 Sep tape, where the old
    # reading (slot = the game's sector number) called 4 yellows that were not his or next
    # yellow in sector 3 (slot 2) while I am in sector 1: not mine, not next
    assert engineer.update(moment(2.0, race(2.0, {"sector_flags": [11, 11, 1]}, {"sector": 1}))) == []
    # yellow in sector 2 (slot 1) while I am in sector 1: the next sector
    assert kinds(engineer.update(moment(3.0, race(3.0, {"sector_flags": [11, 1, 11]}, {"sector": 1})))) == ["YELLOW"]


def test_B2_only_flag_value_1_is_a_yellow():
    # replay of 25 Sep night (26 Sep): "Yellow flag. Yellow." when the clock ran out at 1387 s.
    # That was value 3, which is there before the race and at the end; on all 5 race tapes a
    # slow car sat in a sector showing 1 61.8% of the time, showing 3 2.5% (11, green: 1.7%)
    engineer = RaceEngineer()
    engineer.update(moment(0.0, race(0.0, {"sector_flags": [11, 11, 11]}, {"sector": 1})))
    assert engineer.update(moment(1.0, race(1.0, {"sector_flags": [3, 11, 11]}, {"sector": 1}))) == []


def test_B3_no_yellow_call_for_the_incident_he_is_in():
    # replay of 25 Sep night: punted at the Porsche Curves, 168 -> 28 km/h, and the game's yellow
    # for HIM came 3.4 s before the spin detector saw it, so "Yellow flag" was said to him
    engineer = RaceEngineer()
    engineer.update(moment(0.0, race(0.0, {"sector_flags": [11, 11, 11]}, {"sector": 1})))
    crawling = moment(1.0, race(1.0, {"sector_flags": [1, 11, 11]}, {"sector": 1}))
    crawling.frame.speed_kmh = 28.0
    assert engineer.update(crawling) == []


def test_H_no_spotter_in_the_garage_or_pits():
    spotter = Spotter()
    parked = replace(moment(0.0, race(0.0, me_changes={"in_pits": True}), nearby=near(0.0, (3.0, 0.0), (-3.0, 0.0))))
    parked.frame.speed_kmh = 0.0
    assert spotter.update(parked) == []
    pit_lane = moment(1.0, race(1.0, me_changes={"in_pits": True}), nearby=near(1.0, (3.0, 0.0)))
    assert spotter.update(pit_lane) == []


def test_E_racecraft_is_silent_in_qualifying():
    seat = Racecraft(PerformanceEngineer())
    quali = replace(moment(1.0, race(1.0, me_changes={"place": 5, "time_behind_leader": 8.0},
                                     opponents=[behind_car(0.3)])), session_type=6)
    assert seat.update(quali) == []


def test_D_a_zero_steam_id_falls_back_to_the_name():
    driver = behind_car(0.3)
    assert identity(replace(driver, steam_id=0)) == "name:Bob"
    assert identity(replace(driver, steam_id=765)) == "765"


# ---- the second live report (24 Sep 2026) ---------------------------------------------------

def test_no_spotter_or_racecraft_on_the_formation_lap():
    formation = race(0.0, {"game_phase": 3}, {"place": 5, "time_behind_leader": 8.0}, opponents=[behind_car(0.3)])
    rolling = moment(0.0, formation, nearby=near(0.0, (3.0, 0.0)))
    rolling.frame.speed_kmh = 72.0                  # the formation pace at Le Mans
    assert Spotter().update(rolling) == []
    assert Racecraft(PerformanceEngineer()).update(replace(rolling, session_type=10)) == []


def test_laps_to_go_does_not_count_the_last_lap_twice():
    # the live race at lap 2: 705.8 s left, 13.4 s behind the leader, 240.28 s laps.
    # He drove 3 more laps; the old count said 4 and called fuel tight with 1.4 laps spare.
    at_lap_2 = race(679.0, {"time_remaining": 705.8}, {"time_behind_leader": 13.4, "laps": 2})
    assert laps_to_go(at_lap_2, 240.28) == 3
    assert laps_to_go(race(0.0, {"max_laps": 20}, {"laps": 3}), None) == 17


def test_no_speed_is_ever_said_on_the_radio():
    for line in ("12 km/h down at Arnage.", "Carry 5 kph more.", "Twelve kilometres an hour down, mate."):
        assert gate(line, call(facts={"corner": "Arnage", "gap_kmh": 12, "x": 5}))[0] is False, line


def picture(engineer, t, lap, me_changes, opponents, time_remaining=900.0):
    snapshot = race(t, {"time_remaining": time_remaining}, me_changes, opponents=opponents)
    return engineer.update(replace(moment(t, snapshot, lap=lap, wrapped=True), session_type=10))


def ann(behind_leader, last_lap=110.0):
    return replace(behind_car(0.0), id=4, driver="Ann", steam_id=444, place=4,
                   time_behind_leader=behind_leader, last_lap=last_lap)


def bob(behind_leader, last_lap=111.0):
    return replace(behind_car(0.0), time_behind_leader=behind_leader, last_lap=last_lap)


def test_catching_the_car_ahead_says_by_which_lap():
    engineer = RaceEngineer()
    me_now = {"place": 5, "time_behind_leader": 8.0, "last_lap": 110.5}
    assert picture(engineer, 110.0, 1, me_now, [ann(6.0)]) == []          # first line: no trend yet
    calls = picture(engineer, 220.0, 2, me_now, [ann(6.4)])             # 2.0 -> 1.6
    catching = [c for c in calls if c.kind == "CATCHING"][0]
    # on lap 2, 1.6 / 0.4 = 4 laps of closing; "by lap" is an upper bound (x1.5, 25 Sep field
    # study: catch timing off by a median 64%, inside 1.5x ~95%): 6 laps -> by lap 7
    assert catching.facts["catch_lap"] == 7
    # v3: no names ("I have to look up their names above, and the pronunciation is weird")
    assert catching.template == "Car ahead, 1.6. You're taking 4 tenths a lap. On it by lap 7."


def test_not_closing_gives_the_lap_time_that_catches_him_by_the_flag():
    engineer = RaceEngineer()
    me_now = {"place": 5, "time_behind_leader": 8.0, "last_lap": 110.5}
    picture(engineer, 110.0, 1, me_now, [ann(6.2)], time_remaining=300.0)
    calls = picture(engineer, 220.0, 2, me_now, [ann(6.2)], time_remaining=300.0)
    target = [c for c in calls if c.kind == "PACE_TARGET"][0]
    # 1.8 s to find in 3 laps: 0.6 a lap under Ann's 1:50.0
    assert target.template == "Car ahead's doing 1:50.0. You need 1:49.4 to catch it by the flag."
    # 3 s in 3 laps is a second a lap: more than 0.6 % of a lap, not a real target
    other = RaceEngineer()
    picture(other, 110.0, 1, me_now, [ann(5.0)], time_remaining=300.0)
    assert picture(other, 220.0, 2, me_now, [ann(5.0)], time_remaining=300.0) == []


def test_a_car_closing_from_behind_is_called_with_the_lap_time_to_hold_him():
    engineer = RaceEngineer()
    me_now = {"place": 5, "time_behind_leader": 8.0, "last_lap": 110.5}
    picture(engineer, 110.0, 1, me_now, [bob(10.0)])
    calls = picture(engineer, 220.0, 2, me_now, [bob(9.6)])             # 2.0 -> 1.6
    threat = [c for c in calls if c.kind == "THREAT_BEHIND"][0]
    assert threat.template == "Car behind, 1.6. Closing 4 tenths a lap. On you by lap 5. Match 1:51.0."


def test_a_job_collects_clean_laps_across_races_at_its_own_track(tmp_path):
    conn = memory.connect_db(str(tmp_path / "apex.db"))

    def race_at(track, laps):
        session = add_session(conn, "tape.jsonl.gz", track=track)
        for lap in range(1, laps + 1):
            memory.save_lap(conn, session, lap, 1)
            memory.save_corner_stat(conn, session, type("S", (), {
                "lap_count": lap, "corner": "Arnage", "brake_onset": 10000.0, "min_speed": 70.0,
                "slow_zone": 40.0, "coast": 5.0})())
        return session

    first = race_at("Circuit de la Sarthe", 4)
    memory.save_contract(conn, first, {"corner": "Arnage", "focus": "Let it roll.", "metric": "min_speed",
                                       "baseline": 67.6, "target": 69.5, "min_laps": 8})
    second = race_at("Circuit de la Sarthe", 5)
    race_at("Autodromo Nazionale Monza", 6)                    # another track: does not count
    third = race_at("Circuit de la Sarthe", 4)
    job = memory.load_latest_contract(conn, second + 1, "Circuit de la Sarthe")
    assert memory.evaluate_contract(conn, job, second) == {"verdict": "insufficient", "laps": 5}
    assert memory.evaluate_contract(conn, job, third)["verdict"] == "hit"      # 9 laps at 70.0
    assert memory.load_latest_contract(conn, third + 1, "Autodromo Nazionale Monza") is None


def test_the_leaders_flag_means_last_lap_then_the_result_when_i_finish():
    engineer = RaceEngineer()
    running = {"place": 5, "time_behind_leader": 8.0, "finish_status": 0}
    engineer.update(replace(moment(1.0, race(1.0, {"game_phase": 5}, running)), session_type=10))
    flag = engineer.update(replace(moment(2.0, race(2.0, {"game_phase": 8}, running)), session_type=10))
    assert kinds(flag) == ["FLAG_LAST_LAP"]
    done = engineer.update(replace(moment(3.0, race(3.0, {"game_phase": 8}, dict(running, finish_status=1))), session_type=10))
    assert kinds(done) == ["FINISH"] and done[0].template == "Chequered flag. P5."


def test_a_timed_race_is_counted_with_the_leaders_pace():
    # live 25 Sep: a "5-lap" race became 6 laps; the leader lapped faster and Apex said "last lap" on lap 5
    from dataclasses import replace as swap
    from test_racecraft import rival
    leader = swap(rival(1, 0.0), laps=4, lap_dist=500.0, last_lap=236.0, best_lap=236.0)
    backmarker = swap(rival(9, 30.0, car_id=7), lap_dist=13000.0)          # gives the lap length
    snapshot = race(0.0, {"time_remaining": 240.0}, {"laps": 4, "place": 5, "time_behind_leader": 20.0,
                                                    "last_lap": 242.0}, opponents=[leader, backmarker])
    # the leader crosses in ~227 s, has 13 s left, so starts one more lap: he does laps 5 and 6
    assert laps_to_go(snapshot, 242.0) == 2
