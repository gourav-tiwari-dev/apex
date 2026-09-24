"""Cut the reference voice for the cloned engineer voice out of any audio or video file.

    python voice_clip.py SOURCE              finds the best 10-15 s of continuous speech
    python voice_clip.py SOURCE --start 42   uses the 15 s starting at 0:42 instead

Writes voice_ref/engineer.wav (mono, 24 kHz, loudness evened out) and prints what is said in
it, so you can check it is the right person and not the interviewer. The voice stays on this
laptop: voice_ref/ is gitignored, and recordings for clips use the normal voice (24 Sep, his
condition for cloning: "it won't be used anywhere, just my laptop for my racing").

Cloning works best from clean speech: one person, no music, no engine noise. The team-radio
sound is added back afterwards by a filter, so an interview is a better source than radio.
"""
import argparse
import os
import wave

import numpy
from faster_whisper import WhisperModel, decode_audio

OUT_DIR = "voice_ref"
OUT_FILE = os.path.join(OUT_DIR, "engineer.wav")
RATE = 24000
LONGEST_S = 15.0
SHORTEST_S = 10.0
TARGET_RMS = 0.1              # about -20 dBFS: loud enough, far from clipping
GOOD_SPEECH_SHARE = 0.8


def speech_segments(path):
    model = WhisperModel("small.en", device="cuda", compute_type="float16")
    audio = decode_audio(path, sampling_rate=16000)
    segments, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=True)
    return [(s.start, s.end, s.text.strip()) for s in segments], len(audio) / 16000


def best_window(segments):
    """The window, starting where a sentence starts, with the most speech in it."""
    best = None
    for index, (start, _, _) in enumerate(segments):
        end = start + LONGEST_S
        spoken = 0.0
        words = []
        for s_start, s_end, text in segments[index:]:
            if s_start >= end:
                break
            spoken += min(s_end, end) - s_start
            words.append(text)
        last_end = min(end, max(s_end for s_start, s_end, _ in segments[index:] if s_start < end))
        length = last_end - start
        if length < SHORTEST_S:
            continue
        share = spoken / length
        if best is None or share > best[2]:
            best = (start, last_end, share, " ".join(words))
    return best


def save(path, start, end):
    audio = decode_audio(path, sampling_rate=RATE)
    clip = audio[int(start * RATE):int(end * RATE)]
    rms = float(numpy.sqrt(numpy.mean(clip ** 2))) or 1e-9
    clip = clip * (TARGET_RMS / rms)
    peak = float(numpy.abs(clip).max())
    if peak > 0.95:
        clip = clip * (0.95 / peak)
    os.makedirs(OUT_DIR, exist_ok=True)
    with wave.open(OUT_FILE, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes((clip * 32767).astype(numpy.int16).tobytes())
    return peak


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--start", type=float, help="seconds into the file to start the clip")
    args = parser.parse_args()

    print("Finding the speech...")
    segments, total = speech_segments(args.source)
    if not segments:
        raise SystemExit("No speech found in that file.")
    if args.start is not None:
        inside = [s for s in segments if args.start <= s[0] < args.start + LONGEST_S]
        spoken = sum(min(e, args.start + LONGEST_S) - s for s, e, _ in inside)
        window = (args.start, args.start + LONGEST_S, spoken / LONGEST_S, " ".join(t for _, _, t in inside))
    else:
        window = best_window(segments)
        if window is None:
            raise SystemExit(f"No {SHORTEST_S:g} s stretch of speech in {total:.0f} s of audio. Try a longer file.")
    start, end, share, words = window
    peak = save(args.source, start, end)
    print(f"Saved {OUT_FILE}: {start:.1f}-{end:.1f} s of {total:.0f} s, speech {share:.0%} of it")
    print(f"It says: \"{words}\"")
    if share < GOOD_SPEECH_SHARE:
        print("Warning: lots of pauses in it. A stretch where he talks without stopping clones better.")
    print("Check it is only his voice (no interviewer, no music). If not, rerun with --start SECONDS.")


if __name__ == "__main__":
    main()
