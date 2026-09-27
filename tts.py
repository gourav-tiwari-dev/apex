import asyncio
import io

import edge_tts
import pygame

VOICE = "en-US-GuyNeural"

pygame.mixer.init()


async def _tts_to_memory(text: str):
    communicate = edge_tts.Communicate(text, VOICE)

    audio = bytearray()

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])

    return io.BytesIO(audio)


def speak(text: str):
    """The brief and the debrief. Online voice first; with a weak or dead network, Windows' own
    offline voice (25 Sep: a dropped connection crashed Apex in the debrief)."""
    kind = "mp3"
    try:
        audio = asyncio.run(asyncio.wait_for(_tts_to_memory(text), 6.0))
    except Exception:
        import offline_voice

        wav = offline_voice.render(text)
        if wav is None:
            print(f"  (no voice available) {text}")
            return
        audio, kind = io.BytesIO(wav), "wav"

    audio.seek(0)

    pygame.mixer.music.load(audio, kind)
    pygame.mixer.music.play()

    while pygame.mixer.music.get_busy():
        pygame.time.wait(50)
