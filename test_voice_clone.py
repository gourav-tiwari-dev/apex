"""The cloned engineer voice, wired in: the words it is given, the mood per call, and the
fall-back to the standard voice whenever the clone is not there or too slow. The real server
needs the GPU and the private model, so these tests use a stand-in server process."""

import sys
import textwrap

import voice
from voice import CloneVoice, speakable, mood_of


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
    assert (
        speakable("Tertre Rouge", clone=True) == "Tairt Roozh"
    )  # measured clearer respelled
    assert speakable("Tertre Rouge") == "Tertre Rouge"  # edge-tts says it fine


def test_each_kind_of_call_gets_its_mood():
    assert mood_of("CAR_LEFT") == "urgent" and mood_of("THREAT_BEHIND") == "urgent"
    assert mood_of("PASSED") == "fired" and mood_of("FINISH") == "fired"
    assert mood_of("CORNER_LOSS") == "dry" and mood_of("ANSWER_AGENT") == "dry"


def stand_in_server(tmp_path, delay=0.0, ready=True):
    """A tiny process that speaks the server's protocol and writes a fake WAV."""
    script = tmp_path / "server.py"
    script.write_text(
        textwrap.dedent(f"""
        import json, os, sys, time
        if {ready}:
            print(json.dumps({{"ready": True, "load_s": 0.1}}), flush=True)
        for line in sys.stdin:
            request = json.loads(line)
            time.sleep({delay})
            path = os.path.join(r"{tmp_path}", "line%d.wav" % request["id"])
            open(path, "wb").write(b"RIFF" + request["mood"].encode())
            print(json.dumps({{"id": request["id"], "path": path, "ms": 1}}), flush=True)
    """)
    )
    return [sys.executable, str(script)]


def test_the_clone_answers_with_wav_in_the_mood_asked(tmp_path):
    clone = CloneVoice(command=stand_in_server(tmp_path))
    assert clone.ready.wait(10)
    assert clone.render("Car left!", "urgent") == b"RIFFurgent"
    clone.stop()


def test_a_slow_clone_falls_back_and_its_late_answer_is_not_said_later(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(voice, "CLONE_TIMEOUT_S", 0.3)
    clone = CloneVoice(command=stand_in_server(tmp_path, delay=0.6))
    assert clone.ready.wait(10)
    assert clone.render("first", "dry") is None  # too slow: the standard voice says it
    monkeypatch.setattr(voice, "CLONE_TIMEOUT_S", 5.0)
    assert (
        clone.render("second", "fired") == b"RIFFfired"
    )  # the late "first" was skipped
    clone.stop()


def test_no_clone_on_this_machine_means_the_standard_voice(tmp_path):
    clone = CloneVoice(
        command=[str(tmp_path / "missing_python.exe"), "voice_server.py"]
    )
    assert clone.failed and clone.render("Car left!", "urgent") is None


def test_a_clone_still_warming_up_is_not_waited_for(tmp_path):
    clone = CloneVoice(command=stand_in_server(tmp_path, ready=False))
    assert clone.render("Car left!", "urgent") is None
    clone.stop()


def test_the_radio_desk_asks_for_the_mood_of_the_call():
    from radio import Budget, Call, PERFORMANCE
    from test_radio import FakeVoice, FakePersona

    desk_voice = FakeVoice()
    desk = voice.RadioDesk(
        desk_voice, FakePersona("Brilliant, mate."), Budget(), clean=False
    )
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
