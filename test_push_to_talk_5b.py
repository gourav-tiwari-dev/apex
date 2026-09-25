"""v3 step 5b (25 Sep 2026): push-to-talk as the last resort - say again, reminders."""
from answers import Answers, intent_of, needs_agent
from live_telemetry import due_reminders
from radio import Governor
from seats.race_engineer import RaceEngineer
from seats.strategist import Strategist
from seats.performance import PerformanceEngineer
from test_seats import race


def answers():
    return Answers(Governor(), RaceEngineer(), Strategist(), PerformanceEngineer())


def test_say_again_repeats_the_last_engineer_line_in_the_fixed_lane():
    assert intent_of("say that again") == "REPEAT" and not needs_agent("sorry mate what did you say, I didn't catch it")
    lane = answers()
    assert lane.answer("say again", race(1.0), 3, 1.0).template == "Nothing to repeat yet, mate."
    lane.last_line = "Stick it. They're in your tow. Cover the inside into Arnage."
    assert lane.answer("repeat that", race(2.0), 3, 2.0).template == lane.last_line


def test_a_reminder_is_said_at_the_line_of_its_lap_once():
    reminders = [{"remind_lap": 8, "what": "box this lap"}, {"remind_lap": 10, "what": "check fuel"}]
    assert due_reminders(reminders, 7, 100.0) == []
    due = due_reminders(reminders, 8, 200.0)
    assert [c.template for c in due] == ["Reminder: box this lap."] and due[0].asked
    assert due_reminders(reminders, 8, 201.0) == [] and len(reminders) == 1


# ---- lookups answered by code, no model (his ask, 25 Sep) ------------------------------------
def say(question, **me_changes):
    assert not needs_agent(question), question
    return answers().answer(question, race(1.0, me_changes=me_changes), 3, 1.0).template


def test_tyres_brakes_damage_settings_come_straight_from_the_car():
    hot = [[90.0] * 3, [90.0] * 3, [110.0] * 3, [96.0] * 3]
    assert say("How are the tires doing?", tyre_temps=hot).startswith("Fronts 90 and 90. Rears 110 and 96. Rear left cooking")
    assert "Hottest the rear right. Nothing cooking." in say("Tyre temps", tyre_temps=[[90.0] * 3] * 3 + [[99.0] * 3])
    assert say("Damage report", dents=[0] * 8) == "No damage."
    assert say("Damage report", dents=[1, 0, 2, 0, 0, 0, 0, 0]) == "Damage in 2 places around the car."
    assert say("Track limits?", track_limit_steps=5) == "5 of 6 track limit steps. 1 before a penalty."
    assert say("Penalty?", penalties=0) == "No penalty."


def test_what_needs_judgment_still_goes_to_the_agent():
    for question in ("Why are my rears so hot?", "Should I change the brake bias?", "Are the tyres cold?",
                     "Is my car ok after that hit?", "What's the penalty for track limits?",
                     "Can I overtake under yellow?", "Did I damage the aero?", "What's the fastest lap in the race?"):
        assert needs_agent(question), question


def test_what_the_recognizer_wrote_live_still_gets_the_right_answer():
    from answers import fix_mishearing, intent_of
    assert intent_of(fix_mishearing("How is the feeling?")) == "FUEL"          # live 25 Sep, twice
    assert "car ahead" in fix_mishearing("Am I catching the thought ahead?")
    assert fix_mishearing("How's the car feeling in the Esses?") == "How's the car feeling in the Esses?"


def test_fuel_gets_the_tank_from_the_first_lap():
    words = say("How's the fuel?", fuel=54.2, virtual_energy=0.81)
    assert words.startswith("54.2 litres in. Energy 81 percent.")


def test_a_guessed_transcription_gets_say_again_not_a_made_up_answer():
    from answers import garbled
    assert garbled("3-1-1, Faucet's down.", -1.4)                  # live 25 Sep
    assert not garbled("How's the fuel?", -0.3)
    assert garbled("   ", None)


def test_a_new_push_to_talk_opens_the_controller_already_plugged_in():
    # live 25 Sep: SDL announces a controller once per run, so from the second session on the pad
    # was never opened and push-to-talk was deaf for the whole race
    import ptt

    class Pad:
        def __init__(self, index):
            self.index = index

        def get_instance_id(self):
            return 7

        def get_name(self):
            return "Xbox 360 Controller"

    class Joysticks:
        Joystick = Pad

        def get_count(self):
            return 1

    class FakeSDL:
        joystick = Joysticks()

    real_start = ptt.start_sdl
    ptt.start_sdl = lambda: FakeSDL()
    try:
        controller = ptt.Controller({"controller": "Xbox 360 Controller", "button": 5})
    finally:
        ptt.start_sdl = real_start
    assert list(controller.pads) == [7]
