"""Every bug found in the first live race (Le Mans, 23 Sep 2026), pinned so it cannot return."""
from dataclasses import replace

from persona import gate
from race_state import identity
from radio import Call, PERFORMANCE
from seats.performance import PerformanceEngineer, SPOKEN_KINDS
from seats.race_engineer import RaceEngineer
from seats.spotter import Spotter
from seats.racecraft import Racecraft
from test_seats import moment, race, near, kinds, behind_car


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
    # yellow in sector 3 (index 0) while I am in sector 1: not mine, not next
    assert engineer.update(moment(2.0, race(2.0, {"sector_flags": [1, 11, 11]}, {"sector": 1}))) == []
    # yellow in sector 2 (index 2) while I am in sector 1: the next sector
    assert kinds(engineer.update(moment(3.0, race(3.0, {"sector_flags": [11, 11, 1]}, {"sector": 1})))) == ["YELLOW"]


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
