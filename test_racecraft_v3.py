"""v3 racecraft (24 Sep 2026): real gaps, the closing alarm, the pass lifecycle and praise."""
from dataclasses import replace

from gaps import TrackClock, Trail
from live_telemetry import Event
from radio import Governor
from seats.performance import PerformanceEngineer
from seats.racecraft import Racecraft
from test_racecraft import step, rival, CORNERS
from test_seats import kinds

LAP = 5800.0


# ---- the same-point gap ----------------------------------------------------------------------

def trail(start_time, speed, until_s, step_s=0.2, start_distance=0.0):
    t = Trail()
    time = start_time
    while time <= start_time + until_s:
        t.add(start_distance + (time - start_time) * speed, time)
        time += step_s
    return t


def test_the_gap_is_when_both_cars_passed_the_same_point():
    clock = TrackClock()
    clock.lap_length = LAP
    clock.mine = trail(0.0, 50.0, 60.0)
    clock.theirs[9] = trail(1.0, 50.0, 59.0)          # the same car, a second later
    assert abs(clock.gap_behind(9, 60.0) - 1.0) < 0.01
    clock.theirs[4] = trail(-0.6, 50.0, 60.6)         # a car 0.6 s up the road
    assert abs(clock.gap_ahead(4, 60.0) - 0.6) < 0.01


def test_closing_rate_is_the_slope_of_the_gap():
    clock = TrackClock()
    for i in range(51):
        t = i * 0.2
        clock.remember_gap(9, 1.5 - 0.05 * t, t)       # 0.05 s of gap gone every second
    assert abs(clock.closing_rate(9) - 0.05) < 0.001


def test_the_catch_point_keeps_the_shape_of_the_lap():
    clock = TrackClock()
    clock.lap_length = LAP
    corners = [{"name": "A", "start": 1000.0, "end": 1300.0}, {"name": "B", "start": 1950.0, "end": 2300.0},
               {"name": "C", "start": 5000.0, "end": 5300.0}]
    # last lap: 1.0 s everywhere, except it got to 0.5 s through B (it is quicker there)
    for step in range(0, 116):
        point = step * 50.0 + 1.0
        gap = 0.5 if 1950 <= point <= 2250 else 1.0
        clock.remember_gap(9, gap, step, point)
    # now, one lap on at 1000 m: 0.8 s, so it closed 0.2 s in a lap at this point
    found = clock.catch_point(9, LAP + 1000.0 + 1.0, 0.8)
    assert found is not None
    catch_at, per_lap = found
    assert abs(per_lap - 0.2) < 1e-9
    assert clock.corner_at_or_after(catch_at, corners) == "B"            # 0.5 - 0.2 = 0.3: on him in B
    assert clock.catch_point(9, LAP + 1000.0 + 1.0, 1.1) is None         # it lost time: no catch


# ---- the seat --------------------------------------------------------------------------------

def seat_with_gaps(behind=None, ahead=None):
    seat = Racecraft(PerformanceEngineer())
    seat.clock.gap_behind = lambda car_id, now: behind
    seat.clock.gap_ahead = lambda car_id, now: ahead
    seat.clock.lap_length = LAP
    seat.clock.theirs[9] = Trail()
    seat.clock.theirs[9].add(1000.0, 0.0)          # where the car behind is
    return seat


def test_a_car_closing_fast_sets_off_the_alarm_in_the_spotter_voice():
    seat = seat_with_gaps()
    seat.clock.closing_rate = lambda car_id: 0.06
    seat.clock.gap_behind = lambda car_id, now: 2.6
    step(seat, 100.0, opponents=[rival(6, 8.9, car_id=9)])                     # too far back yet
    seat.clock.gap_behind = lambda car_id, now: 0.9
    calls = step(seat, 100.2, opponents=[rival(6, 8.9, car_id=9)])
    alarm = [c for c in calls if c.kind == "CLOSING_ALARM"]
    assert alarm and alarm[0].voice == "spotter" and alarm[0].immediate
    assert alarm[0].template == "Car behind, 9 tenths, closing fast."
    assert alarm[0].facts["predicted_catch_s"] == 10.0          # logged to be scored, not said
    assert step(seat, 100.4, opponents=[rival(6, 8.9, car_id=9)]) == []      # once per approach


def test_the_alarm_says_if_that_car_has_hit_you():
    seat = seat_with_gaps(behind=1.0)
    seat.clock.closing_rate = lambda car_id: 0.06
    seat.reputation.hits_on_me["111"] = 1
    calls = step(seat, 100.0, opponents=[rival(6, 9.0, car_id=9)])
    alarm = [c for c in calls if c.kind == "CLOSING_ALARM"][0]
    assert "It's already hit you once." in alarm.template


def pass_sequence(seat, gap_after=0.3):
    step(seat, 100.0, lap=3, opponents=[rival(4, 7.7, car_id=5)])                 # it's ahead
    seat.clock.gap_behind = lambda car_id, now: gap_after
    through = dict(me_changes={"place": 4, "time_behind_leader": 7.6}, opponents=[rival(5, 7.9, car_id=5)],
                   lap_dist=4000.0)
    assert step(seat, 100.2, lap=3, **through) == []                               # through, not yet held
    return step(seat, 101.3, lap=3, **through)                                     # a second later, it counts


def test_side_by_side_swaps_are_not_passes():
    seat = seat_with_gaps(behind=0.2)
    for t in (100.0, 100.4, 100.8, 101.2, 101.6):
        place = 5 if int(t * 10) % 8 == 0 else 4
        other = 4 if place == 5 else 5
        assert step(seat, t, lap=3, me_changes={"place": place, "time_behind_leader": 7.6},
                    opponents=[rival(other, 7.7, car_id=5)]) == []
    assert seat.open_passes == {}


def test_a_pass_is_not_praised_until_it_is_held_then_it_is():
    seat = seat_with_gaps()
    calls = pass_sequence(seat)
    assert "PASS_PRAISE" not in kinds(calls)
    assert "STICK_IT" in kinds(calls)                                              # it's in my tow
    stick = [c for c in calls if c.kind == "STICK_IT"][0]
    assert "T11 Parabolica" in stick.template
    after = dict(me_changes={"place": 4, "time_behind_leader": 7.6}, opponents=[rival(5, 7.9, car_id=5)])
    step(seat, 110.0, lap=3, new_race=False, corner="T11 Parabolica", **after)     # the braking zone
    calls = step(seat, 115.0, lap=3, corner=None, **after)                         # through it, still ahead
    praise = [c for c in calls if c.kind == "PASS_PRAISE"]
    assert praise and not praise[0].template.startswith("Clear.")         # the spotter's word, not Max's
    assert "fucking" in praise[0].template.lower() or "lovely" in praise[0].template.lower()


def test_a_pass_that_breaks_the_tow_is_clear_at_once():
    seat = seat_with_gaps()
    pass_sequence(seat, gap_after=0.3)
    seat.clock.gap_behind = lambda car_id, now: 1.2
    calls = step(seat, 104.0, lap=3, me_changes={"place": 4, "time_behind_leader": 7.6},
                 opponents=[rival(5, 8.8, car_id=5)])
    assert "PASS_PRAISE" in kinds(calls)


def test_taken_back_before_it_is_held_means_go_again_not_praise():
    seat = seat_with_gaps()
    pass_sequence(seat)
    step(seat, 103.0, lap=3, opponents=[rival(4, 7.7, car_id=5)])                  # it's back ahead
    calls = step(seat, 104.1, lap=3, opponents=[rival(4, 7.7, car_id=5)])
    assert kinds(calls) == ["PASS_RETAKEN"]
    assert calls[0].template.startswith("They're back past. Go again.")


def test_a_car_that_pitted_is_a_gift_not_a_move():
    seat = seat_with_gaps()
    step(seat, 100.0, lap=3, opponents=[rival(4, 7.7, car_id=5)])
    pitting = replace(rival(5, 7.9, car_id=5), pit_state=2)
    step(seat, 100.2, lap=3, me_changes={"place": 4, "time_behind_leader": 7.6}, opponents=[pitting])
    calls = step(seat, 101.3, lap=3, me_changes={"place": 4, "time_behind_leader": 7.6}, opponents=[pitting])
    assert kinds(calls) == ["PLACE_GIFT"]
    assert calls[0].template == "P4. Car ahead's pitting."
    assert seat.open_passes == {}


def test_contact_during_the_pass_means_no_praise():
    seat = seat_with_gaps()
    pass_sequence(seat)
    hit = Event(kind="CONTACT", sim_time=101.0, speed_kmh=90.0, corner="T11 Parabolica", other_car="111")
    step(seat, 101.0, lap=3, new_race=False, events=[hit], me_changes={"place": 4, "time_behind_leader": 7.6})
    seat.clock.gap_behind = lambda car_id, now: 1.5
    calls = step(seat, 104.0, lap=3, me_changes={"place": 4, "time_behind_leader": 7.6},
                 opponents=[rival(5, 9.1, car_id=5)])
    assert "PASS_PRAISE" not in kinds(calls)


def test_late_on_the_brakes_is_a_brilliant_move():
    seat = seat_with_gaps()
    step(seat, 100.0, lap=3, opponents=[rival(4, 7.7, car_id=5)])
    seat.clock.gap_behind = lambda car_id, now: 1.3
    snapshot_changes = dict(me_changes={"place": 4, "time_behind_leader": 7.6}, opponents=[rival(5, 9.0, car_id=5)])
    from test_seats import moment, race
    m = replace(moment(100.2, race(100.2, **{"me_changes": snapshot_changes["me_changes"],
                                            "opponents": snapshot_changes["opponents"]}), lap=3),
                corners=CORNERS)
    m.frame.brake = 0.7
    seat.update(m)                             # the order flips on the brakes
    calls = step(seat, 101.3, lap=3, **snapshot_changes)     # held, and already out of its tow
    praise = [c for c in calls if c.kind == "PASS_PRAISE"][0]
    assert "Late on the brakes." in praise.template
    assert praise.facts["move"] == "late_brake"


def test_defending_that_held_gets_praise():
    seat = seat_with_gaps(behind=0.4)
    step(seat, 100.0, opponents=[rival(6, 8.4, car_id=9)])
    seat.clock.gap_behind = lambda car_id, now: 1.8
    calls = step(seat, 175.0, opponents=[rival(6, 9.8, car_id=9)])
    assert "DEFEND_HELD" in kinds(calls)


def test_praise_and_the_alarm_go_out_in_the_start_chaos():
    # his call, 25 Sep (was: praise held): a 3-car pass at the start deserves the praise
    governor = Governor()
    governor.settled = False
    seat = seat_with_gaps()
    praise = seat.praise("late_brake", 10.0)
    alarm = seat.instant("CLOSING_ALARM", "Car behind.", 10.0, seat="spotter", voice="spotter")
    assert governor.offer(praise)
    assert governor.offer(alarm)


def test_no_racecraft_after_a_spin():
    # live 25 Sep: spun at Indianapolis, and racecraft kept saying "mega defending"
    from test_racecraft import step, rival
    from live_telemetry import Event
    seat = seat_with_gaps()
    spin = Event(kind="SPIN", sim_time=10.0, speed_kmh=50.0, corner="Indianapolis", conclusion="spin")
    assert step(seat, 10.0, opponents=[rival(4, 7.4)], events=[spin]) == []
    assert step(seat, 25.0, opponents=[rival(4, 7.4)]) == []            # still quiet 15 s later
