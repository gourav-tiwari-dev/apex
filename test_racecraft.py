from dataclasses import replace

import memory
from live_telemetry import Event
from race_state import Opponent
from seats.performance import PerformanceEngineer, CornerPass
from seats.racecraft import Racecraft
from team_memory import build_profile, facts
from test_seats import moment, race, near, kinds
from test_team_memory import add_session

CORNERS = [
    {"name": "T1 Rettifilo", "start": 747, "end": 1088},
    {"name": "T8 Ascari", "start": 3795, "end": 4318},
    {"name": "T11 Parabolica", "start": 4992, "end": 5584},
]


def rival(place, behind_leader, steam_id=111, driver="Ann", car_class="GT3", car_id=5):
    return Opponent(
        id=car_id,
        driver=driver,
        steam_id=steam_id,
        car_class=car_class,
        car_name="Ferrari",
        place=place,
        laps=3,
        lap_dist=4000.0,
        time_behind_next=0.0,
        time_behind_leader=behind_leader,
        laps_behind_leader=0,
        best_lap=110.0,
        last_lap=110.0,
        in_pits=False,
        pit_state=0,
        pitstops=0,
        finish_status=0,
        control=2,
        flag=0,
        speed_kmh=150.0,
    )


def seat_with_data(my_speeds, his_speeds, steam_id="111"):
    performance = PerformanceEngineer()
    for corner, speed in my_speeds.items():
        performance.my_speeds[corner] = [speed, speed]
    for corner, speed in his_speeds.items():
        for lap in (1, 2):
            performance.opponents.rows.append(
                CornerPass(
                    who=steam_id,
                    driver="Ann",
                    car_class="GT3",
                    car_model=None,
                    corner=corner,
                    lap=lap,
                    min_speed=speed,
                )
            )
    return Racecraft(performance)


def step(
    seat,
    t,
    me_changes=None,
    opponents=None,
    new_race=True,
    events=(),
    lap_dist=100.0,
    **kw,
):
    snapshot = race(
        t,
        me_changes=me_changes or {"place": 5, "time_behind_leader": 8.0},
        opponents=opponents or [],
    )
    m = replace(
        moment(t, snapshot, new_race=new_race, **kw),
        corners=CORNERS,
        events=list(events),
    )
    m.frame.lap_dist = lap_dist
    return seat.update(m)


def test_a_car_brushing_past_is_not_a_fight_yet():
    # v3: a fight is a fight after 8 s within a second
    seat = Racecraft(PerformanceEngineer())
    assert step(seat, 1.0, opponents=[rival(4, 7.4)]) == []


def test_a_fight_with_no_data_yet_says_stay_close_no_lunges():
    seat = Racecraft(PerformanceEngineer())
    step(seat, 1.0, opponents=[rival(4, 7.4)])
    calls = step(seat, 10.0, opponents=[rival(4, 7.4)])
    assert kinds(calls) == ["ATTACK_PLAN"]
    assert "No lunges" in calls[0].conclusion
    assert calls[0].facts["driver"] == "Ann"


def test_attack_plan_uses_the_exit_of_your_strong_corner():
    seat = seat_with_data(
        {"T8 Ascari": 130.0, "T1 Rettifilo": 60.0},
        {"T8 Ascari": 125.0, "T1 Rettifilo": 61.0},
    )
    step(seat, 1.0, opponents=[rival(4, 7.4)])
    call = step(seat, 10.0, opponents=[rival(4, 7.4)])[0]
    assert call.facts["strong_corner"] == "T8 Ascari"
    assert call.facts["pass_corner"] == "T11 Parabolica"
    assert (
        call.template
        == "You're faster out of T8 Ascari. Pass into T11 Parabolica. Not before."
    )


def test_the_plan_upgrades_once_when_real_data_arrives_then_stays_quiet():
    seat = Racecraft(PerformanceEngineer())
    step(seat, -9.0, opponents=[rival(4, 7.4)])
    assert kinds(step(seat, 1.0, opponents=[rival(4, 7.4)])) == ["ATTACK_PLAN"]
    assert step(seat, 2.0, opponents=[rival(4, 7.4)]) == []
    seat.performance.my_speeds["T8 Ascari"] = [130.0, 130.0]
    seat.performance.opponents.rows += [
        CornerPass(
            who="111",
            driver="Ann",
            car_class="GT3",
            car_model=None,
            corner="T8 Ascari",
            lap=1,
            min_speed=125.0,
        )
    ] * 2
    assert step(seat, 3.0, opponents=[rival(4, 7.4)]) == []  # one plan a minute
    assert kinds(step(seat, 70.0, opponents=[rival(4, 7.4)])) == ["ATTACK_PLAN"]
    assert step(seat, 140.0, opponents=[rival(4, 7.4)]) == []


def test_other_classes_are_not_a_fight():
    seat = Racecraft(PerformanceEngineer())
    assert step(seat, 1.0, opponents=[rival(4, 7.8, car_class="Hypercar")]) == []


def test_defend_plan_names_his_strong_corner():
    seat = seat_with_data({"T8 Ascari": 125.0}, {"T8 Ascari": 130.0}, steam_id="222")
    step(seat, 1.0, opponents=[rival(6, 8.5, steam_id=222, driver="Bob", car_id=9)])
    calls = step(
        seat, 10.0, opponents=[rival(6, 8.5, steam_id=222, driver="Bob", car_id=9)]
    )
    assert kinds(calls) == ["DEFEND_PLAN"]
    assert calls[0].facts["cover_corner"] == "T11 Parabolica"


def test_not_here_before_a_corner_where_he_is_as_quick():
    seat = seat_with_data({"T11 Parabolica": 150.0}, {"T11 Parabolica": 151.0})
    step(seat, 1.0, opponents=[rival(4, 7.7)])  # 0.3 s behind him
    calls = step(seat, 1.1, new_race=False, lap_dist=4800.0)  # 192 m before Parabolica
    assert kinds(calls) == ["NOT_HERE"]
    assert calls[0].urgent
    assert step(seat, 1.2, new_race=False, lap_dist=4850.0) == []  # once per lap


def test_no_warning_into_his_weak_corner():
    seat = seat_with_data({"T11 Parabolica": 155.0}, {"T11 Parabolica": 150.0})
    step(seat, 1.0, opponents=[rival(4, 7.7)])
    assert step(seat, 1.1, new_race=False, lap_dist=4800.0) == []


def test_contact_gets_a_calm_reset_not_blame():
    seat = Racecraft(PerformanceEngineer())
    hit = Event(
        kind="CONTACT",
        sim_time=5.0,
        speed_kmh=90.0,
        corner="T1 Rettifilo",
        other_car="111",
    )
    calls = step(seat, 5.0, events=[hit])
    assert kinds(calls) == ["CONTACT_RESET"]
    assert "No blame" in calls[0].conclusion


def test_losing_a_place_gets_composure():
    seat = Racecraft(PerformanceEngineer())
    step(
        seat,
        1.0,
        me_changes={"place": 5, "time_behind_leader": 8.0},
        opponents=[rival(6, 9.0, car_id=9)],
    )
    step(
        seat,
        2.0,
        me_changes={"place": 6, "time_behind_leader": 8.6},
        opponents=[rival(5, 8.5, car_id=9)],
    )
    calls = step(
        seat,
        3.1,
        me_changes={"place": 6, "time_behind_leader": 8.6},
        opponents=[rival(5, 8.5, car_id=9)],
    )
    assert "PASSED" in kinds(calls)  # once it has held a second


def seat_race():
    return race(
        1.1,
        me_changes={"place": 5, "time_behind_leader": 8.0},
        opponents=[rival(4, 7.7)],
    )


def test_pass_attempt_outcomes_are_recorded():
    seat = Racecraft(PerformanceEngineer())
    step(seat, 1.0, opponents=[rival(4, 7.7)])
    assert seat.open_attempt is None
    # alongside the car ahead (car id 5), on the brakes, in a corner
    alongside = near(1.1, (3.0, 1.0))
    alongside.cars[0].id = 5
    m = replace(
        moment(
            1.1, seat_race(), new_race=False, corner="T1 Rettifilo", nearby=alongside
        ),
        corners=CORNERS,
    )
    m.frame.brake = 0.6
    seat.update(m)
    assert seat.open_attempt is not None
    step(
        seat,
        1.2,
        me_changes={"place": 4, "time_behind_leader": 7.7},
        opponents=[rival(5, 7.9)],
    )
    assert seat.attempts[0][-1] == "pass"


def test_team_memory_measures_the_hasty_commit_habit(tmp_path):
    conn = memory.connect_db(str(tmp_path / "m.db"))
    for tape, outcomes in (("a", ["contact", "pass"]), ("b", ["contact", "no_pass"])):
        session = add_session(conn, f"tape_{tape}.jsonl.gz")
        memory.save_pass_attempts(
            conn, session, [("111", "Ann", "T1 Rettifilo", 1, o) for o in outcomes]
        )
    build_profile(conn)
    fact = facts(conn, "pass_attempts")[0]
    assert fact["summary"] == "pass attempts: 4, 1 passes, 2 ended in contact"
