"""v3 step 1 (24 Sep 2026): the start chaos, the talk budget, and the two voices."""

from dataclasses import replace

from radio import Call, Governor, ENGINEER, PERFORMANCE, SPOTTER
from seats.settle import RaceSettle
from test_seats import moment, race, near, behind_car


def chaos_race(t, phase=5, place=6, laps=0, opponents=None, sector_flags=None):
    return race(
        t,
        {"game_phase": phase, "sector_flags": sector_flags or [0, 0, 0]},
        {"place": place, "laps": laps},
        opponents,
    )


def run(settle, t, snapshot, nearby=None, lap=1, new_race=True):
    return settle.update(
        replace(
            moment(t, snapshot, new_race=new_race, nearby=nearby, lap=lap),
            session_type=10,
        )
    )


def test_lights_out_starts_the_chaos_and_calm_settles_it():
    settle = RaceSettle()
    run(settle, 0.0, chaos_race(0.0, phase=3))  # formation lap
    assert settle.settled
    run(settle, 10.0, chaos_race(10.0))  # green
    assert not settle.settled
    said = []
    for step in range(1, 40):
        t = 10.0 + step * 0.5
        said += run(settle, t, chaos_race(t))
    assert settle.settled and [c.kind for c in said] == ["SETTLED"]
    assert said[0].template.startswith("Settled. P6, held position.")
    assert not said[0].phrase  # code's words: no model


def test_a_car_alongside_keeps_the_start_unsettled():
    settle = RaceSettle()
    run(settle, 0.0, chaos_race(0.0, phase=4))
    run(settle, 1.0, chaos_race(1.0))
    for step in range(1, 60):
        t = 1.0 + step * 0.5
        run(
            settle, t, chaos_race(t), nearby=near(t, (3.0, 0.0))
        )  # side by side all along
    assert not settle.settled


def test_place_swaps_around_him_reset_the_clock():
    settle = RaceSettle()
    run(settle, 0.0, chaos_race(0.0, phase=4))
    run(settle, 1.0, chaos_race(1.0))
    swapped = False
    for step in range(1, 61):  # 30 s, a swap every 5 s
        t = 1.0 + step * 0.5
        swapped = not swapped if step % 10 == 0 else swapped
        other = replace(behind_car(0.5), place=7 if swapped else 5)
        run(settle, t, chaos_race(t, opponents=[other]))
    assert not settle.settled  # calm never lasted SETTLE_S


def test_a_brawl_that_never_calms_hands_over_on_lap_3():
    settle = RaceSettle()
    run(settle, 0.0, chaos_race(0.0, phase=4))
    run(settle, 1.0, chaos_race(1.0))
    said = run(settle, 400.0, chaos_race(400.0), nearby=near(400.0, (3.0, 0.0)), lap=3)
    assert [c.kind for c in said] == ["SETTLED"]


def test_apex_started_mid_lap_one_is_still_chaos():
    # 24 Sep: Apex restarted at P24 on lap 1
    settle = RaceSettle()
    run(settle, 500.0, chaos_race(500.0, laps=0))
    assert not settle.settled


def test_practice_is_never_chaos():
    settle = RaceSettle()
    settle.update(replace(moment(0.0, chaos_race(0.0, phase=4)), session_type=2))
    settle.update(replace(moment(1.0, chaos_race(1.0)), session_type=2))
    assert settle.settled


def test_a_restart_after_a_full_course_yellow_is_chaos_again():
    settle = RaceSettle()
    run(settle, 0.0, chaos_race(0.0, phase=6, laps=3), lap=4)
    run(settle, 1.0, chaos_race(1.0, laps=3), lap=4)
    assert not settle.settled


def line(
    seat="performance",
    kind="CORNER_LOSS",
    at=10.0,
    priority=PERFORMANCE,
    ttl=60.0,
    **changes,
):
    return Call(seat, kind, at, priority, ttl, "x", template="x", **changes)


def test_the_governor_drops_coaching_during_the_chaos_but_not_the_spotter_or_answers():
    governor = Governor()
    governor.settled = False
    assert not governor.offer(line())
    assert governor.dropped[0][1] == "start_chaos"
    assert governor.offer(line("spotter", "CAR_LEFT", priority=SPOTTER, urgent=True))
    assert governor.offer(
        line("race_engineer", "ANSWER_POSITION", priority=ENGINEER, asked=True)
    )


def test_the_engineer_gets_two_lines_a_minute_the_spotter_is_never_counted():
    governor = Governor()
    for i in range(4):
        governor.offer(line(seat=f"seat{i}", kind=f"K{i}", at=0.0, ttl=120.0))
    assert governor.step(0.0, in_corner=False) is not None
    assert governor.step(5.0, in_corner=False) is not None
    assert governor.step(10.0, in_corner=False) is None  # 2 in the last minute
    governor.offer(
        line("spotter", "CAR_LEFT", at=11.0, priority=SPOTTER, urgent=True, ttl=1.0)
    )
    assert governor.step(11.0, in_corner=False).kind == "CAR_LEFT"
    assert (
        governor.step(60.5, in_corner=False) is not None
    )  # the first one left the window


def test_the_spotter_calls_are_said_in_the_spotter_voice():
    from voice import SPOTTER_KINDS

    assert {
        "CAR_LEFT",
        "CAR_RIGHT",
        "THREE_WIDE",
        "STILL_THERE",
        "CLEAR",
    } <= SPOTTER_KINDS


def test_a_seat_line_is_said_in_code_words_with_a_max_closer_and_never_asks_the_model():
    from lines import MaxLines
    from seats.race_engineer import spoken

    the_call = spoken(
        "CATCHING", "x", 10.0, {}, template="Car ahead, 1.6. On it by lap 5."
    )
    assert not the_call.phrase
    lines = MaxLines()
    assert (
        lines.line(the_call) == "Car ahead, 1.6. On it by lap 5. Keep fucking pushing."
    )
    assert (
        lines.line(the_call) == "Car ahead, 1.6. On it by lap 5."
    )  # rotates, not random
    assert MaxLines(clean=True).line(the_call).endswith("Keep pushing.")


def test_no_seat_line_names_a_driver():
    import re

    for path in (
        "seats/race_engineer.py",
        "seats/racecraft.py",
        "seats/performance.py",
    ):
        source = open(path, encoding="utf8").read()
        spoken_lines = re.findall(r'template\s*=\s*f"[^"]*"', source)
        assert not [l for l in spoken_lines if "driver" in l], path


def test_praise_and_stick_it_go_out_in_the_start_chaos():
    # his call, 25 Sep: a 3-car pass on the straight at the start got no praise
    from radio import Governor, Call, RACECRAFT

    governor = Governor()
    governor.settled = False
    praise = Call(
        "racecraft",
        "PASS_PRAISE",
        10.0,
        RACECRAFT,
        6.0,
        "p",
        template="WHAT A MOVE!",
        immediate=True,
    )
    plan = Call(
        "racecraft",
        "ATTACK_PLAN",
        10.0,
        RACECRAFT,
        6.0,
        "a",
        template="Pass into Arnage.",
    )
    assert governor.offer(praise) and not governor.offer(plan)
