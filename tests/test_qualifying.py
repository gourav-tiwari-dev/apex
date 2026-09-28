from dataclasses import replace

from seats.qualifying import QualifyingEngineer
from words import lap_text
from seats.strategist import Strategist
from test_seats import race, moment, behind_car, kinds

QUALI = 5


def quali_moment(t, snapshot, wrapped=False, lap=2):
    m = moment(t, snapshot, lap=lap, wrapped=wrapped)
    m.session_type = QUALI
    return m


def rival(best, car_id=9, car_class="GT3"):
    return replace(behind_car(1.0), id=car_id, best_lap=best, car_class=car_class)


def test_lap_text():
    assert lap_text(239.615) == "3:59.6"
    assert lap_text(119.97) == "2:00.0"


def test_best_lap_says_where_it_puts_him():
    seat = QualifyingEngineer()
    snapshot = race(
        10.0,
        me_changes={"last_lap": 110.0, "best_lap": 110.0},
        opponents=[rival(109.0, 1), rival(111.0, 2), rival(108.8, 3, "LMP2")],
    )
    calls = seat.update(quali_moment(10.0, snapshot, wrapped=True))
    assert calls[0].kind == "QUALI_LAP"
    # the LMP2 is not his class; one GT3 is quicker
    assert calls[0].template == "1:50.0. Best lap. P2 in class, 1.0 off pole."


def test_slower_lap_says_how_far_off_and_pole_is_pole():
    seat = QualifyingEngineer()
    snapshot = race(
        10.0,
        me_changes={"last_lap": 110.6, "best_lap": 110.0},
        opponents=[rival(111.0)],
    )
    calls = seat.update(quali_moment(10.0, snapshot, wrapped=True))
    assert calls[0].template == "1:50.6. 0.6 off your best. P1 in class, on pole."


def test_a_lap_that_does_not_stand_is_said():
    seat = QualifyingEngineer()
    snapshot = race(10.0, me_changes={"last_lap": 108.0, "best_lap": -1.0})
    assert (
        seat.update(quali_moment(10.0, snapshot, wrapped=True))[0].template
        == "1:48.0. Doesn't count."
    )


def test_clock_one_more_lap_then_nothing_twice():
    seat = QualifyingEngineer()
    snapshot = race(
        10.0, {"time_remaining": 150.0}, {"last_lap": 110.0, "best_lap": 110.0}
    )
    calls = seat.update(quali_moment(10.0, snapshot, wrapped=True))
    assert kinds(calls) == ["QUALI_LAP", "QUALI_CLOCK"]
    assert "one more" in calls[1].template
    assert kinds(seat.update(quali_moment(120.0, snapshot, wrapped=True))) == [
        "QUALI_LAP"
    ]


def test_the_lap_after_the_clock_runs_out_is_still_called():
    seat = QualifyingEngineer()
    snapshot = race(
        10.0,
        {"game_phase": 8, "time_remaining": 0.0},
        {"last_lap": 110.0, "best_lap": 110.0},
    )
    assert kinds(seat.update(quali_moment(10.0, snapshot, wrapped=True))) == [
        "QUALI_LAP"
    ]


def test_silent_in_a_race():
    seat = QualifyingEngineer()
    snapshot = race(10.0, me_changes={"last_lap": 110.0, "best_lap": 110.0})
    assert seat.update(moment(10.0, snapshot, wrapped=True)) == []  # session 10 = race


def test_no_fuel_box_or_last_lap_calls_in_qualifying():
    strategist = Strategist()
    calls = []
    for lap, fuel in enumerate([30.0, 27.0, 24.0], start=1):
        snapshot = race(lap * 110.0, {"max_laps": 3}, {"fuel": fuel, "laps": lap})
        calls += strategist.update(
            quali_moment(lap * 110.0, snapshot, wrapped=True, lap=lap)
        )
    assert "FUEL" not in kinds(calls) and "LAST_LAP" not in kinds(calls)
