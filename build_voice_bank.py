"""Render the urgent radio lines once, so they play in 0.01 ms during a race.

    python build_voice_bank.py            the standard voice (edge-tts, needs internet)
    python build_voice_bank.py --clone    the cloned engineer voice, into voice_bank/clone/
    python build_voice_bank.py --phrases            the sentences of instant lines (phrasebook.py),
                                                    spotter + standard engineer voice (internet)
    python build_voice_bank.py --phrases --clone    the same sentences in Max's voice (GPU free)

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


EDGE_AT_ONCE = 6                 # edge-tts renders in flight at once


def mp3_to_wav(mp3_bytes):
    """edge-tts only makes MP3; the phrasebook joins raw samples, so decode it once here."""
    import io
    import pygame
    import phrasebook
    if not pygame.mixer.get_init():
        pygame.mixer.init(frequency=24000, size=-16, channels=1)
    # the mixer opens in stereo even when asked for mono (measured: (24000, -16, 2)); read as
    # mono, every sentence came out twice as long and a transcriber heard nonsense
    rate, _, channels = pygame.mixer.get_init()
    import numpy as np
    raw = np.frombuffer(pygame.mixer.Sound(file=io.BytesIO(mp3_bytes)).get_raw(), dtype=np.int16)
    samples = raw.reshape(-1, channels).mean(axis=1)
    return phrasebook.to_wav(samples, rate)


def phrases_standard():
    import phrasebook
    wanted = phrasebook.units()
    jobs = [("spotter", SPOTTER_VOICE_NAME, text) for text in phrasebook.missing("spotter", wanted["spotter"])]
    jobs += [("engineer", voice.ENGINEER_VOICE, text) for text in phrasebook.missing("engineer", wanted["engineer"])]
    print(f"{len(jobs)} sentences to render in the standard voices")

    async def all_of_them():
        gate = asyncio.Semaphore(EDGE_AT_ONCE)

        async def one(book, speaker, text):
            async with gate:
                for attempt in range(3):
                    try:
                        return book, text, await render(speakable(text), speaker)
                    except Exception as error:
                        failure = error
                return book, text, failure
        return await asyncio.gather(*(one(*job) for job in jobs))

    failed = 0
    for book, text, audio in asyncio.run(all_of_them()):
        if isinstance(audio, Exception) or not audio:
            failed += 1
            print(f"  FAILED {book:8s} {text}  ({audio!r})")
            continue
        phrasebook.save_piece(book, text, mp3_to_wav(audio))
    print(f"done: {len(jobs) - failed} saved, {failed} failed (run again to retry the failed ones)")


def phrases_cloned():
    """Max's voice. Each sentence: up to PHRASE_TAKES takes, checked by a transcriber; a sentence
    no take says clearly is left out, and a line that needs it is rendered live (still Max)."""
    import phrasebook
    moods = phrasebook.units()["engineer"]
    wanted = phrasebook.missing("clone", moods)
    print(f"{len(wanted)} sentences to render in Max's voice")
    voice.CLONE_TIMEOUT_S = CLONE_LINE_TIMEOUT_S
    clone = CloneVoice()
    if clone.failed or not clone.ready.wait(240):
        raise SystemExit("The cloned voice is not set up or did not start (see voice_server.log).")
    from faster_whisper import WhisperModel
    ears = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    os.makedirs(phrasebook.PHRASE_FOLDER, exist_ok=True)
    trial = os.path.join(phrasebook.PHRASE_FOLDER, "_take.wav")
    kept, left_out = 0, []
    try:
        for n, text in enumerate(wanted, 1):
            best = None
            for take in range(PHRASE_TAKES):
                audio = clone.render(speakable(text, clone=True), moods[text], seed=2000 + take)
                if audio is None:
                    continue
                with open(trial, "wb") as f:
                    f.write(audio)
                segments, _ = ears.transcribe(trial, language="en", beam_size=5)
                heard = " ".join(s.text.strip() for s in segments)
                rate = heard_right(text, heard)
                if best is None or rate < best[0]:
                    best = (rate, audio, heard)
                if rate == 0:
                    break
            if best is not None and best[0] <= GOOD_ENOUGH:
                phrasebook.save_piece("clone", text, best[1])
                kept += 1
            else:
                left_out.append((text, best[2] if best else "nothing"))
            if n % 25 == 0:
                print(f"  {n}/{len(wanted)} ({kept} kept)")
    finally:
        clone.stop()
        if os.path.exists(trial):
            os.remove(trial)
    for text, heard in left_out:
        print(f"  LEFT OUT (live render instead): \"{text}\" heard \"{heard}\"")
    print(f"done: {kept} of {len(wanted)} sentences in Max's voice")


# 25 Sep, first 75 sentences at 3 takes: only 18 kept. The clone garbles short numbered lines
# ("It's hit you 4 times" -> "I'd set you for time", "P25" -> "Day 25"), so more takes
PHRASE_TAKES = 6


def heard_right(text, heard):
    """Misheard share, against the words as written and as the clone was told to say them
    ("P four", "Tairt Roozh"), numbers compared as digits: the better of the two."""
    from persona import words_to_digits
    as_written = error_rate(words_to_digits(text.lower()), words_to_digits(heard.lower()))
    return min(as_written, error_rate(speakable(text, clone=True), heard))
SPOTTER_VOICE_NAME = voice.SPOTTER_VOICE


if __name__ == "__main__":
    if "--phrases" in sys.argv:
        if "--clone" in sys.argv:
            phrases_cloned()
        else:
            phrases_standard()
    elif "--clone" in sys.argv:
        cloned()
    else:
        standard()
