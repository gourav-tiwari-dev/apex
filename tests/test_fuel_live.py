"""Live 25 Sep: 0.6 laps of energy for 1.7 laps of race, and the radio said numbers, then
"no need to pit". Fuel is now measured per metre driven and every answer carries the verdict."""

from dataclasses import replace

from seats.strategist import Strategist, verdict_of, fuel_words
from coach.answer_checks import fuel_honest
from test_seats import moment, race
from test_racecraft import rival


def drive(
    strategist,
    energy_start,
    energy_per_lap,
    metres,
    lap_len=13000.0,
    time_left=256.0,
    lap_time=242.0,
):
    calls = []
    for step in range(0, int(metres / 100) + 1):
        d = step * 100.0
        energy = energy_start - energy_per_lap * d / lap_len
        snap = race(
            1000 + step,
            {"time_remaining": time_left - step * 100 / 54, "max_laps": 2147483647},
            {
                "virtual_energy": energy,
                "fuel": 50.0,
                "fuel_capacity": 120.0,
                "laps": 3,
                "last_lap": lap_time,
                "best_lap": lap_time,
            },
            opponents=[replace(rival(5, 3.0), lap_dist=lap_len - 5)],
        )
        m = moment(1000 + step, snap)
        m.frame.lap_dist = d % lap_len
        calls += strategist.update(m)
    return calls


LAP = 13000.0


def fuel_after(distance, first_half=3.0, second_half=4.8, start=50.0):
    """Fuel left after driving this far, burning more in the second half of every lap, like the
    Porsche Curves and the Mulsanne against the chicanes."""
    laps_done = int(distance // LAP)
    into = distance - laps_done * LAP
    half = LAP / 2
    if into < half:
        this_lap = first_half * into / half
    else:
        this_lap = first_half + second_half * (into - half) / half
    return start - laps_done * (first_half + second_half) - this_lap


def see(strategist, distance, t, phase=5):
    snap = race(
        t,
        {"lap_length": LAP, "game_phase": phase},
        {"fuel": fuel_after(distance), "virtual_energy": 0.0},
    )
    strategist.see_burn(snap, distance % LAP)


def test_the_first_lap_of_the_race_is_not_a_fuel_lap():
    # replay of 25 Sep night (26 Sep): lap 1 burned 7.53 litres in the traffic, the clean laps
    # 7.74-7.78, and measured over lap 1 the radio said "fine, 0.5 laps spare" (tape: tight, 0.33)
    s = Strategist()
    for step in range(0, 10):
        see(s, step * 100.0, step, phase=4)  # the formation lap
    for step in range(10, 150):  # green: lap 1 and a bit more
        see(s, step * 100.0, step)
    litres, energy, whole = s.live_usage()
    assert not whole  # only lap 1 so far: good enough for "box" only
    for step in range(150, 275):  # a whole lap after lap 1: green at 1 km, so 27 km
        see(s, step * 100.0, step)
    litres, energy, whole = s.live_usage()
    assert whole and abs(litres - 7.8) < 0.05


def test_a_spin_does_not_wipe_the_fuel_measured_this_lap():
    # replay of 25 Sep night (26 Sep): punted round at the Porsche Curves, the car rolled back, the
    # burn was wiped as "a reset", and 1.9 km later the radio said "tight, 0.0 laps spare" (tape: 0.33)
    s = Strategist()
    t = 0
    for step in range(0, 121):  # 12 km
        see(s, step * 100.0, t)
        t += 1
    see(s, 11980.0, t)  # spun: rolled back 20 m, then 40 m
    see(s, 11960.0, t + 1)
    for step in range(120, 141):  # on to 14 km
        see(s, step * 100.0, t + 2 + step)
    litres, energy, whole = s.live_usage()
    assert whole
    assert abs(litres - 7.8) < 0.05


def test_the_burn_is_measured_over_exactly_the_last_lap():
    s = Strategist()
    for step in range(0, 171):  # 17 km: a lap and a third
        see(s, step * 100.0, step)
    litres, energy, whole = s.live_usage()
    assert (
        whole and abs(litres - 7.8) < 0.05
    )  # 1.2 laps kept, but a lap and a bit would say more


def test_part_of_a_lap_is_not_stretched_into_a_number():
    # the second half of the lap burns more: 3 km of it stretched to a lap says 9.6 litres, not 7.8
    s = Strategist()
    for step in range(70, 101):  # 7 km to 10 km, just after a refuel
        see(s, step * 100.0, step)
    litres, energy, whole = s.live_usage()
    assert not whole and litres > 9.0  # measured, but only good enough to say "box"


def test_short_on_energy_is_called_box_this_lap_unasked():
    s = Strategist()
    calls = drive(s, energy_start=0.072, energy_per_lap=0.10, metres=3000)
    assert s.fuel_now["verdict"] == "box" and s.fuel_now["limit"] == "energy"
    assert [c.template for c in calls if c.kind == "FUEL"][0].startswith(
        "Box this lap for fuel."
    )


# a number needs a whole lap measured (26 Sep): these drive 14 km of a 13 km lap. The time left at
# the start is chosen so the end of the drive matches the 3 km drives they replaced (54 m/s)
WHOLE_LAP_M = 14000
WHOLE_LAP_S = WHOLE_LAP_M / 54


def test_plenty_of_fuel_says_push_once_and_never_nags():
    s = Strategist()
    calls = drive(
        s,
        energy_start=0.90,
        energy_per_lap=0.10,
        metres=WHOLE_LAP_M,
        time_left=256.0 + WHOLE_LAP_S,
    )
    fuel = [c for c in calls if c.kind == "FUEL"]
    assert s.fuel_now["verdict"] == "fine"
    assert (
        len(fuel) == 1 and fuel[0].template.endswith("Push.") and not fuel[0].immediate
    )


def test_tight_fuel_is_said_not_only_save_or_box():
    # live 25 Sep: 0.3 laps spare the whole race and the radio never said "tight". With about 4 laps
    # to go, as in that race: since 27 Sep "tight" is judged against the laps left
    s = Strategist()
    calls = drive(
        s,
        energy_start=0.13 + 0.10 * 11000 / 13000 + 0.30,
        energy_per_lap=0.10,
        metres=WHOLE_LAP_M,
        time_left=200.4 + WHOLE_LAP_S + 3 * 242.0,
    )
    fuel = [c for c in calls if c.kind == "FUEL"]
    assert s.fuel_now["verdict"] == "tight" and fuel[0].template.startswith(
        "Energy's tight"
    )


def test_part_of_a_lap_says_nothing_unless_it_is_box():
    # the same car after 3 km: part of a lap cannot tell tight from fine, so it waits for the lap
    s = Strategist()
    calls = drive(s, energy_start=0.13, energy_per_lap=0.10, metres=3000)
    assert [c for c in calls if c.kind == "FUEL"] == []


def test_the_verdicts():
    assert verdict_of(1.2, 3) == "fine" and verdict_of(0.2, 3) == "tight"
    assert verdict_of(-0.1, 2) == "save" and verdict_of(-1.1, 1.7) == "box"
    assert fuel_words(
        {"verdict": "box", "limit": "energy", "spare_laps": -1.1}
    ).startswith("Box this lap")


def test_the_coach_cannot_say_no_stop_against_the_fuel_maths_or_without_it():
    box = {"verdict": "box", "spare_laps": -1.1, "limit": "energy"}
    assert not fuel_honest("Do I need to pit?", "No, mate. No need to pit.", box)[0]
    assert fuel_honest(
        "Do I need to pit?", "Box this lap, mate, energy won't make it.", box
    )[0]
    assert not fuel_honest(
        "Will I make it on fuel?", "Yeah, you'll make it, mate.", "not known yet"
    )[0]
    assert fuel_honest("What's the gap?", "1.2, mate.", box)[0]  # not a fuel question


def test_the_leader_beating_the_clock_after_last_lap_gives_one_more_lap():
    # live 25 Sep: "last lap" on lap 5, the leader crossed with time left, the race ran 6
    s = Strategist()
    s.last_lap_called = True
    leader = replace(rival(1, 0.0), laps=4, lap_dist=13000.0)
    before = race(
        0.0,
        {"time_remaining": 5.0, "max_laps": 2147483647},
        {"place": 5},
        opponents=[leader],
    )
    assert s.leader_over_the_line(before, 0.0) == []
    after = race(
        3.0,
        {"time_remaining": 2.0, "max_laps": 2147483647},
        {"place": 5},
        opponents=[replace(leader, laps=5, lap_dist=40.0)],
    )
    extra = s.leader_over_the_line(after, 3.0)
    assert [c.template for c in extra] == [
        "One more lap after this one. The leader beat the clock."
    ]
    assert not s.last_lap_called  # the real last lap is called at the next line


def test_after_a_save_call_fine_means_the_saving_is_working_not_push():
    # live 25 Sep: "short, lift and coast" -> he saved -> "fine, push" right after "box this lap"
    s = Strategist()
    drive(s, energy_start=0.072, energy_per_lap=0.10, metres=3000)
    assert s.told_to_save
    s.last_live_check = None
    s.burn = []
    s.wraps = 0
    s.last_lap_dist = None
    # now it makes the flag, just
    drive(
        s,
        energy_start=0.16 + 0.10 * 11000 / 13000,
        energy_per_lap=0.10,
        metres=WHOLE_LAP_M,
        time_left=200.4 + WHOLE_LAP_S,
    )
    assert s.fuel_now["verdict"] == "saving"
    assert fuel_words(s.fuel_now).startswith("Saving's working")


def test_laps_to_go_uses_the_session_lap_not_the_farthest_car():
    # live 25 Sep, lap 1 at Le Mans: the farthest car was 1.9 km in, so the lap looked 1.9 km long
    from race_state import laps_to_go

    leader = replace(
        rival(5, 3.0), place=1, laps=0, lap_dist=1880.0, last_lap=236.0, best_lap=236.0
    )
    snap = race(
        200.0,
        {"time_remaining": 1159.0, "max_laps": 2147483647, "lap_length": 13624.0},
        {"laps": 0, "place": 20},
        opponents=[leader],
    )
    assert (
        laps_to_go(snap, None) == 6
    )  # the leader crosses 5 more times before the clock


def test_laps_to_go_after_the_leader_takes_the_flag():
    # live 25 Sep, his last lap: the leader had finished and Apex still counted one more lap
    from race_state import laps_to_go

    leader = replace(
        rival(5, 3.0),
        place=1,
        laps=6,
        lap_dist=39.0,
        last_lap=236.0,
        best_lap=236.0,
        finish_status=1,
    )
    snap = race(
        1622.0,
        {"time_remaining": -235.8, "max_laps": 2147483647, "lap_length": 13624.0},
        {"laps": 5, "place": 12},
        opponents=[leader],
    )
    assert laps_to_go(snap, 240.0) == 1  # his current lap is his last


def test_laps_to_go_without_the_session_lap_uses_the_longest_lap_seen():
    # replay of 24 Sep (26 Sep): that tape has no lap length, so the farthest car stood in for it,
    # and at 1320 s the farthest car was the leader himself, 1.1 km from the line: "100% of the lap
    # done", 15 s on the clock became one more lap, the 5-lap race looked 6, and the fuel said
    # "0.8 laps spare" against 1.81 at the flag
    from race_state import laps_to_go, leader_margin
    from race_model import RaceModel

    leader = replace(
        rival(5, 3.0), place=1, laps=4, lap_dist=12556.5, last_lap=242.0, best_lap=239.8
    )
    snap = race(
        1320.0,
        {"time_remaining": 15.0, "max_laps": 2147483647},
        {"laps": 4, "place": 13},
        opponents=[leader],
    )
    model = RaceModel(lap_length=13621.3)  # the longest lap distance seen this race
    assert laps_to_go(snap, None, model) == 1  # the leader's lap is the last: so is his
    assert leader_margin(snap, model) > 0  # the clock runs out before the leader's line


def test_a_lap_at_lights_out_is_not_a_lap_time():
    # live 25 Sep: the formation was timed as a 2:23 "lap, your best"
    s = Strategist()
    snap = race(187.0, {"max_laps": 2147483647}, {"laps": 0, "last_lap": 0.0})
    s.line_time = 44.0
    s.update(moment(187.0, snap, lap=1, wrapped=True))
    assert s.lap_times == []


def test_in_the_last_laps_less_spare_is_still_fine():
    # live 27 Sep: "Energy's tight, 0.4 laps spare. Lift and coast" with 2.4 and then 1.3 laps to go;
    # 1.47 laps spare at the flag (his mark). The right "tight" calls on the tapes had 6-8% spare
    assert verdict_of(0.4, 2.4) == "fine" and verdict_of(0.4, 1.3) == "fine"
    assert (
        verdict_of(0.3, 4.0) == "tight" and verdict_of(0.2, 2.5) == "tight"
    )  # 25 Sep: both right
    assert verdict_of(0.45, 8.0) == "tight"  # long stints: as before
    assert verdict_of(0.05, 0.3) == "tight"  # never "fine" on crumbs


def test_the_leader_is_timed_to_the_line_on_its_own_last_lap():
    # live 27 Sep, 12 s on the clock, the leader 684 m from the line: by distance it was over in 12 s,
    # one more lap; on the road the Ford chicanes took 25 s, the clock ran out first, and that lap was
    # the last. "Laps to go" had been a lap out for minutes, and "Energy's tight" with it
    from race_state import laps_to_go, leader_margin
    from race_model import RaceModel
    from gaps import Trail

    lap = 13624.0
    model = RaceModel(lap_length=lap)

    def time_at(into):
        """Its time at a distance into lap 4 onwards: 60 m/s to the chicanes, then 684 m in 25.6 s."""
        at = into % lap
        fast = min(at, 12940.0) / 60.0
        slow = max(at - 12940.0, 0.0) / 26.7
        return 1000.0 + (into // lap) * 241.3 + fast + slow

    trail = Trail()
    points = [12840.0 + step * 50.0 for step in range(275)] + [12940.0 + lap]
    for into in points:  # from just before this point one lap ago to now
        trail.add(3 * lap + into, time_at(into))
    model.clock.theirs[1] = trail
    leader = replace(
        rival(4, 3.0),
        id=1,
        place=1,
        laps=4,
        lap_dist=12940.0,
        last_lap=-1.0,
        best_lap=239.1,
    )
    snap = race(
        1373.4,
        {"time_remaining": 12.2, "max_laps": 2147483647, "lap_length": lap},
        {"laps": 4, "place": 5},
        opponents=[leader],
    )
    assert (
        laps_to_go(snap, 251.8) == 2
    )  # by distance: over the line with 0.1 s to spare
    assert laps_to_go(snap, 251.8, model) == 1  # on the road: the clock runs out first
    assert leader_margin(snap, model) > 0
