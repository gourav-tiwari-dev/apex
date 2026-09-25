"""The bugs from the full live test at Le Mans (24 Sep 2026, evening), pinned."""
import gzip
import json
import os
from dataclasses import replace

from seats.strategist import measured_lap, Strategist
from seats.racecraft import Racecraft
from seats.performance import PerformanceEngineer
from test_seats import moment, race
from test_agent import snapshot_at_lap_4


def test_an_invalid_lap_is_timed_by_apex_itself():
    # the game posted -1 for laps 1-4, and fuel and pace stayed "unknown" all race
    assert measured_lap(-1.0, 1000.0, 1243.6) == 243.6
    assert measured_lap(240.3, 1000.0, 1243.6) == 240.3            # the game's time when it has one
    assert measured_lap(-1.0, None, 1243.6) is None                # no line crossed yet
    assert measured_lap(-1.0, 1000.0, 1005.0) is None              # 5 s is not a lap


def test_fuel_is_known_after_three_lines_even_when_every_lap_is_invalid():
    strategist = Strategist()
    for lap, fuel in enumerate((43.7, 38.6, 30.8, 23.2), start=1):
        now = 100.0 + lap * 243.0
        snapshot = race(now, {"time_remaining": 1200.0 - lap * 243.0}, {"fuel": fuel, "laps": lap, "last_lap": -1.0})
        strategist.update(moment(now, snapshot, lap=lap, wrapped=True))
    assert strategist.fuel_now is not None


def test_losing_places_in_the_lap_1_shuffle_is_not_called_and_later_only_once_a_minute():
    from test_seats import behind_car
    seat = Racecraft(PerformanceEngineer())
    places = [(1.0, 10, 1), (2.0, 11, 1), (3.0, 12, 1),          # lap 1: the start shuffle
              (300.0, 12, 2), (301.0, 13, 2), (330.0, 14, 2), (365.0, 15, 2)]
    said = []
    for now, place, lap in places:
        # cars 1..20 keep their order; I sit after the first place-1 of them
        cars = [replace(behind_car(0.5), id=car, place=car if car < place else car + 1) for car in range(1, 21)]
        snapshot = race(now, me_changes={"place": place, "time_behind_leader": 8.0}, opponents=cars)
        said += [c.kind for c in seat.update(replace(moment(now, snapshot, lap=lap), session_type=10))]
    assert said.count("PASSED") == 2          # 301 s, then not at 330 s, then 365 s


def test_a_misheard_corner_is_still_found():
    snapshot = snapshot_at_lap_4()
    snapshot.corners = {"ford chicanes": {"corner": "Ford Chicanes", "laps_measured": 3}}
    found = snapshot.run_tool("corner", {"name": "Four Chickens"})
    assert found["corner"] == "Ford Chicanes" and found["heard_as"] == "four chickens"


def test_a_tape_cut_off_mid_write_replays_up_to_the_cut(tmp_path):
    from live_telemetry import ReplaySource
    frame = {"speed_kmh": 100.0, "throttle": 1.0, "brake": 0.0, "gear": 4, "rpm": 7000, "max_rpm": 8000,
             "lap_dist": 10.0, "lap_invalidated": False, "wheel_rot": [0, 0, 0, 0], "accel_long": 0.0,
             "accel_lat": 0.0, "surface": [0, 0, 0, 0], "yaw_rate": 0.0, "elapsed_time": 1.0}
    whole = tmp_path / "whole.jsonl.gz"
    with gzip.open(whole, "wt") as f:
        for i in range(2000):
            f.write(json.dumps(dict(frame, elapsed_time=i / 60)) + "\n")
    data = whole.read_bytes()
    cut = tmp_path / "cut.jsonl.gz"
    cut.write_bytes(data[: len(data) - 40])                 # the end never got written
    frames = list(ReplaySource(None, str(cut)))
    assert 0 < len(frames) < 2000


def test_the_voice_that_spoke_is_known():
    from voice import Voice
    silent = Voice(out_loud=False, clone=False)
    assert silent.render_with_engine("Car left!") == (None, None)
