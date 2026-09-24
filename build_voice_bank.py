"""Render the urgent radio lines once, so they play in 0.01 ms during a race.

    python build_voice_bank.py            the standard voice (edge-tts, needs internet)
    python build_voice_bank.py --clone    the cloned engineer voice, into voice_bank/clone/

Run it again after changing BANK_LINES. The cloned bank stays on this laptop
(voice_bank/ is gitignored) and is used only when the cloned voice is on, never with --record.
"""
import asyncio
import os
import sys

from voice import SPOTTER_KINDS, BANK_FOLDER, BANK_LINES, CLONE_BANK_FOLDER, CloneVoice, mood_of, render, speakable
import voice

CLONE_LINE_TIMEOUT_S = 30.0      # rendering ahead of time: no hurry, unlike a live line
TAKES = 8
# 24 Sep, first cloned bank: "Car left" came out as "Carla?", "Green" as "Brain". Very short
# lines are this model's weak spot, so every take is checked by a transcriber and the best
# kept. A spotter or flag line must be heard exactly, or it stays in the standard voice: a
# clear standard "Car left" beats a cloned one that sounds like a name.
MUST_BE_EXACT = {"CAR_LEFT", "CAR_RIGHT", "THREE_WIDE", "STILL_THERE", "CLEAR", "YELLOW", "SAFETY_CAR",
                 "GREEN", "BLUE_FLAG", "LIGHTS_OUT", "NOT_HERE"}
GOOD_ENOUGH = 0.2                # the other lines: at most one word in five misheard


def words(text):
    import re
    return re.sub(r"[^a-z0-9' ]+", " ", text.lower().replace("-", " ")).split()


def error_rate(meant, heard):
    a, b = words(meant), words(heard)
    table = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        previous, table[0] = table[0], i
        for j in range(1, len(b) + 1):
            current = table[j]
            table[j] = min(table[j] + 1, table[j - 1] + 1, previous + (a[i - 1] != b[j - 1]))
            previous = current
    return table[len(b)] / max(1, len(a))


def standard():
    os.makedirs(BANK_FOLDER, exist_ok=True)
    for key, (speaker, text) in BANK_LINES.items():
        audio = asyncio.run(render(text, speaker))
        with open(os.path.join(BANK_FOLDER, key + ".mp3"), "wb") as f:
            f.write(audio)
        print(f"  {key:14s} {speaker:20s} {text}")
    print(f"{len(BANK_LINES)} lines saved to {BANK_FOLDER}/")


def cloned():
    voice.CLONE_TIMEOUT_S = CLONE_LINE_TIMEOUT_S
    clone = CloneVoice()
    if clone.failed or not clone.ready.wait(240):
        raise SystemExit("The cloned voice is not set up or did not start (see voice_server.log).")
    from faster_whisper import WhisperModel
    ears = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    os.makedirs(CLONE_BANK_FOLDER, exist_ok=True)
    trial = os.path.join(CLONE_BANK_FOLDER, "_take.wav")
    kept = 0
    try:
        for key, (_, text) in BANK_LINES.items():
            if key in SPOTTER_KINDS:
                continue                   # the spotter is always the standard voice (24 Sep)
            target = os.path.join(CLONE_BANK_FOLDER, key + ".wav")
            best = None
            for take in range(TAKES):
                audio = clone.render(speakable(text, clone=True), mood_of(key), seed=1000 + take)
                if audio is None:
                    continue
                with open(trial, "wb") as f:
                    f.write(audio)
                segments, _ = ears.transcribe(trial, language="en", beam_size=5)
                heard = " ".join(s.text.strip() for s in segments)
                rate = error_rate(text, heard)
                if best is None or rate < best[0]:
                    best = (rate, audio, heard, take + 1)
                if rate == 0:
                    break
            limit = 0.0 if key in MUST_BE_EXACT else GOOD_ENOUGH
            if best is not None and best[0] <= limit:
                with open(target, "wb") as f:
                    f.write(best[1])
                kept += 1
                print(f"  {key:14s} cloned  (take {best[3]}, heard \"{best[2]}\")")
            else:
                if os.path.exists(target):
                    os.remove(target)      # the standard voice plays this one
                heard = best[2] if best else "nothing"
                print(f"  {key:14s} STANDARD voice: no take clear enough (best heard \"{heard}\")")
    finally:
        clone.stop()
        if os.path.exists(trial):
            os.remove(trial)
    print(f"{kept} of {len(BANK_LINES)} lines in the cloned voice, saved to {CLONE_BANK_FOLDER}/")


if __name__ == "__main__":
    if "--clone" in sys.argv:
        cloned()
    else:
        standard()
