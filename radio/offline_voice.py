"""The voice of last resort: Windows' own speech (SAPI, via pyttsx3). No internet, no GPU.

Why (live 25 Sep, warning-lobby race): his internet went weak; every engineer line is voiced by
Microsoft's online voice (edge-tts), which can hang up to its 60 s timeout per line, so the radio
went quiet and the debrief crashed ("Cannot connect to host speech.platform.bing.com"). Now an
online render that does not come back in a few seconds is said in this voice instead: plainer,
but the radio never goes silent. Measured: ~120 ms a line.
"""

import os
import tempfile
import threading

_lock = threading.Lock()  # one SAPI engine at a time


def render(text, voice_index=0, rate=185):
    """WAV bytes, or None if Windows speech is not available."""
    try:
        import pythoncom

        pythoncom.CoInitialize()  # SAPI is COM: every thread that uses it must initialise
    except Exception:
        pass
    try:
        import pyttsx3
    except ImportError:
        return None
    path = os.path.join(
        tempfile.gettempdir(), f"apex_offline_{threading.get_ident()}.wav"
    )
    with _lock:
        try:
            engine = pyttsx3.init()
            voices = engine.getProperty("voices")
            if voices:
                engine.setProperty(
                    "voice", voices[min(voice_index, len(voices) - 1)].id
                )
            engine.setProperty("rate", rate)
            engine.save_to_file(text, path)
            engine.runAndWait()
            engine.stop()
            with open(path, "rb") as f:
                audio = f.read()
            os.remove(path)
            return audio if audio[:4] == b"RIFF" else None
        except Exception:
            return None
