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
from test_seats import moment, race, near, kinds, behind_car, frame
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


def test_I_in_a_race_the_lap_he_is_on_is_the_games():
    # replay of 25 Sep night (27 Sep): his own line crossings made the formation lap "lap 1", so
    # race lap 1 was "lap 2" for the coach, his reminders and the debrief ("2 contacts: lap 2")
    from live_telemetry import LapCounter
    counter = LapCounter()

    def at(t, lap_dist, game_laps, green):
        f = frame(float(t))
        f.lap_dist = float(lap_dist)
        counter.update(f)
        return counter.race_lap(game_laps, float(t), green)

    assert at(0, 13400, 0, False) == 1               # on the grid for the formation lap
    assert at(5, 50, 0, False) == 1                  # crosses the line on the formation lap
    assert at(60, 13400, 0, False) == 1              # back on the grid
    assert at(61, 13450, 0, True) == 1               # lights out
    assert at(64, 20, 0, True) == 1                  # the start line is not a lap finished
    assert at(200, 9000, 0, True) == 1
    assert at(300, 10, 0, True) == 2                 # lap 1 done: counts before the game's count does
    assert at(300.2, 60, 0, True) == 2
    assert at(300.4, 110, 1, True) == 2              # the game caught up


def test_J_after_a_crash_he_is_asked_if_he_is_ok_once():
    # his mark, 25 Sep (58-car race): "it doesn't know that I crashed and spun, my race is over".
    # Replay: 214 -> 6 km/h at Indianapolis, stopped a minute, and nobody asked. A pit wall's first
    # question after a crash is "Are you OK?"
    from live_telemetry import Event
    engineer = RaceEngineer()
    engineer.update(moment(0.0, race(0.0, {}, {"sector": 1})))
    spin = moment(1.0, race(1.0, {}, {"sector": 1}), new_race=False)      # incidents come on car frames
    spin.events = [Event(kind="SPIN", sim_time=1.0, speed_kmh=150.0, corner="Indianapolis")]
    engineer.update(spin)
    said = []
    for t in (2.0, 3.0, 4.0, 5.0, 6.0, 10.0):
        stopped = moment(t, race(t, {}, {"sector": 1}))
        stopped.frame.speed_kmh = 3.0
        said += engineer.update(stopped)
    assert kinds(said) == ["ARE_YOU_OK"]


def test_J2_no_blue_flag_calls_while_he_crawls():
    # the same replay: "Blue flag. Let him by on the exit." three times in 6 s at 5-9 km/h
    engineer = RaceEngineer()
    engineer.update(moment(0.0, race(0.0, {}, {"sector": 1, "flag": 0})))
    crawling = moment(1.0, race(1.0, {}, {"sector": 1, "flag": 6}))
    crawling.frame.speed_kmh = 8.0
    assert engineer.update(crawling) == []
    engineer.update(moment(2.0, race(2.0, {}, {"sector": 1, "flag": 0})))
    racing = moment(3.0, race(3.0, {}, {"sector": 1, "flag": 6}))
    assert kinds(engineer.update(racing)) == ["BLUE_FLAG"]


def test_J3_no_wide_call_right_after_a_spin_or_a_hit():
    # the same replay: the spin call, then "Wide at Indianapolis" twice for the same crash
    from live_telemetry import Event
    from seats.performance import PerformanceEngineer
    seat = PerformanceEngineer()
    assert seat.call_for_event(Event(kind="SPIN", sim_time=1045.2, speed_kmh=200.0, corner="Indianapolis"), 1) is not None
    assert seat.call_for_event(Event(kind="OFF_TRACK", sim_time=1046.7, speed_kmh=40.0, corner="Indianapolis"), 2) is None
    assert seat.call_for_event(Event(kind="OFF_TRACK", sim_time=1080.0, speed_kmh=150.0, corner="Arnage"), 3) is not None
    # parked 25 s after the crash, the car crept onto the grass at 1 km/h: not "wide"
    assert seat.call_for_event(Event(kind="OFF_TRACK", sim_time=1070.6, speed_kmh=1.0, corner="Indianapolis"), 5) is None
    seat.saw_hit(1200.0)                         # 25 Sep night: hit at the Porsche Curves, then "Wide"
    assert seat.call_for_event(Event(kind="OFF_TRACK", sim_time=1204.0, speed_kmh=90.0, corner="Porsche Curves"), 4) is None


def test_K_track_limits_are_said_when_a_penalty_gets_near_not_on_every_step():
    # 24 Sep: "Track limits at X. Keep it inside." at steps 3, 5, 6 and 7 of 20, never near a penalty.
    # Crew Chief users' commonest complaint is the same thing: off-track warnings every time "since
    # they already know" (27 Sep, RACE_MODEL.md). Said from half way, then on each of the last 3
    engineer = RaceEngineer()
    engineer.update(moment(0.0, race(0.0, {"limit_steps_per_penalty": 20}, {"track_limit_steps": 0})))
    said = {}
    for t, steps in enumerate((3, 5, 6, 7, 9, 10, 12, 16, 17, 18, 19), start=1):
        calls = engineer.update(moment(float(t), race(float(t), {"limit_steps_per_penalty": 20},
                                                      {"track_limit_steps": steps}), corner="Arnage"))
        said[steps] = "TRACK_LIMITS" in kinds(calls)
    assert [steps for steps, spoken in said.items() if spoken] == [10, 17, 18, 19]


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


def mixed_field():
    """Him (GT3, P5 overall) behind three Hypercars and one GT3: P2 in his class."""
    cars = [replace(behind_car(0.3), id=20 + n, place=n, car_class="Hypercar", steam_id=20 + n) for n in (1, 2, 3)]
    cars.append(replace(behind_car(0.3), id=30, place=4, car_class="GT3", steam_id=30))
    return cars


def test_L_in_a_multiclass_race_his_place_is_his_place_in_class():
    # replay of the 58-car race (27 Sep): "Settled. P54, up seven." and "P43." were overall places
    # among Hypercars and LMP2s; among the GT3s he races he was P18 and P7
    from race_state import said_place, class_place, multiclass
    mixed = race(1.0, {}, {"place": 5}, opponents=mixed_field())
    assert multiclass(mixed)
    assert class_place(mixed, 5, "GT3") == 2
    assert said_place(mixed) == "P2 in class"
    alone_in_class = race(1.0, {}, {"place": 5}, opponents=[replace(c, car_class="GT3") for c in mixed_field()])
    assert not multiclass(alone_in_class)
    assert said_place(alone_in_class) == "P5"


def test_L2_the_finish_and_the_gap_report_say_the_class_place():
    engineer = RaceEngineer()
    engineer.update(moment(0.0, race(0.0, {}, {"place": 5}, opponents=mixed_field())))
    done = engineer.update(replace(moment(1.0, race(1.0, {}, {"place": 5, "finish_status": 1}, opponents=mixed_field())),
                                   session_type=10))
    finish = [c for c in done if c.kind == "FINISH"][0]
    assert finish.template == "Chequered flag. P2 in class."
    report = RaceEngineer().gap_report(race(2.0, {}, {"place": 5}, opponents=mixed_field()), 2.0)
    assert report.template.startswith("P2 in class.")
