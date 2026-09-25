"""Live 25 Sep: 0.6 laps of energy for 1.7 laps of race, and the radio said numbers, then
"no need to pit". Fuel is now measured per metre driven and every answer carries the verdict."""
from dataclasses import replace

from seats.strategist import Strategist, verdict_of, fuel_words
from agent import fuel_honest
from test_seats import moment, race
from test_racecraft import rival


def drive(strategist, energy_start, energy_per_lap, metres, lap_len=13000.0, time_left=256.0, lap_time=242.0):
    calls = []
    for step in range(0, int(metres / 100) + 1):
        d = step * 100.0
        energy = energy_start - energy_per_lap * d / lap_len
        snap = race(1000 + step, {"time_remaining": time_left - step * 100 / 54, "max_laps": 2147483647},
                    {"virtual_energy": energy, "fuel": 50.0, "fuel_capacity": 120.0, "laps": 3,
                     "last_lap": lap_time, "best_lap": lap_time},
                    opponents=[replace(rival(5, 3.0), lap_dist=lap_len - 5)])
        m = moment(1000 + step, snap)
        m.frame.lap_dist = (d % lap_len)
        calls += strategist.update(m)
    return calls


def test_short_on_energy_is_called_box_this_lap_unasked():
    s = Strategist()
    calls = drive(s, energy_start=0.072, energy_per_lap=0.10, metres=3000)
    assert s.fuel_now["verdict"] == "box" and s.fuel_now["limit"] == "energy"
    assert [c.template for c in calls if c.kind == "FUEL"][0].startswith("Box this lap for fuel.")


def test_plenty_of_fuel_says_push_and_never_nags():
    s = Strategist()
    calls = drive(s, energy_start=0.90, energy_per_lap=0.10, metres=3000)
    assert s.fuel_now["verdict"] == "fine" and not [c for c in calls if c.kind == "FUEL"]


def test_the_verdicts():
    assert verdict_of(1.2, 3) == "fine" and verdict_of(0.2, 3) == "tight"
    assert verdict_of(-0.1, 2) == "save" and verdict_of(-1.1, 1.7) == "box"
    assert fuel_words({"verdict": "box", "limit": "energy", "spare_laps": -1.1}).startswith("Box this lap")


def test_the_coach_cannot_say_no_stop_against_the_fuel_maths_or_without_it():
    box = {"verdict": "box", "spare_laps": -1.1, "limit": "energy"}
    assert not fuel_honest("Do I need to pit?", "No, mate. No need to pit.", box)[0]
    assert fuel_honest("Do I need to pit?", "Box this lap, mate, energy won't make it.", box)[0]
    assert not fuel_honest("Will I make it on fuel?", "Yeah, you'll make it, mate.", "not known yet")[0]
    assert fuel_honest("What's the gap?", "1.2, mate.", box)[0]                  # not a fuel question


def test_the_leader_beating_the_clock_after_last_lap_gives_one_more_lap():
    # live 25 Sep: "last lap" on lap 5, the leader crossed with time left, the race ran 6
    s = Strategist()
    s.last_lap_called = True
    leader = replace(rival(1, 0.0), laps=4, lap_dist=13000.0)
    before = race(0.0, {"time_remaining": 5.0, "max_laps": 2147483647}, {"place": 5}, opponents=[leader])
    assert s.leader_over_the_line(before, 0.0) == []
    after = race(3.0, {"time_remaining": 2.0, "max_laps": 2147483647}, {"place": 5},
                 opponents=[replace(leader, laps=5, lap_dist=40.0)])
    extra = s.leader_over_the_line(after, 3.0)
    assert [c.template for c in extra] == ["One more lap after this one. The leader beat the clock."]
    assert not s.last_lap_called                       # the real last lap is called at the next line
