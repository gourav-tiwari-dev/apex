from dataclasses import replace

from live_telemetry import CarState
from race_state import Session, Me, Opponent, RaceSnapshot, NearCar, NearCars
from seats import Moment
from seats.spotter import Spotter
from seats.race_engineer import RaceEngineer
from seats.strategist import Strategist

FACING_STRAIGHT = [1, 0, 0, 0, 1, 0, 0, 0, 1]      # car frame = world frame


def frame(t, pos=(0.0, 0.0, 0.0)):
    return CarState(speed_kmh=200.0, throttle=1.0, brake=0.0, gear=5, rpm=0, max_rpm=0,
                    lap_dist=100.0, lap_invalidated=False, wheel_rot=[0] * 4, accel_long=0.0,
                    accel_lat=0.0, surface=[0] * 4, yaw_rate=0.0, elapsed_time=t,
                    pos=list(pos), ori=FACING_STRAIGHT)


def near(t, *positions):
    if not positions:
        return None
    cars = [NearCar(id=i, x=x, y=0.0, z=z, speed_kmh=200.0, brake=0.0, throttle=1.0, steering=0.0)
            for i, (x, z) in enumerate(positions)]
    return NearCars(sim_time=t, cars=cars)


def session(**changes):
    base = Session(track="Monza", session=10, game_phase=5, time_remaining=900.0, end_time=0.0,
                   max_laps=2147483647, yellow_flag_state=0, sector_flags=[0, 0, 0], start_light=0,
                   red_lights=0, raining=0.0, ambient_temp=25.0, track_temp=35.0, wetness=0.0,
                   grip_level=2, fixed_setup=True, limit_steps_per_penalty=6, in_realtime=True)
    return replace(base, **changes)


def me(**changes):
    base = Me(driver="Gourav", car_class="GT3", place=5, grid=7, laps=3, time_behind_next=1.2,
              time_behind_leader=8.0, laps_behind_leader=0, best_lap=110.0, last_lap=110.5,
              sector=1, cur_sector1=0.0, cur_sector2=0.0, pitstops=0, penalties=0, in_pits=False,
              pit_state=0, finish_status=0, flag=0, under_yellow=False, count_lap_flag=2,
              fuel=54.0, fuel_capacity=100.0, virtual_energy=0.0, battery=0.0, lift_and_coast=0,
              track_limit_steps=0, gap_car_ahead=1.2, gap_car_behind=0.8, gap_place_ahead=1.2,
              gap_place_behind=0.8, tyre_temps=[[90.0, 90.0, 90.0]] * 4, tyre_pressures=[180.0] * 4,
              tyre_wear=[0.0] * 4, brake_temps=[400.0] * 4, compound="Medium", dents=[0] * 8,
              detached=False, overheating=False, brake_bias_rear=0.45, tc=3, abs=3, motor_map=1,
              arb_front=3, arb_rear=3, car_model="BMW M4 GT3")
    return replace(base, **changes)


def behind_car(gap):
    return Opponent(id=9, driver="Bob", steam_id=765, car_class="GT3", car_name="Porsche",
                    place=6, laps=3, lap_dist=90.0, time_behind_next=gap, time_behind_leader=9.0,
                    laps_behind_leader=0, best_lap=111.0, last_lap=111.0, in_pits=False,
                    pit_state=0, pitstops=0, finish_status=0, control=2, flag=0)


def race(t, session_changes=None, me_changes=None, opponents=None):
    return RaceSnapshot(sim_time=t, session=session(**(session_changes or {})),
                        me=me(**(me_changes or {})), opponents=opponents or [])


def moment(t, race_state=None, new_race=True, nearby=None, lap=3, wrapped=False, corner=None):
    return Moment(frame=frame(t), race=race_state, new_race=new_race, near=nearby,
                  lap_count=lap, lap_wrapped=wrapped, corner=corner, track="Monza")


def kinds(calls):
    return [c.kind for c in calls]


# ---- spotter ------------------------------------------------------------------------------

def test_spotter_calls_a_car_alongside_and_then_clear():
    spotter = Spotter(side_calls=True)
    assert kinds(spotter.update(moment(0.0, nearby=near(0.0, (3.0, 1.0))))) == ["CAR_LEFT"]
    assert spotter.update(moment(1.1, nearby=near(1.1, (3.0, -1.0)))) == []   # still alongside
    assert spotter.update(moment(1.2)) == []                                  # gone, wait...
    assert kinds(spotter.update(moment(1.7))) == ["CLEAR"]                    # ...then clear


def test_a_car_that_only_brushed_past_gets_no_clear():
    # 24 Sep: 17 of 34 spotter lines were "Clear", mostly after cars that just went by
    spotter = Spotter(side_calls=True)
    assert kinds(spotter.update(moment(0.0, nearby=near(0.0, (3.0, 1.0))))) == ["CAR_LEFT"]
    assert spotter.update(moment(0.3)) == []
    assert spotter.update(moment(1.0)) == []                                  # no "clear"


def test_still_there_backs_off_while_alongside():
    spotter = Spotter(side_calls=True)
    said = []
    for step in range(0, 450):
        t = step * 0.1
        said += [(c.kind, round(c.sim_time)) for c in spotter.update(moment(t, nearby=near(t, (3.0, 0.0))))]
    assert said == [("CAR_LEFT", 0), ("STILL_THERE", 4), ("STILL_THERE", 12), ("STILL_THERE", 28), ("STILL_THERE", 44)]


def test_a_car_flickering_at_the_edge_does_not_repeat_car_left():
    spotter = Spotter()
    spotter.update(moment(0.0, nearby=near(0.0, (3.0, 0.0))))
    spotter.update(moment(0.1))                                     # drops out for a moment
    assert spotter.update(moment(0.2, nearby=near(0.2, (3.0, 0.0)))) == []


def test_three_wide_and_right_side():
    spotter = Spotter(side_calls=True)
    assert kinds(spotter.update(moment(0.0, nearby=near(0.0, (-3.0, 0.5))))) == ["CAR_RIGHT"]
    assert kinds(spotter.update(moment(0.1, nearby=near(0.1, (-3.0, 0.5), (3.0, 0.0))))) == ["THREE_WIDE"]


def test_a_car_directly_behind_is_not_alongside():
    spotter = Spotter()
    assert spotter.update(moment(0.0, nearby=near(0.0, (0.3, 3.0)))) == []   # in line, not beside
    assert spotter.update(moment(0.1, nearby=near(0.1, (3.0, 8.0)))) == []   # beside but a car length back


def test_spotter_is_silent_on_old_tapes():
    spotter = Spotter()
    old = Moment(frame=replace(frame(0.0), pos=None, ori=None), race=None, new_race=False,
                 near=near(0.0, (3.0, 0.0)), lap_count=1, lap_wrapped=False, corner=None, track=None)
    assert spotter.update(old) == []


# ---- race engineer -------------------------------------------------------------------------

def test_formation_then_lights_out():
    engineer = RaceEngineer()
    assert kinds(engineer.update(moment(0.0, race(0.0, {"game_phase": 3})))) == ["FORMATION"]
    assert engineer.update(moment(1.0, race(1.0, {"game_phase": 4}))) == []
    calls = engineer.update(moment(2.0, race(2.0, {"game_phase": 5})))
    assert kinds(calls) == ["LIGHTS_OUT"]
    assert calls[0].urgent


def test_safety_car_then_green():
    engineer = RaceEngineer()
    engineer.update(moment(0.0, race(0.0)))
    assert kinds(engineer.update(moment(1.0, race(1.0, {"game_phase": 6})))) == ["SAFETY_CAR"]
    assert kinds(engineer.update(moment(2.0, race(2.0, {"game_phase": 5})))) == ["GREEN"]


def test_blue_flag_penalty_and_track_limits():
    engineer = RaceEngineer()
    engineer.update(moment(0.0, race(0.0)))
    calls = engineer.update(moment(1.0, race(1.0, me_changes={"flag": 6, "penalties": 1, "track_limit_steps": 2}),
                                   corner="T4 Roggia"))
    assert kinds(calls) == ["BLUE_FLAG", "PENALTY", "TRACK_LIMITS"]
    limits = calls[2]
    assert limits.facts == {"corner": "T4 Roggia", "steps": 2, "penalty_at": 6}


def test_gap_report_every_three_laps_with_its_numbers_as_facts():
    engineer = RaceEngineer()
    engineer.update(moment(0.0, race(0.0)))
    snapshot = race(1.0, opponents=[behind_car(0.8)])
    calls = engineer.update(moment(1.0, snapshot, lap=3, wrapped=True))
    assert kinds(calls) == ["GAP_REPORT"]
    assert calls[0].facts == {"place": 5, "gap_ahead_s": 1.2, "gap_behind_s": 0.8}
    assert engineer.update(moment(2.0, snapshot, lap=4, wrapped=True)) == []    # not every lap


def test_damage_after_a_hit():
    engineer = RaceEngineer()
    engineer.update(moment(0.0, race(0.0)))
    dented = [0, 1, 0, 0, 0, 0, 0, 0]
    assert kinds(engineer.update(moment(1.0, race(1.0, me_changes={"dents": dented})))) == ["DAMAGE"]


# ---- strategist ---------------------------------------------------------------------------

def run_laps(strategist, fuel_readings, max_laps=20):
    calls = []
    for lap, fuel in enumerate(fuel_readings, start=1):
        snapshot = race(lap * 110.0, {"max_laps": max_laps}, {"fuel": fuel, "laps": lap})
        calls += strategist.update(moment(lap * 110.0, snapshot, lap=lap, wrapped=True))
    return calls


def test_no_fuel_call_before_two_measured_laps():
    assert run_laps(Strategist(), [60.0, 57.0]) == []


def test_fuel_fine_to_the_flag():
    calls = run_laps(Strategist(), [60.0, 57.0, 54.0], max_laps=20)
    assert kinds(calls) == ["FUEL"]
    # 54 litres / 3 per lap = 18 laps, 17 left -> 1.0 lap spare
    assert calls[0].facts["spare_laps"] == 1.0
    assert "Push" in calls[0].conclusion


def test_fuel_short_says_how_much_to_save():
    calls = run_laps(Strategist(), [30.0, 27.0, 24.0], max_laps=20)
    fuel = calls[0]
    assert fuel.facts["spare_laps"] == 9.0         # 8 laps of fuel, 17 to go
    assert fuel.facts["save_per_lap"] > 0
    assert "short" in fuel.conclusion


def test_fuel_speaks_again_only_when_the_picture_changes():
    strategist = Strategist()
    first = run_laps(strategist, [60.0, 57.0, 54.0, 51.0, 48.0, 45.0], max_laps=20)
    assert kinds(first) == ["FUEL"]


def test_last_lap_call():
    strategist = Strategist()
    snapshot = race(10.0, {"max_laps": 10}, {"laps": 9})
    assert strategist.update(moment(10.0, snapshot)) == []                 # mid-lap: not counted
    assert kinds(strategist.update(moment(11.0, snapshot, lap=9, wrapped=True))) == ["LAST_LAP"]



def test_side_by_side_calls_are_off_by_default():
    # his call, 25 Sep: "I know who is on my right or left", and they cut every engineer line
    assert Spotter().update(moment(0.0, nearby=near(0.0, (3.0, 1.0)))) == []
