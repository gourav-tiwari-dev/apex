"""Render the urgent radio lines once, so they play in 0.01 ms during a race.

    python build_voice_bank.py

Needs internet (edge-tts is a cloud voice). Run it again after changing BANK_LINES.
"""
import asyncio
import os

from voice import BANK_FOLDER, BANK_LINES, render


def main():
    os.makedirs(BANK_FOLDER, exist_ok=True)
    for key, (voice, text) in BANK_LINES.items():
        audio = asyncio.run(render(text, voice))
        with open(os.path.join(BANK_FOLDER, key + ".mp3"), "wb") as f:
            f.write(audio)
        print(f"  {key:14s} {voice:20s} {text}")
    print(f"{len(BANK_LINES)} lines saved to {BANK_FOLDER}/")


if __name__ == "__main__":
    main()
