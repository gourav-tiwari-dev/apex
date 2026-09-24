"""The cloned engineer voice, as a small server (24 Sep 2026).

Runs inside GPT-SoVITS's own environment (.sovits/.venv, PyTorch 2.7), which Apex's Python
cannot import, so voice.py starts it as a child process and talks to it over stdin/stdout:

    request   {"id": 3, "text": "Car left!", "mood": "urgent"}
    answer    {"id": 3, "path": "C:/.../line3.wav", "ms": 1840}   or   {"id": 3, "error": "..."}

It warms up before saying it is ready: the first line after loading takes about 20 s, every
line after that 1.2-3.1 s with LMU running (measured on track, 24 Sep). The model and the
reference clips stay on this laptop (.sovits/ is gitignored); only this script is shared.

The voice copies the delivery of a reference clip, so each mood has its own clip from the
interview, with its words written out by hand (a wrong transcript garbled the pronunciation).
"""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sovits")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "GPT_SoVITS"))
os.chdir(ROOT)

SLICES = os.path.join(ROOT, "output", "slicer_opt_max")
PREFIX = "Max Verstappen Answers F1 Driver Questions  Tech Support  WIRED - WIRED.mp3_"
REFERENCES = {
    "urgent": ("0003622400_0003778240", "Wet races are not boring, cars are not slow and there is racing."),
    "dry": ("0006233920_0006364800", "No, you would never say something like that about yourself."),
    "fired": ("0003778240_0004040640", "Most of the time, yeah, that's definitely not the only entertainment, the driver mistakes. So I very heavily disagree."),
}
# the clearest settings, measured with a transcriber on 24 Sep (37 % of words misheard vs
# 40-48 % for calmer sampling or the earlier checkpoint); 1.04x: "slow it down, very little"
SETTINGS = {"top_k": 5, "top_p": 1, "temperature": 0.9, "text_split_method": "cut0", "batch_size": 1,
            "speed_factor": 1.04, "seed": 42, "fragment_interval": 0.07, "parallel_infer": True,
            "repetition_penalty": 1.35}


# 24 Sep, live: "I can't even hear the Verstappen voice, it's so low compared to the game".
# The clone comes out at about 0.08-0.10 RMS (edge-tts is mastered far louder), so every line
# is lifted to a radio-loud level and a soft limiter keeps the peaks from cracking.
TARGET_RMS = 0.16            # 0.25 cracked ("Max's voice is cracking now"): the limiter was driven into distortion
LIMIT = 0.98


def radio_loud(audio):
    import numpy
    audio = numpy.asarray(audio, dtype="float32")
    rms = float(numpy.sqrt(numpy.mean(audio ** 2))) or 1e-6
    audio = audio * (TARGET_RMS / rms)
    return (numpy.tanh(audio / LIMIT) * LIMIT).astype("float32")      # soft limit, no hard clip


def watch_parent():
    """Live 24 Sep: two voice servers were still running after Apex had gone, holding GPU
    memory the next race needed. The server now ends itself the moment its parent is gone."""
    import psutil
    import threading
    parent = int(os.environ.get("APEX_PARENT_PID", "0"))
    if not parent:
        return

    def watch():
        while psutil.pid_exists(parent):
            time.sleep(2)
        os._exit(0)
    threading.Thread(target=watch, daemon=True).start()


def main():
    watch_parent()
    answers = sys.stdout
    sys.stdout = sys.stderr            # GPT-SoVITS prints a lot: keep stdout for answers only
    import numpy
    import soundfile
    from TTS_infer_pack.TTS import TTS, TTS_Config

    started = time.perf_counter()
    tts = TTS(TTS_Config({"custom": {
        "device": "cuda", "is_half": True, "version": "v2ProPlus",
        "t2s_weights_path": os.path.join(ROOT, "GPT_weights_v2ProPlus", "max2-e15.ckpt"),
        "vits_weights_path": os.path.join(ROOT, "SoVITS_weights_v2ProPlus", "max2_e8_s352.pth"),
        "bert_base_path": os.path.join(ROOT, "GPT_SoVITS", "pretrained_models", "chinese-roberta-wwm-ext-large"),
        "cnhuhbert_base_path": os.path.join(ROOT, "GPT_SoVITS", "pretrained_models", "chinese-hubert-base")}}))
    folder = tempfile.mkdtemp(prefix="apex_voice_")

    def speak(text, mood, seed=42):
        clip, prompt = REFERENCES.get(mood, REFERENCES["dry"])
        request = dict(SETTINGS, text=text, text_lang="en", prompt_text=prompt, prompt_lang="en",
                       ref_audio_path=os.path.join(SLICES, PREFIX + clip + ".wav"), seed=seed)
        rate, audio = next(tts.run(request))
        audio = numpy.asarray(audio)
        if audio.dtype.kind == "i":                        # int16 PCM -> -1..1
            audio = audio.astype("float32") / 32768.0
        return rate, radio_loud(audio)

    for mood in REFERENCES:            # warm every mood up now, not in the first lap
        speak("Radio check, mate.", mood)
    answers.write(json.dumps({"ready": True, "load_s": round(time.perf_counter() - started, 1)}) + "\n")
    answers.flush()

    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        began = time.perf_counter()
        try:
            # a different seed is a different take (the voice bank picks its best take)
            rate, audio = speak(request["text"], request.get("mood", "dry"), request.get("seed", 42))
            path = os.path.join(folder, f"line{request['id']}.wav")
            soundfile.write(path, audio, rate)
            answer = {"id": request["id"], "path": path, "ms": round((time.perf_counter() - began) * 1000)}
        except Exception as error:
            answer = {"id": request["id"], "error": f"{error.__class__.__name__}: {error}"}
        answers.write(json.dumps(answer) + "\n")
        answers.flush()
    os._exit(0)                        # stdin closed: Apex is done; do not wait on library threads


if __name__ == "__main__":
    main()
