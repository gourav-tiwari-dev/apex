"""The words a voice is given and the mood of each call."""

from radio.desk import RadioDesk
from radio.voice import mood_of
from radio.words import speakable


def test_racing_shorthand_becomes_words_a_voice_can_say():
    assert (
        speakable("That's it! P4!") == "That's it! P four!"
    )  # 24 Sep: "P4" came out "before"
    assert speakable("T11 Parabolica, wide.") == "turn eleven Parabolica, wide."
    assert (
        speakable("You need 3:59.4 to catch")
        == "You need three fifty-nine point four to catch"
    )
    assert speakable("Lapping 4:05.3") == "Lapping four oh five point three"
    assert speakable("Tertre Rouge") == "Tertre Rouge"  # edge-tts says it fine


def test_each_kind_of_call_gets_its_mood():
    assert mood_of("CAR_LEFT") == "urgent" and mood_of("THREAT_BEHIND") == "urgent"
    assert mood_of("PASSED") == "fired" and mood_of("FINISH") == "fired"
    assert mood_of("CORNER_LOSS") == "dry" and mood_of("ANSWER_AGENT") == "dry"


def test_the_radio_desk_asks_for_the_mood_of_the_call():
    from radio.calls import Call, PERFORMANCE
    from coach.llm import Budget
    from test_radio import FakeVoice, FakePersona

    desk_voice = FakeVoice()
    desk = RadioDesk(desk_voice, FakePersona("Brilliant, mate."), Budget(), clean=False)
    call = Call(
        "racecraft",
        "PASSED",
        1.0,
        PERFORMANCE,
        10.0,
        "passed",
        template="Brilliant, mate.",
    )
    desk.latest_sim_time = 1.0
    desk.submit(call)
    desk.stop()
    assert desk_voice.moods == ["fired"]
