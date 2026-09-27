"""Render the urgent radio lines once, so they play in 0.01 ms during a race.

    python build_voice_bank.py              the standard voice (edge-tts, needs internet)
    python build_voice_bank.py --phrases    the sentences of instant lines (phrasebook.py),
                                            spotter + standard engineer voice (internet)
    python build_voice_bank.py --azure      all of it in Azure's voices with emotion (.env key)

Run it again after changing BANK_LINES. voice_bank/ is gitignored.
"""

import asyncio
import os
import sys

from voice import (
    SPOTTER_KINDS,
    BANK_FOLDER,
    BANK_LINES,
    mood_of,
    render,
    speakable,
)
import voice


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
            table[j] = min(
                table[j] + 1, table[j - 1] + 1, previous + (a[i - 1] != b[j - 1])
            )
            previous = current
    return table[len(b)] / max(1, len(a))


def standard():
    os.makedirs(BANK_FOLDER, exist_ok=True)
    for key, (speaker, text) in BANK_LINES.items():
        audio = asyncio.run(
            render(
                text,
                speaker,
                "spotter" if key in SPOTTER_KINDS else "engineer",
                mood_of(key),
            )
        )
        from phrasebook import radio_ready

        with open(os.path.join(BANK_FOLDER, key + ".wav"), "wb") as f:
            f.write(radio_ready(audio))  # levelled like every live line
        print(f"  {key:14s} {speaker:20s} {text}")
    print(f"{len(BANK_LINES)} lines saved to {BANK_FOLDER}/")


EDGE_AT_ONCE = 6  # edge-tts renders in flight at once


def azure_bank():
    """Everything the bank holds, in Azure's voices with emotion: the sentences of the instant
    lines (phrase books azure_spotter / azure_engineer) and the urgent whole lines
    (voice_bank/azure/). Paced under the free tier's 20 requests a minute; run it again to
    finish or retry - what is already there is kept."""
    import time
    import phrasebook
    from azure_voice import AzureVoice, FREE_TIER_PER_MINUTE

    azure = AzureVoice()
    if not azure.ready:
        raise SystemExit(
            "No Azure key: put AZURE_SPEECH_KEY and AZURE_SPEECH_REGION in .env"
        )
    wanted = phrasebook.units()
    jobs = [
        ("azure_spotter", "spotter", "urgent", text)
        for text in phrasebook.missing("azure_spotter", wanted["spotter"])
    ]
    jobs += [
        ("azure_engineer", "engineer", wanted["engineer"][text], text)
        for text in phrasebook.missing("azure_engineer", wanted["engineer"])
    ]
    os.makedirs(voice.AZURE_BANK_FOLDER, exist_ok=True)
    for key, (_, text) in BANK_LINES.items():
        if not os.path.exists(os.path.join(voice.AZURE_BANK_FOLDER, key + ".wav")):
            role = "spotter" if key in SPOTTER_KINDS else "engineer"
            jobs.append(("whole", role, mood_of(key), (key, text)))
    gap_s = 60.0 / (FREE_TIER_PER_MINUTE - 1)
    print(
        f"{len(jobs)} to render, about {round(len(jobs) * gap_s / 60)} minutes (free tier pace)"
    )
    failed = 0
    for n, (book, role, mood, text) in enumerate(jobs, 1):
        started = time.perf_counter()
        words = text[1] if book == "whole" else text
        audio = azure.render(speakable(words), role, mood, timeout=15)
        if audio is None:
            failed += 1
            print(f"  FAILED ({azure.last_error}): {words}")
            if not azure.ready:
                raise SystemExit(
                    "Azure stopped answering (key, region or quota): run again later"
                )
        elif book == "whole":
            with open(
                os.path.join(voice.AZURE_BANK_FOLDER, text[0] + ".wav"), "wb"
            ) as f:
                f.write(audio)
        else:
            phrasebook.save_piece(book, text, audio)
        if n % 25 == 0:
            print(f"  {n}/{len(jobs)}")
        time.sleep(max(0.0, gap_s - (time.perf_counter() - started)))
    print(
        f"done: {len(jobs) - failed} saved, {failed} failed (run again to retry them)"
    )


def phrases_standard():
    import phrasebook

    wanted = phrasebook.units()
    jobs = [
        ("spotter", SPOTTER_VOICE_NAME, text)
        for text in phrasebook.missing("spotter", wanted["spotter"])
    ]
    jobs += [
        ("engineer", voice.ENGINEER_VOICE, text)
        for text in phrasebook.missing("engineer", wanted["engineer"])
    ]
    moods = wanted["engineer"]
    print(f"{len(jobs)} sentences to render in the standard voices")

    async def all_of_them():
        gate = asyncio.Semaphore(EDGE_AT_ONCE)

        async def one(book, speaker, text):
            async with gate:
                for attempt in range(3):
                    try:
                        mood = "urgent" if book == "spotter" else moods[text]
                        return (
                            book,
                            text,
                            await render(speakable(text), speaker, book, mood),
                        )
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
        phrasebook.save_piece(book, text, phrasebook.mp3_to_wav(audio))
    print(
        f"done: {len(jobs) - failed} saved, {failed} failed (run again to retry the failed ones)"
    )


SPOTTER_VOICE_NAME = voice.SPOTTER_VOICE


if __name__ == "__main__":
    if "--azure" in sys.argv:
        azure_bank()
    elif "--phrases" in sys.argv:
        phrases_standard()
    else:
        standard()
