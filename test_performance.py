from dataclasses import replace

from live_telemetry import CornerStat, RearSnapDetector
from race_state import Opponent
from seats.performance import PerformanceEngineer, OpponentCorners
from test_seats import frame, moment, race, kinds

CORNERS = [{"name": "T8 Ascari", "start": 3795, "end": 4318}]


def finish_corner(seat, lap, speed, t, corner="T8 Ascari"):
    stat = CornerStat(lap_count=lap, corner=corner, brake_onset=3800.0, min_speed=speed,
                      slow_zone=50.0, coast=0.0)
    return seat.update(replace(moment(t), corner_stat=stat))


def cross_line(seat, t, lap, race_state=None, session_type=10):
    return seat.update(replace(moment(t, race_state, lap=lap, wrapped=True), session_type=session_type))


def test_one_corner_call_per_lap_for_the_biggest_loss_only():
    seat = PerformanceEngineer()
    finish_corner(seat, 1, 130.0, 1.0)                          # sets my best
    finish_corner(seat, 1, 150.0, 2.0, corner="T11 Parabolica")
    cross_line(seat, 3.0, 2)
    finish_corner(seat, 2, 124.0, 4.0)                          # 6 down at Ascari
    finish_corner(seat, 2, 145.0, 5.0, corner="T11 Parabolica")  # 5 down at Parabolica
    calls = cross_line(seat, 6.0, 3)
    assert kinds(calls) == ["CORNER_LOSS"]
    assert calls[0].facts["corner"] == "T8 Ascari"
    assert calls[0].facts["loss_kmh"] == 6.0


def test_a_loss_inside_the_noise_is_not_said():
    seat = PerformanceEngineer()
    finish_corner(seat, 1, 130.0, 1.0)
    cross_line(seat, 2.0, 2)
    finish_corner(seat, 2, 127.0, 3.0)       # 3 km/h: inside his 1-5 km/h spread
    assert cross_line(seat, 4.0, 3) == []


def opponent(lap_dist, speed, laps, driver="Ann", car_class="GT3"):
    return Opponent(id=5, driver=driver, steam_id=111, car_class=car_class, car_name="Ferrari",
                    place=1, laps=laps, lap_dist=lap_dist, time_behind_next=0.0, time_behind_leader=0.0,
                    laps_behind_leader=0, best_lap=108.0, last_lap=108.0, in_pits=False, pit_state=0,
                    pitstops=0, finish_status=0, control=2, flag=0, speed_kmh=speed)


def test_fastest_car_reference_needs_two_of_its_laps():
    tracker = OpponentCorners()
    for lap, speeds in ((1, [150.0, 131.0, 140.0]), (2, [150.0, 132.0, 141.0])):
        for distance, speed in zip((3800.0, 4000.0, 4200.0), speeds):
            tracker.update(race(0.0, opponents=[opponent(distance, speed, lap)]), CORNERS, "GT3")
        tracker.update(race(0.0, opponents=[opponent(4500.0, 200.0, lap)]), CORNERS, "GT3")
    assert tracker.fastest_through("T8 Ascari") == ("Ann", 131.5)


def test_other_classes_are_not_a_reference():
    tracker = OpponentCorners()
    tracker.update(race(0.0, opponents=[opponent(4000.0, 170.0, 1, car_class="Hypercar")]), CORNERS, "GT3")
    tracker.update(race(0.0, opponents=[opponent(4500.0, 250.0, 1, car_class="Hypercar")]), CORNERS, "GT3")
    assert tracker.rows == []


def test_praise_for_a_new_best_lap_at_most_twice():
    seat = PerformanceEngineer()
    calls = []
    for lap, best in ((2, 111.0), (3, 110.5), (4, 110.2), (5, 110.0)):
        calls += cross_line(seat, lap * 111.0, lap, race(0.0, me_changes={"best_lap": best}))
    praise = [c for c in calls if c.kind == "PRAISE"]
    assert len(praise) == 2
    assert praise[0].facts == {"gain_s": 0.5}


def test_qualifying_lap_report_carries_its_own_numbers():
    seat = PerformanceEngineer()
    calls = cross_line(seat, 111.0, 2, race(0.0, me_changes={"best_lap": 110.1, "last_lap": 110.3}),
                       session_type=6)
    quali = [c for c in calls if c.kind == "QUALI_LAP"][0]
    assert quali.template == "1:50.3. 0.2 off your best."
    assert quali.facts == {"minutes": 1, "seconds": 50.3, "delta_s": 0.2}


def test_rear_snap_needs_braking_lock_and_rotation_the_corner_does_not_explain():
    detector = RearSnapDetector()
    # 120 km/h, 15 m/s2 lateral -> the corner explains 0.45 rad/s of rotation
    steady = replace(frame(0.0), speed_kmh=120.0, brake=0.5, steering=0.2, accel_lat=15.0, yaw_rate=0.46)
    snapping = replace(steady, yaw_rate=1.2)
    assert detector.is_triggered(steady) is False
    assert detector.is_triggered(snapping) is True
    assert detector.is_triggered(replace(snapping, steering=None)) is False    # old tapes
    assert detector.is_triggered(replace(snapping, brake=0.0)) is False        # not braking
