"""The desk: cooks a line (its words, then its sound) while the call waits for the air.

A seat's call is prepared the moment it is raised, on a worker thread, so when the governor
puts it on air the audio is usually ready and plays at once. On a fast replay every line is
cooked and said on the spot instead, on sim time, so two replays of one tape say the same."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Full, Queue

from radio.lines import MaxLines
from radio.voice import SPOTTER_VOICE, mood_of


class RadioDesk:
    """Words, renders and speaks the non-urgent calls.

    Cooking starts the moment a seat raises a call (prepare), while the call is still waiting
    for a straight. When the governor puts it on air (submit), the audio is usually ready, so it
    plays at once. On 23 Sep, doing all of it only after the governor said "go" put lines about
    11 s behind the moment (LLM 1.8 s + voice render 1.3 s + queueing + saying it).

    It never touches the database (the main thread owns it): everything is reported through
    `results`, and the main loop writes the log."""

    def __init__(self, voice, clean=False, synchronous=False):
        self.voice = voice
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
        """A call's line and its audio: the words (words_of), then the voice bank or a live
        render. A render that fails leaves the line without audio."""
        line = self.words_of(call)
        if line is None:
            return {"line": None, "audio": None, "reason": None}
        if hasattr(self.voice, "from_bank"):
            banked, engine = self.voice.from_bank(line, spotter=call.voice == "spotter")
            if banked is not None:
                call.facts["voice"] = engine
                call.facts["banked"] = (
                    True  # measured in the radio log: how often it hits
                )
                return {"line": line, "audio": banked, "reason": None}
        try:
            audio, engine = self.render(line, call)
        except Exception as error:
            return {
                "line": line,
                "audio": None,
                "reason": f"voice render failed: {error.__class__.__name__}",
            }
        if engine:
            call.facts["voice"] = engine
        return {"line": line, "audio": audio, "reason": None}

    def words_of(self, call):
        """A call's words: his answers and the spotter's lines as they are, everything else
        in code's own words with a Max closer (lines.py)."""
        if call.asked or call.voice == "spotter":
            # his answers are already in Max's voice; spotter lines get no Max closer
            return call.template
        return self.max_lines.line(call)

    def render(self, line, call):
        """(audio, engine) of a live render. The spotter keeps its own voice (his call, 24 Sep):
        Azure's Guy, or edge-tts."""
        if call.voice == "spotter":
            if hasattr(self.voice, "render_spotter"):
                return self.voice.render_spotter(line, mood_of(call.kind))
            audio = (
                self.voice.render(line, voice=SPOTTER_VOICE)
                if getattr(self.voice, "out_loud", False)
                else None
            )
            return audio, "standard"
        if hasattr(self.voice, "render_with_engine"):
            return self.voice.render_with_engine(line, mood=mood_of(call.kind))
        return self.voice.render(line, mood=mood_of(call.kind)), None

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
