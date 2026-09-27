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

import re
import asyncio
import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Full, Empty

from persona import gate
from lines import MaxLines
from phrasebook import Phrasebook, radio_ready
from azure_voice import AzureVoice

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


NUMBER_WORDS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
TENS_WORDS = [
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
]


def number_words(n):
    if n < 20:
        return NUMBER_WORDS[n]
    if n < 100:
        return TENS_WORDS[n // 10] + ("" if n % 10 == 0 else "-" + NUMBER_WORDS[n % 10])
    return str(n)


def lap_time_words(match):
    minutes, seconds, tenth = int(match.group(1)), int(match.group(2)), match.group(3)
    second_words = (
        number_words(seconds) if seconds >= 10 else "oh " + NUMBER_WORDS[seconds]
    )
    return f"{number_words(minutes)} {second_words} point {NUMBER_WORDS[int(tenth)]}"


def speakable(text):
    """Racing shorthand in words a voice can say: "P4" came out as "before" (24 Sep)."""
    text = re.sub(r"\b(\d):(\d\d)\.(\d)\b", lap_time_words, text)  # 3:59.4
    text = re.sub(
        r"\bP(\d{1,2})\b", lambda m: "P " + number_words(int(m.group(1))), text
    )
    text = re.sub(
        r"\bT(\d{1,2})\b", lambda m: "turn " + number_words(int(m.group(1))), text
    )
    return text


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
        import offline_voice

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
                f"[voice bank: {len(missing)} lines missing - run  python build_voice_bank.py]"
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


class RadioDesk:
    """Phrases, checks, renders and speaks the non-urgent calls.

    Cooking starts the moment a seat raises a call (prepare), while the call is still waiting
    for a straight. When the governor puts it on air (submit), the audio is usually ready, so it
    plays at once. On 23 Sep, doing all of it only after the governor said "go" put lines about
    11 s behind the moment (LLM 1.8 s + voice render 1.3 s + queueing + saying it).

    It never touches the database (the main thread owns it): everything is reported through
    `results`, and the main loop writes the log."""

    def __init__(self, voice, persona, budget, clean=False, synchronous=False):
        self.voice = voice
        self.persona = persona
        self.budget = budget
        self.clean = clean
        self.max_lines = MaxLines(clean)
        self.kitchen = ThreadPoolExecutor(max_workers=2)
        self.orders = {}  # id(call) -> future of the cooked line
        self.admitted_at = {}  # id(call) -> when the governor put it on air
        self.inbox = Queue(maxsize=1)
        self.results = Queue()
        self.latest_sim_time = 0.0
        # fast replays: each line is cooked and said on the spot, on sim time. The Governor already
        # keeps the radio busy for as long as a line takes; the desk's own wall-clock waits made two
        # replays of one tape disagree (27 Sep: a SLIDE_CAUGHT spoken in one run, not the other)
        self.synchronous = synchronous
        self.thread = None
        if not synchronous:
            self.thread = threading.Thread(target=self.work, daemon=True)
            self.thread.start()

    def prepare(self, call):
        if self.synchronous or call.urgent or id(call) in self.orders:
            return
        self.orders[id(call)] = self.kitchen.submit(self.cook, call)

    def cook(self, call):
        line = None
        reason = None
        if call.asked or call.voice == "spotter":
            # his answers are already in Max's voice; spotter lines get no Max closer
            line = call.template
        elif not call.phrase:
            # code's own words plus a Max closer: no model, so no 1.8 s wait and no cost
            line = self.max_lines.line(call)
        elif self.budget.allows_llm():
            text, tokens_in, tokens_out, seconds = self.persona.phrase(call)
            if tokens_in or tokens_out:
                cost = self.budget.charge(tokens_in, tokens_out)
                # cost is logged even if the line never goes on air
                self.results.put(
                    {
                        "llm_only": True,
                        "call": call,
                        "llm": {
                            "tokens_in": tokens_in,
                            "tokens_out": tokens_out,
                            "seconds": round(seconds, 3),
                            "cost_rs": round(cost, 5),
                        },
                    }
                )
            if text is not None:
                ok, reason = gate(text, call, self.clean)
                if ok:
                    line = text
        else:
            reason = "over budget"
        # the gate failed or the model is away: fall back to code's own words
        if line is None and call.template:
            line = call.template
        audio = None
        if line is not None and hasattr(self.voice, "from_bank"):
            banked, engine = self.voice.from_bank(line, spotter=call.voice == "spotter")
            if banked is not None:
                call.facts["voice"] = engine
                call.facts["banked"] = (
                    True  # measured in the radio log: how often it hits
                )
                return {"line": line, "audio": banked, "reason": reason}
        if line is not None:
            try:
                if call.voice == "spotter":
                    # the spotter keeps its own voice (his call, 24 Sep): Azure's Guy, or edge-tts
                    if hasattr(self.voice, "render_spotter"):
                        audio, engine = self.voice.render_spotter(
                            line, mood_of(call.kind)
                        )
                    else:
                        audio = (
                            self.voice.render(line, voice=SPOTTER_VOICE)
                            if getattr(self.voice, "out_loud", False)
                            else None
                        )
                        engine = "standard"
                elif hasattr(self.voice, "render_with_engine"):
                    audio, engine = self.voice.render_with_engine(
                        line, mood=mood_of(call.kind)
                    )
                else:
                    audio, engine = (
                        self.voice.render(line, mood=mood_of(call.kind)),
                        None,
                    )
                if engine:
                    call.facts["voice"] = engine
            except Exception as error:
                return {
                    "line": line,
                    "audio": None,
                    "reason": f"voice render failed: {error.__class__.__name__}",
                }
        return {"line": line, "audio": audio, "reason": reason}

    def submit(self, call):
        if self.synchronous:
            self.say_now(call)
            return True
        self.prepare(call)
        try:
            self.admitted_at[id(call)] = time.perf_counter()
            self.inbox.put_nowait(call)
            return True
        except Full:
            self.admitted_at.pop(id(call), None)
            return False

    def say_now(self, call):
        cooked = self.cook(call)
        if cooked["line"] is None:
            self.report(call, "no_line", reason=cooked["reason"])
            return
        self.voice.play(cooked["audio"], cooked["line"])
        self.report(
            call, "spoken", line=cooked["line"], reason=cooked["reason"], latency_ms=0
        )

    def report(self, call, status, line=None, reason=None, latency_ms=None):
        self.results.put(
            {
                "call": call,
                "status": status,
                "line": line,
                "reason": reason,
                "latency_ms": latency_ms,
            }
        )

    def work(self):
        while True:
            call = self.inbox.get()
            if call is None:
                break
            order = self.orders.pop(id(call), None)
            admitted = self.admitted_at.pop(id(call), time.perf_counter())
            try:
                cooked = order.result(timeout=max(call.ttl, 1.0))
            except Exception:
                self.report(call, "no_line", reason="not ready in time")
                continue
            if cooked["line"] is None or (
                cooked["audio"] is None and getattr(self.voice, "out_loud", False)
            ):
                self.report(
                    call, "no_line", line=cooked["line"], reason=cooked["reason"]
                )
                continue
            if self.latest_sim_time - call.sim_time > call.ttl:
                self.report(call, "stale", line=cooked["line"], reason=cooked["reason"])
                continue
            try:
                started = self.voice.play(cooked["audio"], cooked["line"])
            except Exception as error:
                self.report(
                    call,
                    "voice_failed",
                    line=cooked["line"],
                    reason=error.__class__.__name__,
                )
                continue
            # latency = from "go on air" to the first sound, which is what the driver feels
            self.report(
                call,
                "spoken",
                line=cooked["line"],
                reason=cooked["reason"],
                latency_ms=round((started - admitted) * 1000),
            )

    def forget(self, call):
        """A call the governor dropped: its cooking (if any) is no longer needed."""
        self.orders.pop(id(call), None)

    def stop(self, grace_s=15):
        if self.thread is not None:
            self.inbox.put(None)
            self.thread.join(timeout=grace_s)
        self.kitchen.shutdown(wait=False, cancel_futures=True)

    def drain(self):
        finished = []
        while True:
            try:
                finished.append(self.results.get_nowait())
            except Empty:
                return finished
