"""Push-to-talk (M9): hold R1, ask, let go. Apex answers on the radio.

    python ptt.py --learn     press R1 once: Apex remembers which button it is
    python ptt.py --test      hold R1 and talk: prints what it heard, how fast, and the intent

Measured 24 Sep 2026 on the RTX 5060: speech-to-text with Whisper base.en takes 53 ms on the
GPU (357 ms on the CPU) for a 3.5 s question. NOT measured yet: the same with LMU running.

The pieces:
  - Controller: reads the button through SDL (pygame) with no window. SDL is told to keep
    reading the controller while another window (LMU) has focus.
  - Mic: always open, keeps the last 0.3 s, so the first word is not cut off while the
    button travels.
  - Ears: Whisper on the GPU (the CPU if the GPU is not there), in its own thread, so the
    60 Hz loop never waits for it.
"""
import json
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from queue import Queue, Empty

BUTTON_FILE = "ptt_button.json"
SAMPLE_RATE = 16000
BLOCK = 800                    # 50 ms of audio per chunk
PRE_ROLL_CHUNKS = 6            # 0.3 s kept from before the press
SHORTEST_PRESS_S = 0.3         # shorter than this is a knock on the button, not a question
LONGEST_PRESS_S = 12.0
# small.en, not base.en: base heard "the guy behind me is diving" as "the guy I had means
# defending" on 24 Sep. small is 128 ms on the GPU instead of 53, measured.
MODEL = "small.en"
# words he will say, so Whisper leans towards them
# live 25 Sep: "how's the fuel" came out "how's the feeling", "car ahead" as "thought ahead".
# His real questions go first, so Whisper expects those words.
RADIO_WORDS = ("How's the fuel? How are the tyres? What's the gap? Am I catching the car ahead? "
               "Gap ahead, gap behind, the car ahead, the car behind me is diving, defend, "
               "let him go, overtake, tyre temps, brakes, damage, what's the plan, laps left, lap time, "
               "catch him, quiet, radio back on, say again, where am I losing time.")


def load_button():
    if not os.path.exists(BUTTON_FILE):
        return None
    with open(BUTTON_FILE) as f:
        return json.load(f)


def save_button(controller_name, button):
    with open(BUTTON_FILE, "w") as f:
        json.dump({"controller": controller_name, "button": button}, f)


def start_sdl():
    # must be set before SDL starts: without it the controller goes silent the moment the
    # game window has focus, which is always
    os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
    import pygame
    pygame.display.init()          # SDL's event system; no window is opened
    pygame.joystick.init()
    return pygame


class Controller:
    def __init__(self, button):
        self.pygame = start_sdl()
        self.name = button.get("controller")
        self.button = button["button"]
        self.pads = {}
        self.held = False

    def poll(self):
        """Returns "pressed", "released" or None. Handles the controller being plugged in late."""
        pygame = self.pygame
        change = None
        try:
            events = pygame.event.get()
        except (SystemError, KeyError):
            # 24 Sep, live: pygame raised KeyError(0) from inside event.get() when a controller
            # it never opened disconnected (LMU taking the pad). Start the controller side over.
            pygame.joystick.quit()
            pygame.joystick.init()
            self.pads = {}
            if self.held:
                self.held = False
                return "released"
            return None
        for event in events:
            if event.type == pygame.JOYDEVICEADDED:
                pad = pygame.joystick.Joystick(event.device_index)
                self.pads[pad.get_instance_id()] = pad
            elif event.type == pygame.JOYDEVICEREMOVED:
                self.pads.pop(event.instance_id, None)
                if self.held:
                    self.held = False
                    change = "released"
            elif event.type == pygame.JOYBUTTONDOWN and event.button == self.button and self.mine(event.instance_id):
                if not self.held:
                    self.held = True
                    change = "pressed"
            elif event.type == pygame.JOYBUTTONUP and event.button == self.button and self.mine(event.instance_id):
                if self.held:
                    self.held = False
                    change = "released"
        return change

    def mine(self, instance_id):
        pad = self.pads.get(instance_id)
        # the controller he learned the button on; any controller if that one is not known
        return pad is None or self.name is None or pad.get_name() == self.name


class Mic:
    def __init__(self):
        import sounddevice
        self.lock = threading.Lock()
        self.pre_roll = deque(maxlen=PRE_ROLL_CHUNKS)
        self.chunks = None
        self.stream = sounddevice.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                              blocksize=BLOCK, callback=self.heard)
        self.stream.start()

    def heard(self, indata, frames, when, status):
        chunk = indata[:, 0].copy()
        with self.lock:
            if self.chunks is not None:
                self.chunks.append(chunk)
            else:
                self.pre_roll.append(chunk)

    def start(self):
        with self.lock:
            self.chunks = list(self.pre_roll)

    def stop(self):
        import numpy
        with self.lock:
            chunks = self.chunks or []
            self.chunks = None
            self.pre_roll.clear()
        if not chunks:
            return numpy.zeros(0, dtype="float32")
        return numpy.concatenate(chunks)

    def close(self):
        self.stream.stop()
        self.stream.close()


@dataclass
class Heard:
    text: str
    seconds_of_speech: float
    transcribe_ms: int          # from letting go of the button to having the words
    confidence: float | None = None   # Whisper's mean log-probability; below -1.0 it guessed


class Ears:
    def __init__(self):
        self.model = None
        self.device = None
        self.jobs = Queue()
        self.results = Queue()
        self.track_words = ""        # the corner names of the track he is on
        threading.Thread(target=self.work, daemon=True).start()

    def load(self):
        from faster_whisper import WhisperModel
        try:
            self.model = WhisperModel(MODEL, device="cuda", compute_type="float16")
            self.device = "GPU"
        except Exception as error:
            print(f"[push-to-talk: GPU not available ({error.__class__.__name__}), using the CPU]")
            self.model = WhisperModel(MODEL, device="cpu", compute_type="int8")
            self.device = "CPU"

    def work(self):
        self.load()
        print(f"[push-to-talk ready on the {self.device}]")
        while True:
            audio, released_at = self.jobs.get()
            # live 24 Sep: "Ford Chicanes" was heard as "four chickens". The track's own corner
            # names go into the prompt, so Whisper expects them.
            segments, _ = self.model.transcribe(audio, language="en", beam_size=1, vad_filter=False,
                                                initial_prompt=(RADIO_WORDS + " " + self.track_words).strip())
            segments = list(segments)
            text = " ".join(segment.text for segment in segments).strip()
            took = round((time.perf_counter() - released_at) * 1000)
            confidence = (round(sum(s.avg_logprob for s in segments) / len(segments), 2) if segments else None)
            self.results.put(Heard(text, round(len(audio) / SAMPLE_RATE, 1), took, confidence))

    def listen(self, audio):
        self.jobs.put((audio, time.perf_counter()))

    def finished(self):
        done = []
        while True:
            try:
                done.append(self.results.get_nowait())
            except Empty:
                return done


class PushToTalk:
    """Poll it once per frame; it returns what he said, when he has said it."""

    def __init__(self, button, verbose=False):
        self.controller = Controller(button)
        self.mic = Mic()
        self.ears = Ears()
        self.pressed_at = None
        self.verbose = verbose     # --test prints every step, so a silent failure shows where

    def set_track_words(self, corner_names):
        self.ears.track_words = ", ".join(corner_names) + "." if corner_names else ""

    def poll(self):
        change = self.controller.poll()
        if change == "pressed":
            self.pressed_at = time.perf_counter()
            self.mic.start()
            if self.verbose:
                print("  [button down]")
        elif change == "released" and self.pressed_at is not None:
            held = time.perf_counter() - self.pressed_at
            audio = self.mic.stop()
            self.pressed_at = None
            # always in the log (live 25 Sep: "push to talk not working", and nothing in the log
            # could say whether R1 was pressed, the mic was silent, or the words were lost)
            loudest = float(abs(audio).max()) if len(audio) else 0.0
            print(f"[ptt: held {held:.1f} s, recorded {len(audio) / SAMPLE_RATE:.1f} s, loudest {loudest:.3f}"
                  f"{' - too short, ignored' if held < SHORTEST_PRESS_S else ''}]")
            if SHORTEST_PRESS_S <= held <= LONGEST_PRESS_S:
                self.ears.listen(audio)
        heard = self.ears.finished()
        for h in heard:
            if not h.text:
                print(f"[ptt: heard nothing ({h.seconds_of_speech} s of audio)]")
        if self.verbose:
            return heard
        return [h for h in heard if h.text]

    def close(self):
        self.mic.close()


def start_if_set_up(verbose=False):
    """PushToTalk, or None (with the reason printed) when it cannot run: no button learned
    yet, or the speech packages missing. Apex races on without it either way."""
    button = load_button()
    if button is None:
        print("[push-to-talk off: run  python ptt.py --learn  once, with the controller plugged in]")
        return None
    try:
        return PushToTalk(button, verbose)
    except Exception as error:
        print(f"[push-to-talk off: {error.__class__.__name__}: {error}]")
        return None


def learn():
    pygame = start_sdl()
    print("Plug in the controller, then press R1 (the button you will hold to talk)...")
    pads = {}
    while True:
        for event in pygame.event.get():
            if event.type == pygame.JOYDEVICEADDED:
                pad = pygame.joystick.Joystick(event.device_index)
                pads[pad.get_instance_id()] = pad
                print(f"  controller: {pad.get_name()}")
            elif event.type == pygame.JOYBUTTONDOWN:
                name = pads[event.instance_id].get_name() if event.instance_id in pads else None
                save_button(name, event.button)
                print(f"Saved: button {event.button} on {name}. Push-to-talk is set up.")
                return
        time.sleep(0.01)


def test():
    from answers import intent_of
    import sounddevice
    print(f"mic: {sounddevice.query_devices(kind='input')['name']}")
    ptt = start_if_set_up(verbose=True)
    if ptt is None:
        return
    print("Loading speech-to-text... then hold the button and ask something. Ctrl+C to stop.")
    try:
        while True:
            for heard in ptt.poll():
                print(f"  heard: {heard.text!r}  ({heard.seconds_of_speech} s of speech, words ready "
                      f"{heard.transcribe_ms} ms after letting go)  ->  {intent_of(heard.text)}")
            time.sleep(0.01)
    except KeyboardInterrupt:
        ptt.close()


if __name__ == "__main__":
    if "--learn" in sys.argv:
        learn()
    elif "--test" in sys.argv:
        test()
    else:
        print(__doc__)
