"""v3 step 4 (24 Sep 2026): what is happening up the road and in the mirrors."""
from dataclasses import replace

from seats.track_awareness import TrackAwareness
from test_racecraft import rival, CORNERS
from test_seats import moment, race, kinds


def look(seat, t, cars, my_lap_dist=1000.0, my_place=8, my_behind_leader=8.0, speed=250.0):
    snapshot = race(t, me_changes={"place": my_place, "time_behind_leader": my_behind_leader}, opponents=cars)
    m = replace(moment(t, snapshot), corners=CORNERS, session_type=10)
    m.frame.lap_dist = my_lap_dist
    m.frame.speed_kmh = speed
    return seat.update(m)


def car(car_id, lap_dist, speed=250.0, place=20, behind_leader=30.0, car_class="GT3"):
    return replace(rival(place, behind_leader, car_id=car_id, car_class=car_class, steam_id=car_id),
                   lap_dist=lap_dist, speed_kmh=speed)


def seat():
    s = TrackAwareness()
    s.lap_length = 5800.0
    return s


def test_a_stopped_car_up_the_road_is_called_by_the_spotter_with_where():
    calls = look(seat(), 1.0, [car(3, 1000.0, speed=5.0)], my_lap_dist=700.0)
    assert kinds(calls) == ["CAR_STOPPED_AHEAD"]
    assert calls[0].template == "Car stopped ahead, T1 Rettifilo."
    calls = look(seat(), 1.0, [car(4, 1200.0, speed=5.0)], my_lap_dist=700.0)
    assert calls[0].template == "Car stopped ahead, before T8 Ascari."
    assert calls[0].voice == "spotter"


def test_a_slow_car_is_called_once_not_every_snapshot():
    s = seat()
    assert kinds(look(s, 1.0, [car(3, 1500.0, speed=100.0)])) == ["SLOW_CAR_AHEAD"]
    assert look(s, 1.2, [car(3, 1510.0, speed=100.0)]) == []


def test_cars_at_racing_speed_ahead_are_not_a_hazard():
    assert look(seat(), 1.0, [car(3, 1200.0, speed=240.0)]) == []


def test_three_wide_ahead_says_stay_out_of_it():
    cars = [car(3, 1200.0), car(4, 1202.0), car(5, 1204.0)]
    calls = look(seat(), 1.0, cars)
    assert kinds(calls) == ["THREE_WIDE_AHEAD"]
    assert calls[0].template == "Three wide ahead. Stay out of it, let them fight."


def test_a_hypercar_closing_behind_gets_one_call():
    s = seat()
    hyper = car(9, 900.0, speed=300.0, car_class="Hypercar")       # 100 m back at 83 m/s: 1.2 s
    calls = look(s, 1.0, [hyper])
    assert kinds(calls) == ["FASTER_CLASS_BEHIND"]
    assert calls[0].template.startswith("Hypercar behind, 1.2 seconds. Hold your line")
    assert look(s, 1.2, [hyper]) == []


def test_two_hypercars_fighting_behind():
    fighters = [car(9, 900.0, speed=300.0, car_class="Hypercar"), car(10, 880.0, speed=300.0, car_class="Hypercar")]
    calls = look(seat(), 1.0, fighters)
    assert kinds(calls) == ["FASTER_FIGHT_BEHIND"]


def test_a_slower_class_behind_is_not_called():
    assert look(seat(), 1.0, [car(9, 900.0, speed=300.0, car_class="GT3")]) == []


def test_a_battle_ahead_is_an_opportunity_for_max_with_positions_not_names():
    s = seat()
    fight = [car(6, 2000.0, place=6, behind_leader=6.3), car(7, 2010.0, place=7, behind_leader=6.6)]
    assert look(s, 1.0, fight) == []                               # not fighting long enough yet
    calls = look(s, 12.0, fight)
    assert kinds(calls) == ["BATTLE_AHEAD"]
    assert calls[0].template.startswith("P6 and P7 are fighting, 1.4 seconds up the road.")
    assert calls[0].voice == "engineer"


def test_a_train_ahead():
    train = [car(5, 2000.0, place=5, behind_leader=5.4), car(6, 2010.0, place=6, behind_leader=6.1),
             car(7, 2020.0, place=7, behind_leader=6.9)]
    calls = look(seat(), 1.0, train)
    assert kinds(calls) == ["TRAIN_AHEAD"]
    assert "3 cars, P5 to P7" in calls[0].template


def test_nothing_in_qualifying():
    s = seat()
    snapshot = race(1.0, opponents=[car(3, 1200.0, speed=5.0)])
    m = replace(moment(1.0, snapshot), corners=CORNERS, session_type=6)
    m.frame.lap_dist = 700.0
    assert s.update(m) == []
