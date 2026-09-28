from dataclasses import replace

from driving.detectors import RearSnapDetector
from driving.corner_stats import CornerStat
from race_state import Opponent
from seats.performance import PerformanceEngineer, OpponentCorners, what_to_change
from test_seats import frame, moment, race, kinds

CORNERS = [{"name": "T8 Ascari", "start": 3795, "end": 4318}]


def finish_corner(
    seat, lap, speed, t, corner="T8 Ascari", time_s=10.0, brake=3800.0, power=4000.0
):
    stat = CornerStat(
        lap_count=lap,
        corner=corner,
        brake_onset=brake,
        min_speed=speed,
        slow_zone=50.0,
        coast=0.0,
        time_s=time_s,
        throttle_on=power,
    )
    return seat.update(replace(moment(t), corner_stat=stat))


def cross_line(seat, t, lap, race_state=None, session_type=10):
    return seat.update(
        replace(moment(t, race_state, lap=lap, wrapped=True), session_type=session_type)
    )


def test_one_corner_call_per_lap_in_time_and_what_to_do_never_a_speed():
    seat = PerformanceEngineer()
    finish_corner(seat, 1, 130.0, 1.0)  # my best
    finish_corner(seat, 1, 150.0, 2.0, corner="T11 Parabolica", time_s=8.0)
    cross_line(seat, 3.0, 2)
    finish_corner(seat, 2, 130.0, 4.0, time_s=10.4, brake=3790.0)  # braked 10 m early
    finish_corner(
        seat, 2, 145.0, 5.0, corner="T11 Parabolica", time_s=8.3
    )  # over-slowed
    calls = cross_line(seat, 6.0, 3)
    assert kinds(calls) == ["CORNER_LOSS"]
    assert calls[0].facts == {
        "corner": "T8 Ascari",
        "time_lost_s": 0.4,
        "car_lengths": 2,
    }
    assert (
        calls[0].template
        == "T8 Ascari: 4 tenths off your best. Brake later, about 2 car lengths."
    )
    assert "km/h" not in calls[0].conclusion


def test_a_loss_inside_the_noise_is_not_said():
    seat = PerformanceEngineer()
    finish_corner(seat, 1, 130.0, 1.0)
    cross_line(seat, 2.0, 2)
    finish_corner(seat, 2, 127.0, 3.0, time_s=10.1)  # a tenth: lap-to-lap noise
    assert cross_line(seat, 4.0, 3) == []


def test_a_loss_the_measurements_do_not_explain_is_not_said():
    seat = PerformanceEngineer()
    finish_corner(seat, 1, 130.0, 1.0)
    cross_line(seat, 2.0, 2)
    finish_corner(seat, 2, 130.0, 3.0, time_s=10.5)  # same brake point, speed and power
    assert cross_line(seat, 4.0, 3) == []


def test_braking_later_and_slower_in_the_middle_is_going_in_too_deep():
    mine = {"brake_onset": 3820.0, "min_speed": 120.0, "throttle_on": 4000.0}
    best = {"brake_onset": 3800.0, "min_speed": 128.0, "throttle_on": 4000.0}
    assert what_to_change(mine, best, 8.0) == ("too_deep", None)


def opponent(
    lap_dist,
    speed,
    laps,
    driver="Ann",
    car_class="GT3",
    car_id=5,
    model=None,
    brake=0.0,
    throttle=1.0,
):
    return Opponent(
        id=car_id,
        driver=driver,
        steam_id=100 + car_id,
        car_class=car_class,
        car_name="Ferrari",
        place=1,
        laps=laps,
        lap_dist=lap_dist,
        time_behind_next=0.0,
        time_behind_leader=0.0,
        laps_behind_leader=0,
        best_lap=108.0,
        last_lap=108.0,
        in_pits=False,
        pit_state=0,
        pitstops=0,
        finish_status=0,
        control=2,
        flag=0,
        speed_kmh=speed,
        brake=brake,
        throttle=throttle,
        car_model=model,
    )


def drive_ascari(tracker, lap, start_t, driver="Ann", car_id=5, model=None, slow=1.0):
    """One pass through Ascari (3795-4318) at 5 Hz-ish: before, brake, apex, power, after."""
    samples = [
        (3700.0, 0.0, 200.0, 0.0, 1.0),
        (3900.0, 1.0, 150.0, 1.0, 0.0),
        (4100.0, 2.0, 130.0, 0.0, 0.2),
        (4200.0, 2.5, 140.0, 0.0, 1.0),
        (4400.0, 3.0, 180.0, 0.0, 1.0),
    ]
    for distance, t, speed, brake, throttle in samples:
        tracker.update(
            race(
                start_t + t * slow,
                opponents=[
                    opponent(
                        distance,
                        speed,
                        lap,
                        driver,
                        "GT3",
                        car_id,
                        model,
                        brake,
                        throttle,
                    )
                ],
            ),
            CORNERS,
            "GT3",
        )


def test_fastest_car_reference_needs_two_of_its_laps_and_times_the_corner():
    tracker = OpponentCorners()
    drive_ascari(tracker, 1, 0.0)
    assert tracker.fastest_through("T8 Ascari") is None  # one lap is not a reference
    drive_ascari(tracker, 2, 100.0)
    fastest = tracker.fastest_through("T8 Ascari")
    assert fastest["driver"] == "Ann"
    # entered at 3795 (t 0.475), left at 4318 (t 2.795): interpolated between the snapshots
    assert abs(fastest["time_s"] - 2.32) < 0.01
    assert fastest["min_speed"] == 130.0


def test_the_reference_is_the_fastest_car_of_my_model_before_the_fastest_of_the_class():
    tracker = OpponentCorners()
    for lap in (1, 2):
        drive_ascari(
            tracker,
            lap,
            lap * 100.0,
            driver="Evan",
            car_id=6,
            model="Porsche 911 GT3 R",
        )
        drive_ascari(
            tracker,
            lap,
            lap * 100.0 + 50,
            driver="Steve",
            car_id=7,
            model="BMW M4 LMGT3",
            slow=1.2,
        )
    assert tracker.fastest_through("T8 Ascari")["driver"] == "Evan"
    best_bmw = tracker.fastest_through("T8 Ascari", "BMW M4 LMGT3")
    assert best_bmw["driver"] == "Steve" and best_bmw["same_model"] is True
    # no car of my model with the laps: the class
    assert tracker.fastest_through("T8 Ascari", "Ferrari 296 LMGT3")["driver"] == "Evan"


def test_other_classes_are_not_a_reference():
    tracker = OpponentCorners()
    tracker.update(
        race(0.0, opponents=[opponent(4000.0, 170.0, 1, car_class="Hypercar")]),
        CORNERS,
        "GT3",
    )
    tracker.update(
        race(0.0, opponents=[opponent(4500.0, 250.0, 1, car_class="Hypercar")]),
        CORNERS,
        "GT3",
    )
    assert tracker.rows == []


def test_praise_for_a_new_best_lap_at_most_twice():
    seat = PerformanceEngineer()
    calls = []
    for lap, best in ((2, 111.0), (3, 110.5), (4, 110.2), (5, 110.0)):
        calls += cross_line(
            seat, lap * 111.0, lap, race(0.0, me_changes={"best_lap": best})
        )
    praise = [c for c in calls if c.kind == "PRAISE"]
    assert len(praise) == 2
    assert praise[0].facts == {"gain_s": 0.5}


def test_qualifying_lap_report_left_the_performance_seat():
    # 25 Sep: the qualifying lap call lives in seats/qualifying.py (see test_qualifying.py)
    seat = PerformanceEngineer()
    calls = cross_line(
        seat,
        111.0,
        2,
        race(0.0, me_changes={"best_lap": 110.1, "last_lap": 110.3}),
        session_type=6,
    )
    assert not [c for c in calls if c.kind == "QUALI_LAP"]


def test_rear_snap_needs_braking_lock_and_rotation_the_corner_does_not_explain():
    detector = RearSnapDetector()
    # 120 km/h, 15 m/s2 lateral -> the corner explains 0.45 rad/s of rotation
    steady = replace(
        frame(0.0),
        speed_kmh=120.0,
        brake=0.5,
        steering=0.2,
        accel_lat=15.0,
        yaw_rate=0.46,
    )
    snapping = replace(steady, yaw_rate=1.2)
    assert detector.is_triggered(steady) is False
    assert detector.is_triggered(snapping) is True
    assert detector.is_triggered(replace(snapping, steering=None)) is False  # old tapes
    assert detector.is_triggered(replace(snapping, brake=0.0)) is False  # not braking


def test_points_too_far_apart_are_different_moments_and_are_not_compared():
    # Porsche Curves, 24 Sep: 240 m between power-on points became "51 car lengths"
    mine = {"brake_onset": 11400.0, "min_speed": 170.0, "throttle_on": 12245.0}
    theirs = {"brake_onset": 11400.0, "min_speed": 170.0, "throttle_on": 12005.0}
    assert what_to_change(mine, theirs, 15.0) is None
