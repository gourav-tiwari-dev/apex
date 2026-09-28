"""Push-to-talk answers (M9, option A): the question is matched in code and answered from the
live race, with no model in the loop. The button and the mic are hardware and are tested by
hand with  python ptt.py --test."""

from dataclasses import replace

from talk.answers import Answers
from talk.hearing import intent_of, laps_asked
from radio import Call, Governor, Budget, SPOTTER, PERFORMANCE
from seats.performance import PerformanceEngineer
from seats.race_engineer import RaceEngineer
from seats.strategist import Strategist
from test_seats import race, behind_car
from test_radio import FakeVoice
from voice import RadioDesk


def test_questions_find_their_intent_and_the_longest_phrase_wins():
    asked = {
        "What's the gap?": "GAP_AHEAD",
        "Who's behind me?": "GAP_BEHIND",
        "Where am I?": "POSITION",
        "Where am I losing time?": "WHERE_LOSING",
        "How's the fuel looking?": "FUEL",
        "How many laps left?": "LAPS_LEFT",
        "What lap time do I need to catch him?": "PACE_TO_CATCH",
        "Quiet for two laps.": "QUIET",
        "Radio back on.": "RADIO_ON",
        "What was my last lap?": "LAP_TIME",
        "Banana.": None,
    }
    for question, intent in asked.items():
        assert intent_of(question) == intent, question
    assert laps_asked("Quiet for three laps") == 3
    assert laps_asked("Shut up") == 2


def team():
    governor = Governor()
    return governor, Answers(
        governor, RaceEngineer(), Strategist(), PerformanceEngineer()
    )


def ann(behind_leader=6.8):
    return replace(
        behind_car(0.0),
        id=4,
        driver="Ann",
        steam_id=444,
        place=4,
        time_behind_leader=behind_leader,
        last_lap=240.5,
    )


def test_answers_come_from_the_live_race_in_code_words():
    governor, answers = team()
    now = race(
        100.0,
        me_changes={"place": 5, "time_behind_leader": 8.0},
        opponents=[ann(), behind_car(0.0)],
    )
    ahead = answers.answer("what's the gap to the car ahead", now, 3, 100.0)
    # the number first, then Max
    assert ahead.template == "Car ahead, 1.2. Lapping 4:00.5. Go fucking get them."
    assert ahead.asked and not ahead.phrase and ahead.kind == "ANSWER_GAP_AHEAD"
    assert (
        answers.answer("who's behind", now, 3, 100.0).template
        == "Car behind, 1.0. Lapping 1:51.0. Keep them in the fucking mirrors."
    )
    assert (
        answers.answer("what position", now, 3, 100.0).template
        == "P5. Let's fucking do better than that."
    )
    assert answers.answer("how's the fuel", now, 3, 100.0).template.endswith(
        "Usage measured in half a lap."
    )
    answers.strategist.fuel_now = {"spare_laps": 1.4, "laps_left": 3, "limit": "energy"}
    fuel = answers.answer("how's the fuel", now, 3, 100.0)
    assert (
        fuel.seat == "strategist"
        and fuel.template
        == "Energy's fine. 1.4 laps spare. Push. Stop worrying about the fucking fuel."
    )
    answers.strategist.fuel_now = {"spare_laps": 0.2, "laps_left": 3, "limit": "fuel"}
    assert (
        "worrying" not in answers.answer("how's the fuel", now, 3, 100.0).template
    )  # not when it's tight
    assert (
        answers.answer("mumble", now, 3, 100.0).template
        == "Didn't get that, mate. Say again."
    )


def test_quiet_holds_everything_but_urgent_calls_and_answers_until_the_lap():
    governor, answers = team()
    now = race(100.0)
    answers.answer("quiet for two laps", now, 3, 100.0)
    assert governor.quiet_until_lap == 5
    governor.lap = 3
    governor.offer(
        Call("performance", "CORNER_LOSS", 100.0, PERFORMANCE, 15.0, "x", template="x")
    )
    governor.offer(
        Call(
            "spotter",
            "CAR_LEFT",
            100.0,
            SPOTTER,
            1.0,
            "Car left.",
            urgent=True,
            template="Car left.",
        )
    )
    assert governor.step(100.0, in_corner=False).kind == "CAR_LEFT"
    assert governor.dropped[0][1] == "quiet"
    governor.lap = 5  # quiet is over
    governor.offer(
        Call("performance", "CORNER_LOSS", 300.0, PERFORMANCE, 15.0, "x", template="x")
    )
    assert governor.step(300.0, in_corner=False).kind == "CORNER_LOSS"
    answers.answer("quiet", now, 5, 301.0)
    answers.answer("radio back on", now, 5, 302.0)
    assert governor.quiet_until_lap is None


def test_an_answer_goes_out_mid_corner_but_never_over_another_line():
    governor, answers = team()
    governor.offer(
        Call("race_engineer", "GAP_REPORT", 10.0, 4, 20.0, "P5.", template="P5.")
    )
    assert (
        governor.step(10.0, in_corner=False).kind == "GAP_REPORT"
    )  # radio busy ~1.2 s
    governor.offer(answers.answer("what position", race(10.5), 3, 10.5))
    assert governor.step(10.5, in_corner=True) is None  # still talking
    assert (
        governor.step(12.0, in_corner=True).kind == "ANSWER_POSITION"
    )  # he asked: corner or not


def test_an_answer_never_waits_for_the_model():
    class NoModel:
        def phrase(self, call):
            raise AssertionError("an answer must not ask the model")

    voice = FakeVoice()
    desk = RadioDesk(voice, NoModel(), Budget(), clean=False)
    _, answers = team()
    answer = answers.answer("what position", race(10.0), 3, 10.0)
    desk.latest_sim_time = 10.0
    desk.submit(answer)
    desk.stop()
    assert voice.said == ["P5. Let's fucking do better than that."]


def test_the_attitude_takes_turns_and_clean_mode_is_clean():
    governor = Governor()
    now = race(
        100.0, me_changes={"place": 5, "time_behind_leader": 8.0}, opponents=[ann()]
    )
    answers = Answers(governor, RaceEngineer(), Strategist(), PerformanceEngineer())
    first = answers.answer("gap ahead", now, 3, 100.0).template
    second = answers.answer("gap ahead", now, 3, 101.0).template
    assert first != second and second.endswith("Reel that car in, mate.")
    clean = Answers(
        governor, RaceEngineer(), Strategist(), PerformanceEngineer(), clean=True
    )
    for question in (
        "gap ahead",
        "gap ahead",
        "gap ahead",
        "what position",
        "who's behind",
    ):
        assert "fuck" not in clean.answer(question, now, 3, 100.0).template
