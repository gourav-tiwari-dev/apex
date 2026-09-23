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
from concurrent.futures import ThreadPoolExecutor
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
    "NOT_HERE":       (ENGINEER_VOICE, "Not here. Wait for it."),
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
    """The one owner of the speaker. Everything Apex says goes through here, so nothing can
    talk over anything else by accident (23 Sep: the brief, a yellow and the spotter overlapped)."""

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
        """Returns immediately. Cuts off EVERYTHING else, like a real spotter keying the radio."""
        if not self.out_loud:
            print(f"  URGENT: {fallback_text}")
            return True
        sound = self.bank.get(key)
        if sound is None:
            return False
        self.pygame.mixer.stop()          # any other urgent clip
        self.pygame.mixer.music.stop()    # the engineer mid-sentence
        sound.play()
        return True

    def render(self, text, voice=ENGINEER_VOICE):
        """Text to audio bytes (edge-tts, about 1.3 s). None when printing instead of speaking."""
        if not self.out_loud:
            return None
        return asyncio.run(render(text, voice))

    def play(self, audio, text):
        """Blocks until the line is done. Waits for an urgent clip to finish first, never talks
        over it. Returns the moment the audio actually started (perf_counter)."""
        if not self.out_loud:
            print(f"  RADIO: {text}")
            return time.perf_counter()
        while self.pygame.mixer.get_busy():
            self.pygame.time.wait(20)
        self.pygame.mixer.music.load(io.BytesIO(audio), "mp3")
        self.pygame.mixer.music.play()
        started = time.perf_counter()
        while self.pygame.mixer.music.get_busy():
            self.pygame.time.wait(20)
        return started

    def say(self, text, voice=ENGINEER_VOICE):
        return self.play(self.render(text, voice), text)


class RadioDesk:
    """Phrases, checks, renders and speaks the non-urgent calls.

    Cooking starts the moment a seat raises a call (prepare), while the call is still waiting
    for a straight. When the governor puts it on air (submit), the audio is usually ready, so it
    plays at once. On 23 Sep, doing all of it only after the governor said "go" put lines about
    11 s behind the moment (LLM 1.8 s + voice render 1.3 s + queueing + saying it).

    It never touches the database (the main thread owns it): everything is reported through
    `results`, and the main loop writes the log."""

    def __init__(self, voice, persona, budget, clean=False):
        self.voice = voice
        self.persona = persona
        self.budget = budget
        self.clean = clean
        self.kitchen = ThreadPoolExecutor(max_workers=2)
        self.orders = {}                  # id(call) -> future of the cooked line
        self.admitted_at = {}             # id(call) -> when the governor put it on air
        self.inbox = Queue(maxsize=1)
        self.results = Queue()
        self.latest_sim_time = 0.0
        self.thread = threading.Thread(target=self.work, daemon=True)
        self.thread.start()

    def prepare(self, call):
        if call.urgent or id(call) in self.orders:
            return
        self.orders[id(call)] = self.kitchen.submit(self.cook, call)

    def cook(self, call):
        line = None
        reason = None
        if not call.phrase:
            # code's own words (push-to-talk answers): no model, so no 1.8 s wait and no cost
            line = call.template
        elif self.budget.allows_llm():
            text, tokens_in, tokens_out, seconds = self.persona.phrase(call)
            if tokens_in or tokens_out:
                cost = self.budget.charge(tokens_in, tokens_out)
                # cost is logged even if the line never goes on air
                self.results.put({"llm_only": True, "call": call,
                                  "llm": {"tokens_in": tokens_in, "tokens_out": tokens_out,
                                          "seconds": round(seconds, 3), "cost_rs": round(cost, 5)}})
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
        if line is not None:
            try:
                audio = self.voice.render(line)
            except Exception as error:
                return {"line": line, "audio": None, "reason": f"voice render failed: {error.__class__.__name__}"}
        return {"line": line, "audio": audio, "reason": reason}

    def submit(self, call):
        self.prepare(call)
        try:
            self.admitted_at[id(call)] = time.perf_counter()
            self.inbox.put_nowait(call)
            return True
        except Full:
            self.admitted_at.pop(id(call), None)
            return False

    def report(self, call, status, line=None, reason=None, latency_ms=None):
        self.results.put({"call": call, "status": status, "line": line, "reason": reason,
                          "latency_ms": latency_ms})

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
            if cooked["line"] is None:
                self.report(call, "no_line", reason=cooked["reason"])
                continue
            if self.latest_sim_time - call.sim_time > call.ttl:
                self.report(call, "stale", line=cooked["line"], reason=cooked["reason"])
                continue
            try:
                started = self.voice.play(cooked["audio"], cooked["line"])
            except Exception as error:
                self.report(call, "voice_failed", line=cooked["line"], reason=error.__class__.__name__)
                continue
            # latency = from "go on air" to the first sound, which is what the driver feels
            self.report(call, "spoken", line=cooked["line"], reason=cooked["reason"],
                        latency_ms=round((started - admitted) * 1000))

    def forget(self, call):
        """A call the governor dropped: its cooking (if any) is no longer needed."""
        self.orders.pop(id(call), None)

    def stop(self, grace_s=15):
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
