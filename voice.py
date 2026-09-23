"""Turning calls into sound.

Two paths, because they have different jobs:
  URGENT     "Car left." "Yellow flag." These are pre-rendered once into voice_bank/ and
             held in memory. Playing one takes 0.01 ms (measured 23 Sep 2026); rendering it
             live through edge-tts took 1381 ms, which is 75 m of track at 200 km/h.
             An urgent line cuts off whatever the engineer is saying, like a real spotter.
  REFLECTIVE everything else: the persona phrases the call, the gate checks it, then it is
             rendered by edge-tts and played. This runs on a worker thread so the 60 Hz loop
             never waits for the network.
"""
import asyncio
import io
import os
import threading
import time
from queue import Queue, Full, Empty

from persona import gate

ENGINEER_VOICE = "en-GB-RyanNeural"
SPOTTER_VOICE = "en-US-GuyNeural"
BANK_FOLDER = "voice_bank"

# key -> (voice, words). The key is the Call.kind of an urgent call.
BANK_LINES = {
    "CAR_LEFT":       (SPOTTER_VOICE, "Car left."),
    "CAR_RIGHT":      (SPOTTER_VOICE, "Car right."),
    "THREE_WIDE":     (SPOTTER_VOICE, "Three wide. You're in the middle."),
    "STILL_THERE":    (SPOTTER_VOICE, "Still there."),
    "CLEAR":          (SPOTTER_VOICE, "Clear."),
    "YELLOW":         (ENGINEER_VOICE, "Yellow flag. Yellow."),
    "SAFETY_CAR":     (ENGINEER_VOICE, "Safety car. Safety car."),
    "GREEN":          (ENGINEER_VOICE, "Green, green, green."),
    "BLUE_FLAG":      (ENGINEER_VOICE, "Blue flag. Let him by on the exit."),
    "LIGHTS_OUT":     (ENGINEER_VOICE, "Lights out. Go."),
    "RADIO_CHECK":    (ENGINEER_VOICE, "Radio check. I'm with you."),
    "LLM_OFFLINE":    (ENGINEER_VOICE, "Engineer's gone quiet. You know what to do."),
}


async def render(text, voice):
    import edge_tts
    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
    return bytes(audio)


class Voice:
    def __init__(self, out_loud=True):
        self.out_loud = out_loud
        self.bank = {}
        if out_loud:
            import pygame
            self.pygame = pygame
            pygame.mixer.init()
            pygame.mixer.set_num_channels(4)
            self.load_bank()

    def load_bank(self):
        missing = []
        for key in BANK_LINES:
            path = os.path.join(BANK_FOLDER, key + ".mp3")
            if os.path.exists(path):
                self.bank[key] = self.pygame.mixer.Sound(path)
            else:
                missing.append(key)
        if missing:
            print(f"[voice bank: {len(missing)} lines missing - run  python build_voice_bank.py]")

    def play_urgent(self, key, fallback_text):
        """Returns immediately. Cuts off the engineer if he is mid-sentence."""
        if not self.out_loud:
            print(f"  URGENT: {fallback_text}")
            return True
        sound = self.bank.get(key)
        if sound is None:
            return False
        self.pygame.mixer.music.stop()
        sound.play()
        return True

    def say(self, text, voice=ENGINEER_VOICE):
        """Blocks until the line has been spoken. Only ever called from the radio worker."""
        if not self.out_loud:
            print(f"  RADIO: {text}")
            return
        audio = asyncio.run(render(text, voice))
        self.pygame.mixer.music.load(io.BytesIO(audio), "mp3")
        self.pygame.mixer.music.play()
        while self.pygame.mixer.music.get_busy():
            self.pygame.time.wait(20)


class RadioDesk:
    """The worker that phrases, checks and speaks reflective calls, one at a time.

    It never touches the database (the main thread owns it): it reports what happened
    through `results`, and the main loop writes the log."""

    def __init__(self, voice, persona, budget, clean=False):
        self.voice = voice
        self.persona = persona
        self.budget = budget
        self.clean = clean
        self.inbox = Queue(maxsize=1)
        self.results = Queue()
        self.latest_sim_time = 0.0
        self.thread = threading.Thread(target=self.work, daemon=True)
        self.thread.start()

    def submit(self, call):
        try:
            self.inbox.put_nowait(call)
            return True
        except Full:
            return False

    def report(self, call, status, line=None, reason=None, llm=None, started=None):
        latency_ms = None
        if started is not None:
            latency_ms = round((time.perf_counter() - started) * 1000)
        self.results.put({"call": call, "status": status, "line": line, "reason": reason,
                          "llm": llm, "latency_ms": latency_ms})

    def work(self):
        while True:
            call = self.inbox.get()
            if call is None:
                break
            started = time.perf_counter()
            line = None
            reason = None
            llm = None

            if self.budget.allows_llm():
                text, tokens_in, tokens_out, seconds = self.persona.phrase(call)
                if tokens_in or tokens_out:
                    cost = self.budget.charge(tokens_in, tokens_out)
                    llm = {"tokens_in": tokens_in, "tokens_out": tokens_out,
                           "seconds": round(seconds, 3), "cost_rs": round(cost, 5)}
                if text is not None:
                    ok, reason = gate(text, call, self.clean)
                    if ok:
                        line = text
            else:
                reason = "over budget"

            # the gate failed or the model is away: fall back to code's own words
            if line is None and call.template:
                line = call.template
            if line is None:
                self.report(call, "no_line", reason=reason, llm=llm)
                continue

            if self.latest_sim_time - call.sim_time > call.ttl:
                self.report(call, "stale", line=line, reason=reason, llm=llm)
                continue
            try:
                self.voice.say(line)
            except Exception as error:
                self.report(call, "voice_failed", line=line, reason=error.__class__.__name__, llm=llm)
                continue
            self.report(call, "spoken", line=line, reason=reason, llm=llm, started=started)

    def stop(self, grace_s=15):
        self.inbox.put(None)
        self.thread.join(timeout=grace_s)

    def drain(self):
        finished = []
        while True:
            try:
                finished.append(self.results.get_nowait())
            except Empty:
                return finished
