"""25 Sep 2026: Azure's voices with emotion replace the cloned voice; edge-tts is the fallback."""

from azure_voice import AzureVoice, ssml
from voice import Voice

WAV = b"RIFF....WAVEfmt "


class Answer:
    def __init__(self, status=200, content=WAV):
        self.status_code = status
        self.content = content


class Post:
    """Stands in for requests.post: answers in turn, keeps what was sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent = []

    def __call__(self, url, data, headers, timeout):
        self.sent.append((url, data.decode("utf8"), headers))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def azure(*answers):
    return AzureVoice(key="k", region="centralindia", post=Post(*answers))


def test_the_line_goes_out_with_its_emotion_and_the_right_voice():
    voice = azure(Answer())
    assert voice.render("What a move!", "engineer", "fired") == WAV
    url, body, headers = voice.post.sent[0]
    assert url == "https://centralindia.tts.speech.microsoft.com/cognitiveservices/v1"
    assert "en-US-DavisNeural" in body and "style='excited'" in body
    assert headers["X-Microsoft-OutputFormat"] == "riff-24khz-16bit-mono-pcm"
    voice.post.answers.append(Answer())
    voice.render("Car left.", "spotter", "urgent")
    assert "en-US-GuyNeural" in voice.post.sent[1][1]


def test_words_are_escaped_for_the_xml():
    assert "Car ahead&apos;s" in ssml(
        "Car ahead's <out> & gone", "v", "chat", 1, "+0%"
    ) or "&lt;out&gt; &amp; gone" in ssml(
        "Car ahead's <out> & gone", "v", "chat", 1, "+0%"
    )


def test_the_free_tier_limit_skips_one_line_but_keeps_azure_on():
    voice = azure(*[Answer(429)] * 5, Answer())
    for _ in range(5):
        assert voice.render("Gap?") is None
    assert voice.ready and voice.render("Gap?") == WAV


def test_three_real_failures_in_a_row_switch_azure_off_for_the_session():
    voice = azure(Answer(401), TimeoutError(), Answer(500))
    for _ in range(3):
        assert voice.render("Gap?") is None
    assert not voice.ready and voice.render("Gap?") is None


def test_no_key_means_edge_tts_as_before():
    assert not AzureVoice(key="", region="").ready


def test_the_engineer_and_spotter_speak_through_azure_and_fall_back_to_edge():
    v = Voice(out_loud=False)
    v.out_loud = True
    v.azure = azure(Answer(), Answer(500))
    assert v.render_with_engine("Box this lap.") == (WAV, "azure")
    import voice as voice_module

    original = voice_module.render

    async def edge(text, speaker, *rest):
        return b"MP3"

    voice_module.render = edge
    try:
        assert v.render_spotter("Car left.") == (
            b"MP3",
            "standard",
        )  # Azure failed: edge-tts
    finally:
        voice_module.render = original


def test_the_bank_uses_the_azure_books_when_azure_is_on():
    class Book:
        def __init__(self, name):
            self.name = name

        def join(self, text):
            return self.name.encode()

    v = Voice(out_loud=False)
    v.out_loud = True
    v.azure = azure()
    v.books = {
        name: Book(name)
        for name in ("spotter", "engineer", "azure_spotter", "azure_engineer")
    }
    assert v.from_bank("Slow car ahead.", spotter=True) == (b"azure_spotter", "azure")
    assert v.from_bank("Stick it.") == (b"azure_engineer", "azure")
    v.azure = None
    assert v.from_bank("Stick it.") == (b"engineer", "standard")


def test_the_standard_voice_carries_the_mood_in_speed_loudness_and_pitch():
    from voice import prosody

    praise, plan = prosody("engineer", "fired"), prosody("engineer", "dry")
    faster = int(praise[0].strip("+%")) > int(plan[0].strip("+%"))
    assert praise != plan and faster  # praise quicker than a plan
    assert prosody("spotter", "dry") == prosody(
        "spotter", "urgent"
    )  # the spotter is always sharp


def test_a_dead_network_falls_back_to_the_offline_windows_voice():
    # live 25 Sep: weak internet silenced the radio and crashed the debrief
    import voice as voice_module

    original = voice_module.render

    async def dead(*args, **kwargs):
        raise OSError("getaddrinfo failed")

    voice_module.render = dead
    try:
        audio, engine = voice_module.online_or_offline(
            "Box this lap.", voice_module.ENGINEER_VOICE, "engineer", "urgent"
        )
    finally:
        voice_module.render = original
    assert engine in ("offline", "no_voice")  # never raises, never hangs
    if engine == "offline":
        assert audio[:4] == b"RIFF"
