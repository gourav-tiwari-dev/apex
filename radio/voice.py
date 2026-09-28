"""Turning calls into sound.

Two paths, because they have different jobs:
  URGENT     "Car left." "Yellow flag." These are pre-rendered once into voice_bank/ and
             held in memory. Playing one takes 0.01 ms (measured 23 Sep 2026); rendering it
             live through edge-tts took 1381 ms, which is 75 m of track at 200 km/h.
             An urgent line cuts off whatever the engineer is saying, like a real spotter.
  REFLECTIVE everything else: the persona phrases the call, the gate checks it, then it is
             rendered and played. This runs on a worker thread so the 60 Hz loop never waits.

The voices (25 Sep 2026): Azure's voices with emotion when .env has a key, edge-tts otherwise
(the mood carried in speed, loudness and pitch), and Windows' own offline voice when the
network is gone, so the radio is never silent.

Two voices, two jobs (his call, 24 Sep 2026): the SPOTTER has its own voice, and everything
else is the engineer, Max. (The cloned Max voice, off since 25 Sep, was removed in the
refactor on 27 Sep; it is in git at tag pre-refactor, and its model folders are still on disk.)
"""

import asyncio
import io
import os
import time

from radio.phrasebook import Phrasebook, radio_ready
from radio.azure_voice import AzureVoice
from radio.words import speakable

# live 25 Sep: Ryan (British, soft) was "barely audible". Christopher: firm and clear, and
# still a different voice from the spotter's Guy.
ENGINEER_VOICE = "en-US-ChristopherNeural"

# the spotter's own lines: said in the spotter's voice
SPOTTER_KINDS = {"CAR_LEFT", "CAR_RIGHT", "THREE_WIDE", "STILL_THERE", "CLEAR"}

# how each kind of call should sound: its mood sets the speed, loudness and pitch (PROSODY)
# and Azure's speaking style
URGENT_KINDS = {
    "CAR_LEFT",
    "CAR_RIGHT",
    "THREE_WIDE",
    "STILL_THERE",
    "YELLOW",
    "SAFETY_CAR",
    "BLUE_FLAG",
    "NOT_HERE",
    "THREAT_BEHIND",
    "DEFEND_PLAN",
    "LIGHTS_OUT",
    "GREEN",
    "PENALTY",
}
FIRED_KINDS = {
    "PASSED",
    "PRAISE",
    "FINISH",
    "CATCHING",
    "ATTACK_PLAN",
    "LAST_LAP",
    "FLAG_LAST_LAP",
    "PASS_PRAISE",
    "DEFEND_HELD",
    "STICK_IT",
    "CLOSING_ON",
    "SETTLED",
}


def mood_of(kind):
    if kind in URGENT_KINDS:
        return "urgent"
    if kind in FIRED_KINDS:
        return "fired"
    return "dry"


SPOTTER_VOICE = "en-US-GuyNeural"
BANK_FOLDER = "voice_bank"
AZURE_BANK_FOLDER = os.path.join("voice_bank", "azure")

# key -> (voice, words). The key is the Call.kind of an urgent call.
BANK_LINES = {
    "CAR_LEFT": (SPOTTER_VOICE, "Car left."),
    "CAR_RIGHT": (SPOTTER_VOICE, "Car right."),
    "THREE_WIDE": (SPOTTER_VOICE, "Three wide. You're in the middle."),
    "STILL_THERE": (SPOTTER_VOICE, "Still there."),
    "CLEAR": (SPOTTER_VOICE, "Clear."),
    "YELLOW": (ENGINEER_VOICE, "Yellow flag. Yellow."),
    "SAFETY_CAR": (ENGINEER_VOICE, "Safety car. Safety car."),
    "GREEN": (ENGINEER_VOICE, "Green, green, green."),
    "BLUE_FLAG": (ENGINEER_VOICE, "Blue flag. Let him by on the exit."),
    "LIGHTS_OUT": (ENGINEER_VOICE, "Lights out. Go."),
    "NOT_HERE": (ENGINEER_VOICE, "Not here. Wait for it."),
    "RADIO_CHECK": (ENGINEER_VOICE, "Radio check. I'm with you."),
    "STAND_BY": (ENGINEER_VOICE, "Copy. Stand by."),
}


# 25 Sep: expressive standard voice (his pick while Azure waits for a card). Microsoft blocks
# edge-tts's emotion styles, so the mood is carried by speed, loudness and pitch instead.
# (role, mood) -> (rate, volume, pitch). Moods come from mood_of(kind). GUESSED: tune by ear.
PROSODY = {
    ("spotter", "urgent"): (
        "+15%",
        "+25%",
        "+0Hz",
    ),  # every spotter call: quick and loud
    ("engineer", "urgent"): ("+12%", "+20%", "-2Hz"),  # fights, flags: quick and firm
    ("engineer", "fired"): (
        "+14%",
        "+25%",
        "+10Hz",
    ),  # praise, attack: quicker, brighter
    ("engineer", "dry"): ("+5%", "+10%", "+0Hz"),  # plans and numbers: calm
}


def prosody(role, mood):
    """(rate, volume, pitch) for edge-tts; the spotter sounds the same whatever the call."""
    if role == "spotter":
        return PROSODY[("spotter", "urgent")]
    return PROSODY.get((role, mood), PROSODY[("engineer", "dry")])


async def render(text, voice, role=None, mood=None):
    import edge_tts

    rate, volume, pitch = prosody(role, mood) if role else ("+0%", "+0%", "+0Hz")
    audio = bytearray()
    async for chunk in edge_tts.Communicate(
        text, voice, rate=rate, volume=volume, pitch=pitch
    ).stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
    return bytes(audio)


EDGE_TIMEOUT_S = 4.0  # edge-tts renders in ~1.3 s; past this the network is struggling


async def render_with_timeout(text, voice, role, mood):
    return await asyncio.wait_for(render(text, voice, role, mood), EDGE_TIMEOUT_S)


def online_or_offline(text, voice, role, mood):
    """(audio, which voice): Microsoft's online voice, or - when the network is weak or gone -
    Windows' own offline voice (live 25 Sep: weak internet silenced the radio for a whole race and
    crashed the debrief). Never raises: a line is better said plainly than not at all."""
    try:
        return radio_ready(
            asyncio.run(render_with_timeout(text, voice, role, mood))
        ), "standard"
    except Exception:
        from radio import offline_voice

        audio = offline_voice.render(text, voice_index=1 if role == "spotter" else 0)
        if audio is None:
            return None, "no_voice"
        return radio_ready(audio), "offline"


class Voice:
    """The one owner of the speaker. Everything Apex says goes through here, so nothing can
    talk over anything else by accident (23 Sep: the brief, a yellow and the spotter overlapped)."""

    def __init__(self, out_loud=True, azure=True):
        self.out_loud = out_loud
        self.bank = {}
        self.books = {}
        self.azure = None
        if out_loud and azure:
            # 25 Sep: Azure's voices with emotion styles; not set up -> edge-tts as before
            candidate = AzureVoice()
            if candidate.ready:
                self.azure = candidate
        if out_loud:
            import pygame

            self.pygame = pygame
            pygame.mixer.init()
            pygame.mixer.set_num_channels(4)
            self.load_bank()
            # v3 step 5: the sentences of the instant lines, pre-rendered (phrasebook.py)
            self.books = {
                "spotter": Phrasebook("spotter"),
                "engineer": Phrasebook("engineer"),
            }
            if self.azure is not None:
                self.books["azure_spotter"] = Phrasebook("azure_spotter")
                self.books["azure_engineer"] = Phrasebook("azure_engineer")
            sizes = ", ".join(
                f"{book} {len(pieces)}" for book, pieces in self.books.items()
            )
            print(f"[phrase bank: {sizes} sentences]")

    def load_bank(self):
        missing = []
        for key in BANK_LINES:
            path = os.path.join(BANK_FOLDER, key + ".wav")  # levelled (25 Sep)
            if not os.path.exists(path):
                path = os.path.join(BANK_FOLDER, key + ".mp3")
            with_emotion = os.path.join(AZURE_BANK_FOLDER, key + ".wav")
            if self.azure is not None and os.path.exists(with_emotion):
                path = with_emotion  # the urgent lines in Azure's voices, with emotion
            if os.path.exists(path):
                self.bank[key] = self.pygame.mixer.Sound(path)
            else:
                missing.append(key)
        if missing:
            print(
                f"[voice bank: {len(missing)} lines missing - run  python dev/build_voice_bank.py]"
            )

    def play_urgent(self, key, fallback_text):
        """Returns immediately. Cuts off EVERYTHING else, like a real spotter keying the radio."""
        if not self.out_loud:
            print(f"  URGENT: {fallback_text}")
            return True
        sound = self.bank.get(key)
        if sound is None:
            return False
        self.pygame.mixer.stop()  # any other urgent clip
        self.pygame.mixer.music.stop()  # the engineer mid-sentence
        sound.play()
        return True

    def play_bank_if_free(self, key, fallback_text):
        """A short acknowledgement ("Copy. Stand by."): only when nothing else is playing,
        so it never cuts off the spotter."""
        if not self.out_loud:
            print(f"  RADIO: {fallback_text}")
            return True
        sound = self.bank.get(key)
        if (
            sound is None
            or self.pygame.mixer.get_busy()
            or self.pygame.mixer.music.get_busy()
        ):
            return False
        sound.play()
        return True

    def azure_up(self):
        return self.azure is not None and self.azure.ready

    def from_bank(self, text, spotter=False):
        """(WAV bytes, which voice) when every sentence of the line is in the phrase bank of
        the voice that would say it, else (None, None) and the line is rendered live.
        The voice is the one render_with_engine would pick, so the bank never changes WHO
        speaks, only how fast."""
        if not self.out_loud:
            return None, None
        if self.azure_up():
            book, engine = ("azure_spotter" if spotter else "azure_engineer"), "azure"
        elif spotter:
            book, engine = "spotter", "standard"
        else:
            book, engine = "engineer", "standard"
        pieces = self.books.get(book)
        audio = pieces.join(text) if pieces else None
        if audio is None:
            return None, None
        return audio, engine

    def render(self, text, voice=ENGINEER_VOICE, mood="dry"):
        """Text to audio bytes: Azure's voice when it is up, else edge-tts (about 1.3 s).
        None when printing instead of speaking."""
        return self.render_with_engine(text, voice, mood)[0]

    def render_with_engine(self, text, voice=ENGINEER_VOICE, mood="dry"):
        """(audio, which voice made it): "azure", "standard" or "offline". Logged per line
        (24 Sep: he heard three different voices and nothing said how often each one spoke)."""
        if not self.out_loud:
            return None, None
        if self.azure_up():
            audio = self.azure.render(speakable(text), "engineer", mood)
            if audio is not None:
                return audio, "azure"
        role = "spotter" if voice == SPOTTER_VOICE else "engineer"
        return online_or_offline(speakable(text), voice, role, mood)

    def render_spotter(self, text, mood="urgent"):
        """The spotter's voice: Azure's with emotion when it is up, else edge-tts."""
        if not self.out_loud:
            return None, None
        if self.azure_up():
            audio = self.azure.render(speakable(text), "spotter", mood)
            if audio is not None:
                return audio, "azure"
        return online_or_offline(speakable(text), SPOTTER_VOICE, "spotter", mood)

    def play(self, audio, text):
        """Blocks until the line is done. Waits for an urgent clip to finish first, never talks
        over it. Returns the moment the audio actually started (perf_counter)."""
        if not self.out_loud:
            print(f"  RADIO: {text}")
            return time.perf_counter()
        while self.pygame.mixer.get_busy():
            self.pygame.time.wait(20)
        # levelled lines, the bank and Azure are WAV; an edge-tts line is MP3 if levelling failed
        kind = "wav" if audio[:4] == b"RIFF" else "mp3"
        self.pygame.mixer.music.load(io.BytesIO(audio), kind)
        self.pygame.mixer.music.play()
        started = time.perf_counter()
        while self.pygame.mixer.music.get_busy():
            self.pygame.time.wait(20)
        return started

    def say(self, text, voice=ENGINEER_VOICE, mood="dry"):
        return self.play(self.render(text, voice, mood), text)
