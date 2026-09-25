"""The race model (25 Sep 2026): one picture of the race, measured on the road."""
from dataclasses import replace

from race_model import RaceModel, ME
from test_seats import race
from test_racecraft import rival

LAP = 1000.0


def drive(model, seconds, cars, my_speed=50.0, my_start=0.0, step=0.2):
    """cars: {id: (start metres, m/s)}; I start at my_start. Every car keeps its speed."""
    for i in range(int(seconds / step) + 1):
        t = i * step
        opponents = []
        for place, (cid, (start, speed)) in enumerate(sorted(cars.items()), start=1):
            d = start + speed * t
            opponents.append(replace(rival(place, 0.0, car_id=cid), laps=int(d // LAP), lap_dist=d % LAP,
                                     speed_kmh=speed * 3.6))
        me = my_start + my_speed * t
        snap = race(t, {"lap_length": LAP}, {"laps": int(me // LAP), "place": 9}, opponents=opponents)
        model.see_race(snap, t)
        model.see_me(me % LAP, t)
    return model


def test_the_gap_is_measured_where_both_cars_passed():
    model = drive(RaceModel(), 60, {3: (100.0, 50.0)})            # 100 m ahead at the same speed
    assert abs(model.gap(3, ME) - 2.0) < 0.05


def test_a_trend_is_sure_only_with_two_laps_of_road():
    model = drive(RaceModel(), 15, {3: (200.0, 48.0)})            # under a lap driven: no trend
    assert model.trend(3, ME) is None
    model = drive(RaceModel(), 25, {3: (200.0, 48.0)})             # one lap: a trend, NOT sure
    assert model.trend(3, ME)["sure"] is False
    model = drive(RaceModel(), 50, {3: (200.0, 48.0)})             # ~2 laps of history
    t = model.trend(3, ME)
    assert t is not None and t["closing_per_lap"] > 0 and t["sure"]


def test_a_catch_is_forecast_only_when_the_trend_is_sure_and_closing():
    model = drive(RaceModel(), 50, {3: (300.0, 48.0)})
    found = model.catch(ME, 3)
    assert found is not None and found[1] > 0
    assert model.catch(3, ME) is None                             # it is not catching me


def test_a_battle_needs_a_second_or_less_held_for_eight_seconds():
    model = drive(RaceModel(), 30, {3: (20.0, 50.0), 4: (400.0, 50.0)})
    battles = model.battles()
    assert any(front == 3 and back == ME for front, back, _ in battles)
    assert not any(4 in (front, back) for front, back, _ in battles)


def test_corner_gains_come_from_the_time_through_the_corner():
    model = drive(RaceModel(), 80, {3: (100.0, 50.0)}, my_speed=50.0)
    corner = [{"name": "T1", "start": 200.0, "end": 400.0}]
    gains = model.corner_gains(ME, 3, corner)
    assert abs(gains["T1"]) < 0.05                                # same speed: nobody gains
