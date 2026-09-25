"""Joins real instant lines from the phrase bank, times the join, and has a transcriber listen
to each result (a trim that clipped a word, or a join that slurs, shows up as misheard words).
Usage: bank_check.py [--clone] [OUT_FOLDER]   the joined WAVs are saved to OUT_FOLDER to listen to."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from phrasebook import Phrasebook
from build_voice_bank import error_rate
from voice import speakable

SPOTTER = ["Car behind, 6 tenths, closing fast. It's already hit you once.",
           "Slow car ahead, before Mulsanne Chicane 1.",
           "Car stopped ahead, Arnage.",
           "Three wide ahead. Stay out of it, let them fight.",
           "Hypercar behind, 1.2 seconds. Hold your line, let it by on the exit."]
MAX = ["Stick it. They're in your tow. Cover the inside into Porsche Curves.",
       "WHAT A FUCKING MOVE! Get in there! Brave through there. Next one, 1.1 seconds.",
       "Lovely. That's how you fucking do it. Great tow. Next one, 8 tenths.",
       "They're back past. Go again. You're quicker out of Tertre Rouge.",
       "Closing fast on the car ahead. 5 tenths.",
       "P19. Car ahead's in trouble.",
       "This fight's costing you 1.4 seconds a lap. Car behind is coming. Go at Indianapolis this lap or settle in."]

clone = "--clone" in sys.argv
out = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
from faster_whisper import WhisperModel
ears = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
worst = 0.0
for book_name, lines in (("spotter", SPOTTER), ("clone" if clone else "engineer", MAX)):
    book = Phrasebook(book_name)
    for n, line in enumerate(lines):
        started = time.perf_counter()
        audio = book.join(line)
        took_ms = (time.perf_counter() - started) * 1000
        if audio is None:
            print(f"  NOT IN BANK ({book_name}): {line}")
            continue
        path = os.path.join(out or os.environ.get("TEMP", "."), f"{book_name}_{n}.wav")
        with open(path, "wb") as f:
            f.write(audio)
        segments, _ = ears.transcribe(path, language="en", beam_size=5)
        heard = " ".join(s.text.strip() for s in segments)
        rate = error_rate(speakable(line, clone=book_name == "clone"), heard)
        worst = max(worst, rate)
        print(f"  {book_name:8s} join {took_ms:5.2f} ms  misheard {rate:4.0%}  heard: {heard}")
print(f"worst misheard share: {worst:.0%}")
