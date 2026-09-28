"""v3 step 4 (24 Sep 2026): what is happening up the road and in the mirrors."""

from dataclasses import replace

from seats.track_awareness import TrackAwareness
from gaps import TrackClock
from test_racecraft import rival, CORNERS
from test_seats import moment, race, kinds


def look(
    seat, t, cars, my_lap_dist=1000.0, my_place=8, my_behind_leader=8.0, speed=250.0
):
    snapshot = race(
        t,
        me_changes={"place": my_place, "time_behind_leader": my_behind_leader},
        opponents=cars,
    )
    m = replace(moment(t, snapshot), corners=CORNERS, session_type=10)
    m.frame.lap_dist = my_lap_dist
    m.frame.speed_kmh = speed
    return seat.update(m)


def car(car_id, lap_dist, speed=250.0, place=20, behind_leader=30.0, car_class="GT3"):
    return replace(
        rival(
            place, behind_leader, car_id=car_id, car_class=car_class, steam_id=car_id
        ),
        lap_dist=lap_dist,
        speed_kmh=speed,
    )


def seat():
    s = TrackAwareness()
    s.lap_length = 5800.0
    s.clock = TrackClock(5800.0)  # a known lap, as the session gives it
    return s


def test_a_stopped_car_up_the_road_is_called_by_the_spotter_with_where():
    calls = look(seat(), 1.0, [car(3, 1000.0, speed=5.0)], my_lap_dist=700.0)
    assert kinds(calls) == ["CAR_STOPPED_AHEAD"]
    assert calls[0].template == "Car stopped ahead, T1 Rettifilo."
    calls = look(seat(), 1.0, [car(4, 1200.0, speed=5.0)], my_lap_dist=700.0)
    assert calls[0].template == "Car stopped ahead, before T8 Ascari."
    assert calls[0].voice == "spotter"


def learn_normal(s, speed=250.0, corner=None):
    """Eight passes of every 50 m at racing speed; corner=(start, end, speed) sets a slow bit."""
    for n in range(8):
        for d in range(0, 5800, 50):
            here = speed
            if corner and corner[0] <= d < corner[1]:
                here = corner[2]
            s.normal.see(100 + n, d + 1.0, here)


def test_a_slow_car_is_called_once_not_every_snapshot():
    s = seat()
    learn_normal(s)
    assert kinds(look(s, 1.0, [car(3, 1500.0, speed=100.0)])) == ["SLOW_CAR_AHEAD"]
    assert look(s, 1.2, [car(3, 1510.0, speed=100.0)]) == []


def test_a_car_braking_for_a_slow_corner_is_not_a_slow_car():
    # 24 Sep replay: every car braking for Mulsanne Corner was "Slow car ahead" (vs MY speed)
    s = seat()
    learn_normal(s, corner=(1400, 1600, 95.0))
    assert look(s, 1.0, [car(3, 1500.0, speed=90.0)], speed=290.0) == []
    assert kinds(look(s, 2.0, [car(4, 1500.0, speed=30.0)], speed=290.0)) == [
        "SLOW_CAR_AHEAD"
    ]


def test_a_slow_car_that_then_stops_is_one_call():
    s = seat()
    learn_normal(s)
    assert kinds(look(s, 1.0, [car(3, 1500.0, speed=60.0)])) == ["SLOW_CAR_AHEAD"]
    assert look(s, 1.6, [car(3, 1502.0, speed=10.0)]) == []


def test_no_slow_car_call_before_the_normal_speed_is_known():
    assert look(seat(), 1.0, [car(3, 1500.0, speed=100.0)]) == []


def test_a_car_parked_in_one_spot_does_not_become_the_normal_there():
    s = seat()
    learn_normal(s)
    for _ in range(100):
        s.normal.see(3, 1501.0, 5.0)
    assert s.normal.at(1501.0) == 250.0


def test_three_wide_ahead_says_stay_out_of_it():
    cars = [car(3, 1200.0), car(4, 1202.0), car(5, 1204.0)]
    calls = look(seat(), 1.0, cars)
    assert kinds(calls) == ["THREE_WIDE_AHEAD"]
    assert calls[0].template == "Three wide ahead. Stay out of it, let them fight."


def test_a_hypercar_closing_behind_gets_one_call():
    s = seat()
    said = []
    for step in range(0, 101):  # 20 s
        t = step * 0.2
        hyper = car(
            9, 1700.0 + 83.3 * t, speed=300.0, car_class="Hypercar"
        )  # 300 m back, 13.9 m/s quicker
        said += look(s, t, [hyper], my_lap_dist=2000.0 + 69.4 * t, speed=250.0)
    assert kinds(said) == ["FASTER_CLASS_BEHIND"]  # once, not again for the same car
    assert said[0].template.startswith("Hypercar behind, closing. On you in about ")


def test_two_cars_stopped_at_one_place_is_one_call():
    # replay of 23 Sep (27 Sep): "Car stopped ahead, before Mulsanne Chicane 2." at 1248.0 and again
    # at 1248.2 for the second car of the same crash; the same at Mulsanne Chicane 1 (1226 / 1227)
    s = seat()
    learn_normal(s)
    first = look(s, 1248.0, [car(3, 1300.0, speed=5.0)])
    both = look(s, 1248.2, [car(3, 1300.0, speed=5.0), car(4, 1310.0, speed=3.0)])
    assert kinds(first) == ["CAR_STOPPED_AHEAD"]
    assert both == []
    later = look(
        s, 1248.0 + 25.0, [car(5, 1305.0, speed=4.0)]
    )  # a new stop there later
    assert kinds(later) == ["CAR_STOPPED_AHEAD"]


def test_three_wide_is_said_once_for_a_pack_not_once_per_trio():
    # replay of 24 Sep (27 Sep): 22 "Three wide ahead" in one race, 18 of them really three wide
    # (4 m+ apart sideways), but in clumps: 5 between 1154 and 1166 s as one pack jostled and made
    # new trios. Once in 20 s is the news; every reshuffle is noise
    s = seat()
    said = []
    trios = [(3, 4, 5), (4, 5, 6), (3, 5, 6), (5, 6, 7)]
    for step, trio in enumerate(trios):
        t = 1154.0 + 3.0 * step
        cars = [car(trio[0], 1200.0), car(trio[1], 1202.0), car(trio[2], 1204.0)]
        said += look(s, t, cars)
    assert kinds(said) == ["THREE_WIDE_AHEAD"]
    later = [car(8, 1200.0), car(9, 1202.0), car(10, 1204.0)]
    assert kinds(look(s, 1154.0 + 25.0, later)) == ["THREE_WIDE_AHEAD"]


def test_three_wide_needs_three_cars_across_the_track():
    # replay of 24 Sep: 4 of the 22 "three wide" had the three only 2.8-3.9 m apart across the track:
    # two alongside and one tucked in behind. Three abreast needs the outside cars 4 m+ apart
    def trio(t, across, lap_start=1200.0):
        cars = []
        for n, car_id in enumerate((3, 4, 5)):
            c = car(car_id, lap_start + 2.0 * n)
            cars.append(
                replace(c, x=across[n], z=1200.0 + 2.0 * n + 50.0 * t)
            )  # all heading +z at 50 m/s
        return cars

    s = seat()
    look(
        s, 10.0, trio(10.0, (0.0, 2.0, 1.0), lap_start=3000.0)
    )  # out of range: only their positions kept
    assert (
        look(s, 10.2, trio(10.2, (0.0, 2.0, 1.0))) == []
    )  # 2 m across: not three wide
    s2 = seat()
    look(s2, 10.0, trio(10.0, (0.0, 2.5, 5.0), lap_start=3000.0))
    assert kinds(look(s2, 10.2, trio(10.2, (0.0, 2.5, 5.0)))) == [
        "THREE_WIDE_AHEAD"
    ]  # 5 m across


def test_a_faster_class_car_is_called_when_it_will_arrive_not_when_it_is_first_near():
    # replay of the 58-car race (27 Sep): "LMP2 behind, 2.5 seconds" three times, and each LMP2
    # took 39-50 s to reach him (it closed ~0.05 s a second). His mark: "LMP do not detected".
    # Now the call comes about 15 s before it arrives, from how fast the gap is really shrinking
    s = seat()
    said = []
    for step in range(0, 201):  # 40 s, 5 snapshots a second
        t = step * 0.2
        me_at = 2000.0 + 60.0 * t  # me at 60 m/s
        lmp2 = car(
            9, 1850.0 + 63.0 * t, speed=63.0 * 3.6, car_class="LMP2"
        )  # 150 m back, 3 m/s quicker
        for call in look(s, t, [lmp2], my_lap_dist=me_at, speed=60.0 * 3.6):
            said.append((round(t, 1), call.kind, call.template))
    assert [kind for _, kind, _ in said] == ["FASTER_CLASS_BEHIND"]
    when, _, words = said[0]
    assert 33.0 <= when <= 37.0  # it arrives at 50 s
    assert words.startswith("LMP2 behind, closing. On you in about 15 seconds.")


def test_a_faster_class_car_that_is_not_closing_is_not_called():
    # the same race, 671.8 s: "LMP2 behind, 1.5 seconds" and it never came by
    s = seat()
    said = []
    for step in range(0, 51):
        t = step * 0.2
        lmp2 = car(
            9, 1900.0 + 60.0 * t, speed=60.0 * 3.6, car_class="LMP2"
        )  # 100 m back, same speed
        said += look(s, t, [lmp2], my_lap_dist=2000.0 + 60.0 * t, speed=60.0 * 3.6)
    assert said == []


def test_two_hypercars_fighting_behind():
    s = seat()
    said = []
    for step in range(0, 101):
        t = step * 0.2
        fighters = [
            car(9, 1700.0 + 83.3 * t, speed=300.0, car_class="Hypercar"),
            car(10, 1680.0 + 83.3 * t, speed=300.0, car_class="Hypercar"),
        ]
        said += look(s, t, fighters, my_lap_dist=2000.0 + 69.4 * t, speed=250.0)
    assert kinds(said) == ["FASTER_FIGHT_BEHIND"]


def test_a_slower_class_behind_is_not_called():
    assert look(seat(), 1.0, [car(9, 900.0, speed=300.0, car_class="GT3")]) == []


MS = 250.0 / 3.6  # everyone at 250 km/h in these runs


def drive(s, seconds, cars_at, my_start=1000.0, my_place=8):
    """Every car and me at 250 km/h for this long, 5 snapshots a second. cars_at(my_distance)
    gives the cars (metres up the road from me set their gaps). Returns every call made."""
    said = []
    for step in range(int(seconds * 5) + 1):
        t = step * 0.2
        mine = my_start + MS * t
        said += look(s, t, cars_at(mine), my_lap_dist=mine, my_place=my_place)
    return said


def group(*spec):
    """spec: (car id, place, seconds up the road from me)."""
    return lambda mine: [
        car(cid, mine + secs * MS, place=place) for cid, place, secs in spec
    ]


def test_a_battle_ahead_is_an_opportunity_for_max_with_positions_not_names():
    s = seat()
    fight = group((7, 7, 2.0), (6, 6, 2.3))
    assert drive(s, 8.0, fight) == []  # not fighting long enough yet
    calls = drive(s, 16.0, fight)
    assert kinds(calls) == ["BATTLE_AHEAD"]
    assert calls[0].template.startswith(
        "P6 and P7 are fighting, 2.0 seconds up the road."
    )
    assert calls[0].voice == "engineer"
    assert not calls[0].immediate  # waits its turn in the talk budget


def test_a_train_ahead():
    calls = drive(seat(), 16.0, group((7, 7, 1.5), (6, 6, 2.2), (5, 5, 3.0)))
    assert kinds(calls) == ["TRAIN_AHEAD"]
    assert "3 cars, P5 to P7, 1.5 seconds up the road" in calls[0].template


def test_a_long_train_is_not_counted_to_the_end():
    # 24 Sep replay: "Train ahead, 18 cars, P6 to P23"
    cars = group(*[(20 - i, 20 - i, 1.5 + 0.6 * i) for i in range(12)])
    calls = drive(seat(), 16.0, cars, my_place=21)
    assert kinds(calls) == ["TRAIN_AHEAD"]
    assert "at least 5 cars from P20 up" in calls[0].template


def test_the_same_train_is_called_once_even_when_cars_join_or_leave_the_front():
    s = seat()
    calls = drive(s, 16.0, group((7, 7, 1.5), (6, 6, 2.2), (5, 5, 3.0)))
    calls += drive(s, 30.0, group((7, 7, 1.5), (6, 6, 2.2), (5, 5, 3.0), (4, 4, 3.8)))
    assert kinds(calls) == ["TRAIN_AHEAD"]


def test_already_in_the_train_is_left_to_racecraft():
    assert drive(seat(), 16.0, group((7, 7, 0.4), (6, 6, 1.0), (5, 5, 1.6))) == []


def test_nothing_in_qualifying():
    s = seat()
    snapshot = race(1.0, opponents=[car(3, 1200.0, speed=5.0)])
    m = replace(moment(1.0, snapshot), corners=CORNERS, session_type=6)
    m.frame.lap_dist = 700.0
    assert s.update(m) == []


def test_a_car_that_jumped_to_the_garage_is_not_a_hazard():
    # live 27 Sep: a car crashed at 2,612 m, "returned to garage" and stood at 89 m among the garages,
    # not flagged in the pits: "Car stopped ahead, before Dunlop Chicane" with nothing on the track
    s = seat()
    look(
        s, 1.0, [car(3, 2612.0, speed=0.0)], my_lap_dist=5000.0
    )  # stopped far up the road
    assert (
        look(s, 3.4, [car(3, 1000.0, speed=0.0)], my_lap_dist=700.0) == []
    )  # then 1.6 km away: jumped
    slowed = seat()
    look(slowed, 1.0, [car(4, 950.0, speed=120.0)], my_lap_dist=500.0)
    calls = look(
        slowed, 1.2, [car(4, 960.0, speed=5.0)], my_lap_dist=700.0
    )  # drove there and stopped
    assert kinds(calls) == ["CAR_STOPPED_AHEAD"]
