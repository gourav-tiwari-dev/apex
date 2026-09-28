"""Hear the Azure voices in each emotion before choosing (his ear decides, not a guess).
Usage: azure_samples.py [--save FOLDER]    plays each sample in turn and prints its label."""

import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from radio import azure_voice
from radio.azure_voice import AzureVoice, ENGINEER, SPOTTER

SAMPLES = [
    (
        "engineer",
        "WHAT A FUCKING MOVE! Get in there! Brave through there. Next one, 8 tenths.",
        [("excited", 1.6), ("cheerful", 1.6), ("shouting", 0.8)],
    ),
    (
        "engineer",
        "Car behind is quicker out of Indianapolis. Cover the inside into Arnage. Everywhere else, your line.",
        [("chat", 1.0), ("shouting", 0.7), ("unfriendly", 1.2), ("excited", 1.0)],
    ),
    (
        "spotter",
        "Car left. Still there. Clear.",
        [("shouting", 0.6), ("excited", 1.2), ("chat", 1.0)],
    ),
]


def main():
    azure = AzureVoice()
    if not azure.ready:
        raise SystemExit("No Azure key in .env")
    save = sys.argv[sys.argv.index("--save") + 1] if "--save" in sys.argv else None
    import pygame

    pygame.mixer.init()
    for role, text, styles in SAMPLES:
        for style, degree in styles:
            rate = azure_voice.STYLES[(role, "dry")][2]
            azure_voice.STYLES[(role, "sample")] = (style, degree, rate)
            audio, ms = azure.timed(text, role, "sample")
            label = f"{role} ({ENGINEER if role == 'engineer' else SPOTTER}) {style} {degree}"
            if audio is None:
                print(f"  FAILED {label}: {azure.last_error}")
                continue
            print(f"  {label}  [{ms} ms]  {text}")
            if save:
                os.makedirs(save, exist_ok=True)
                with open(
                    os.path.join(save, f"{role}_{style}_{degree}.wav"), "wb"
                ) as f:
                    f.write(audio)
            sound = pygame.mixer.Sound(file=io.BytesIO(audio))
            sound.play()
            time.sleep(sound.get_length() + 0.8)
            time.sleep(3.2)  # the free tier: 20 requests a minute


if __name__ == "__main__":
    main()
