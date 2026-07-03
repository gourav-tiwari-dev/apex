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
    audio = asyncio.run(_tts_to_memory(text))

    audio.seek(0)

    pygame.mixer.music.load(audio, "mp3")
    pygame.mixer.music.play()

    while pygame.mixer.music.get_busy():
        pygame.time.wait(50)