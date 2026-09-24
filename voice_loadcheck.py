"""Chatterbox next to LMU: can the cloned voice run live while he races? (24 Sep 2026)

    .voice_env/Scripts/python.exe voice_loadcheck.py --model turbo       # without emotion
    .voice_env/Scripts/python.exe voice_loadcheck.py --model original    # with emotion

Loads the model with the reference voice, then speaks a radio line every few seconds for a few
minutes, like a race. For every line it logs how long it took to make and what the GPU looks
like (memory used by everything on it, LMU included, and load). He drives and watches the
frame rate; the log says what the voice cost.
"""
import argparse
import os
import statistics
import subprocess
import tempfile
import time
import winsound

import torch
import torchaudio

REF = "voice_ref/engineer.wav"
# (line, exaggeration, cfg_weight): the original model's emotion per line; Turbo ignores both
LINES = [
    ("Car left! Car left! Hold your line, mate. Hold it!", 0.9, 0.3),
    ("Wide at Arnage. Again. Simply lovely. Tidy it up, mate.", 0.35, 0.5),
    ("That's it! Kossman's done, fucking brilliant move, mate! P four!", 1.2, 0.3),
    ("Gap to Zino one point two. You're taking three tenths a lap. Keep pushing.", 0.5, 0.5),
    ("Yellow flag. Yellow. Sector two.", 0.8, 0.4),
    ("Tertre Rouge, rear's loose on entry. Come off the brake smoother, mate.", 0.6, 0.5),
    ("Last lap. Bring this fucking thing home.", 1.0, 0.3),
    ("Radio's with you, mate. Head down.", 0.4, 0.5),
]


def gpu_state():
    """(MB used on the whole GPU, % load) from nvidia-smi: LMU and the voice together."""
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
    used, total, load = [int(x) for x in out.strip().split(",")]
    return used, total, load


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["turbo", "original"], required=True)
    parser.add_argument("--every", type=float, default=6.0, help="seconds between lines")
    parser.add_argument("--minutes", type=float, default=3.0)
    args = parser.parse_args()

    before, total, _ = gpu_state()
    print(f"GPU before loading the voice: {before} of {total} MB used", flush=True)
    started = time.perf_counter()
    if args.model == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        model = ChatterboxTurboTTS.from_pretrained(device="cuda")
        model.prepare_conditionals(REF)
    else:
        from chatterbox.tts import ChatterboxTTS
        model = ChatterboxTTS.from_pretrained(device="cuda")
        model.prepare_conditionals(REF, exaggeration=0.5)
    loaded, _, _ = gpu_state()
    print(f"{args.model} loaded in {time.perf_counter() - started:.1f} s: GPU now {loaded} MB "
          f"(the voice took {loaded - before} MB)", flush=True)

    folder = tempfile.mkdtemp()
    times = []
    peak = loaded
    end_at = time.time() + args.minutes * 60
    index = 0
    while time.time() < end_at:
        text, exaggeration, cfg = LINES[index % len(LINES)]
        began = time.perf_counter()
        if args.model == "turbo":
            wav = model.generate(text)
        else:
            wav = model.generate(text, exaggeration=exaggeration, cfg_weight=cfg)
        took = time.perf_counter() - began
        used, _, load = gpu_state()
        peak = max(peak, used)
        path = os.path.join(folder, f"line{index}.wav")
        torchaudio.save(path, wav.cpu(), model.sr)
        seconds = wav.shape[-1] / model.sr
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        if index > 0:                       # the first line warms the model up: not counted
            times.append(took)
        print(f"line {index + 1:2d}: made in {took:4.1f} s ({seconds:3.1f} s of speech) | GPU {used} MB, load {load}% | {text}", flush=True)
        index += 1
        time.sleep(max(args.every, seconds + 0.5))

    if times:
        print(f"\n{args.model}: {len(times)} lines, made in median {statistics.median(times):.1f} s, "
              f"worst {max(times):.1f} s | GPU peak {peak} of {total} MB "
              f"(voice added {loaded - before} MB on load)", flush=True)


if __name__ == "__main__":
    main()
