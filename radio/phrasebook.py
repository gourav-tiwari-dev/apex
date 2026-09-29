"""Radio v3 step 5: every sentence an instant line can contain, rendered ahead of time, so the
line plays the moment it is called instead of ~1.3 s (edge-tts) later.

Why sentences and not whole lines: the lines carry numbers and places ("Car behind, 6 tenths,
closing fast. It's already hit you once.", "Stick it. They're in your tow. Cover the inside
into Arnage."). Whole lines would be thousands of files; their sentences are a few hundred.
A line is split at its sentence ends, each piece is looked up, and the pieces are joined with
a short radio pause. A sentence end is where a voice pauses anyway, so the join does not show.
If any piece is missing, the line is rendered live as before: the bank only ever makes a line
faster, never different.

One book per voice:
    spotter          the standard spotter voice (edge-tts, SPOTTER_VOICE)
    engineer         the standard engineer voice (edge-tts, ENGINEER_VOICE)
    azure_spotter    the same sentences in Azure's voices with emotion (when .env has a key)
    azure_engineer

    python dev/build_voice_bank.py --phrases    spotter + engineer (needs internet)
    python dev/build_voice_bank.py --azure      the Azure books
"""

import hashlib
import io
import json
import os
import re
import wave

import numpy as np

PHRASE_FOLDER = os.path.join("voice_bank", "phrases")
JOIN_PAUSE_S = 0.12  # between two sentences, like a breath on the radio
# live 25 Sep: "the engineer is barely audible, I don't hear half the sentence; the spotter is
# loud and clear". Every line is now levelled to the same loudness (RMS), with a soft limiter so
# it never cracks (0.25 cracked on 24 Sep).
TARGET_RMS = 0.14
LIMIT = 0.92  # the loudest a sample may ever be, as a share of full scale
EDGE_PAD_S = 0.08  # kept either side of a trimmed sentence (soft "s"/"c" starts)
QUIET = 0.01  # below this share of full scale is silence, for trimming


# ---- which sentences ----------------------------------------------------------------------
def gap_words(low_s, high_s):
    """Every way tenths_words() can say a gap between these two, in tenths."""
    from radio.words import tenths_words

    return sorted(
        {
            tenths_words(tenths / 10)
            for tenths in range(round(low_s * 10), round(high_s * 10) + 1)
        }
    )


def corner_names():
    from driving.track_map import MAPS_FOLDER, MONZA_CORNERS

    names = {corner["name"] for corner in MONZA_CORNERS}
    if os.path.isdir(MAPS_FOLDER):
        for file in sorted(os.listdir(MAPS_FOLDER)):
            if file.endswith(".json"):
                with open(os.path.join(MAPS_FOLDER, file)) as f:
                    names |= {corner["name"] for corner in json.load(f)["corners"]}
    return sorted(names)


def reputation_sentences():
    sentences = {"It's already hit you once."}
    sentences |= {f"It's hit you {n} times." for n in range(2, 10)}
    sentences |= {f"That car's had {n} incidents today." for n in range(2, 21)}
    return sentences


def units(kinds=None):
    """book -> every unit (one or more whole sentences) to pre-render. The spotter's is a set;
    the engineer's maps each unit to the mood Max says it in. The engineer units are rendered
    twice: in the standard voice and in Max's."""
    corners = corner_names()
    by_kind = max_sentences(corners)
    from radio.voice import mood_of

    engineer = {}
    for kind, texts in by_kind.items():
        for text in texts:
            engineer.setdefault(text, mood_of(kind))
    if kinds is not None:
        wanted = {}
        for text, mood in engineer.items():
            if any(text in by_kind.get(kind, ()) for kind in kinds):
                wanted[text] = mood
        engineer = wanted
    return {"spotter": spotter_sentences(corners), "engineer": engineer}


def spotter_sentences(corners):
    """Every spotter sentence: the hazards ahead, a car closing fast behind, the faster
    classes arriving."""
    from seats.racecraft import ALARM_MAX_GAP_S
    from seats.track_awareness import FASTER_CLASS_ARRIVES_S

    places = corners + [f"before {name}" for name in corners]

    spotter = {
        "Three wide ahead. Stay out of it, let them fight.",
        "Hold your line, let it by on the exit.",
        "Stay predictable, hold your line.",
    }
    spotter |= {
        f"Car behind, {gap}, closing fast."
        for gap in gap_words(0.1, ALARM_MAX_GAP_S + 0.5)
    }
    spotter |= reputation_sentences()
    for what in ("Slow car", "Car stopped"):
        spotter.add(f"{what} ahead.")
        spotter |= {f"{what} ahead, {where}." for where in places}
    for spoken in ("Hypercar", "LMP2", "Faster car"):
        spotter.add(f"{spoken} behind, closing.")
        spotter.add(f"{spoken} right behind you.")
        spotter.add(f"Two {spoken}s fighting behind.")
    spotter |= {
        f"On you in about {n} seconds."
        for n in range(1, int(FASTER_CLASS_ARRIVES_S) + 1)
    }
    return spotter


def max_sentences(corners):
    """Max's sentences, by the kind of call that says them: each is rendered in THAT call's
    mood (voice.mood_of), the mood a live render of the whole line would get, so a joined
    praise line does not switch from fired to dry halfway through."""
    from seats.racecraft import (
        BRILLIANT,
        SOLID,
        MOVE_WORDS,
        ALARM_MAX_GAP_S,
        FIGHT_COST_S,
    )

    return {
        "PASS_PRAISE": {f"Next one, {gap}." for gap in gap_words(0.1, 9.9)}
        | {text for pair in BRILLIANT + SOLID for text in pair}
        | set(MOVE_WORDS.values()),
        "STICK_IT": {"Stick it. They're in your tow."}
        | {f"Cover the inside into {name}." for name in corners},
        "CLOSING_ON": {"Closing fast on the car ahead."}
        | {
            f"{gap[0].upper()}{gap[1:]}."
            for gap in gap_words(0.1, ALARM_MAX_GAP_S + 0.5)
        },
        "DEFEND_HELD": {
            "Mega defending, mate. They've got fucking nothing.",
            "Mega defending, mate. They've got nothing.",
        },
        "PASSED": {"Stay in the tow."}
        | {f"Get it back into {name}." for name in corners},
        "PASS_RETAKEN": {"They're back past. Go again."}
        | {f"You're quicker out of {name}." for name in corners},
        "PLACE_GIFT": {f"P{n}." for n in range(1, 41)}
        | {f"P{n} in class." for n in range(1, 41)}
        | {"Car ahead's pitting.", "Car ahead's out.", "Car ahead's in trouble."},
        "FIGHT_COST": {"Car behind is coming.", "Commit or settle."}
        | {
            f"This fight's costing you {gap} a lap."
            for gap in gap_words(FIGHT_COST_S, 6.0)
        }
        | {f"Go at {name} this lap or settle in." for name in corners},
        "REPUTATION": reputation_sentences(),  # dry: said after a plan, a fact not a cheer
    }


# ---- splitting and joining ----------------------------------------------------------------
def sentences(text):
    """At a sentence end followed by a space: "1.4 seconds." stays whole."""
    return [piece for piece in re.split(r"(?<=[.!?])\s+", text.strip()) if piece]


def file_name(text):
    return hashlib.sha1(text.encode("utf8")).hexdigest()[:16] + ".wav"


def trimmed(samples, rate):
    """Cut the silence the voice left before and after the words, keeping a short pad."""
    loud = np.flatnonzero(np.abs(samples) > QUIET * 32767)
    if loud.size == 0:
        return samples
    pad = int(EDGE_PAD_S * rate)
    return samples[max(0, loud[0] - pad) : loud[-1] + pad + 1]


def levelled(samples):
    """Every voice at the same radio loudness: gain to TARGET_RMS, then a tanh soft limiter."""
    x = samples.astype(np.float64) / 32767.0
    rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
    if rms < 1e-4:
        return samples
    # tanh never passes 1, so LIMIT is a hard ceiling. The first version divided by tanh(1.2),
    # reached 1.2x full scale, and int16 WRAPPED from +max to -max: 531 of 555 banked sentences
    # cracked ("like bass bursting, nothing audible", live 25 Sep). np.clip is the safety net.
    x = LIMIT * np.tanh(x * (TARGET_RMS / rms))
    return np.clip(x * 32767.0, -32767, 32767).astype(np.int16)


def decode_mp3(mp3_bytes):
    """edge-tts makes MP3; decode it (pygame, the mixer Apex already runs) to mono samples."""
    import io
    import pygame

    if not pygame.mixer.get_init():
        pygame.mixer.init(frequency=24000, size=-16, channels=1)
    # the mixer opens in stereo even when asked for mono (measured: (24000, -16, 2)); read as
    # mono, every sentence came out twice as long and a transcriber heard nonsense
    rate, _, channels = pygame.mixer.get_init()
    raw = np.frombuffer(
        pygame.mixer.Sound(file=io.BytesIO(mp3_bytes)).get_raw(), dtype=np.int16
    )
    return raw.reshape(-1, channels).mean(axis=1).astype(np.int16), rate


def mp3_to_wav(mp3_bytes):
    """edge-tts's MP3 as WAV bytes: the phrase bank joins raw samples, so it keeps WAV."""
    return to_wav(*decode_mp3(mp3_bytes))


def radio_ready(audio):
    """Any rendered line (edge MP3 or WAV) -> levelled WAV bytes, ready to play."""
    try:
        if audio[:4] == b"RIFF":
            samples, rate = read_wav(audio)
        else:
            samples, rate = decode_mp3(audio)
    except Exception:
        return audio  # never lose a line over levelling it
    if samples is None:
        return audio
    return to_wav(levelled(samples), rate)


def to_wav(samples, rate):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())
    return out.getvalue()


def read_wav(data):
    with wave.open(io.BytesIO(data)) as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            return None, None
        return np.frombuffer(
            w.readframes(w.getnframes()), dtype=np.int16
        ), w.getframerate()


class Phrasebook:
    """One voice's rendered sentences, held in memory. join() is the whole race-time cost."""

    def __init__(self, book, folder=PHRASE_FOLDER):
        self.book = book
        self.folder = os.path.join(folder, book)
        self.pieces = {}  # sentence(s) -> samples
        self.rate = None
        index = os.path.join(self.folder, "index.json")
        if not os.path.exists(index):
            return
        with open(index, encoding="utf8") as f:
            for text, name in json.load(f).items():
                path = os.path.join(self.folder, name)
                if not os.path.exists(path):
                    continue
                with open(path, "rb") as audio:
                    samples, rate = read_wav(audio.read())
                if samples is None or (self.rate is not None and rate != self.rate):
                    continue
                self.rate = rate
                self.pieces[text] = samples

    def __len__(self):
        return len(self.pieces)

    def pieces_for(self, text):
        """The longest known units that make up the line, in order, or None if any is missing."""
        parts = sentences(text)
        found = []
        start = 0
        while start < len(parts):
            for end in range(len(parts), start, -1):
                key = " ".join(parts[start:end])
                if key in self.pieces:
                    found.append(key)
                    start = end
                    break
            else:
                return None
        return found

    def join(self, text):
        """WAV bytes of the whole line from the book, or None to render it live."""
        found = self.pieces_for(text)
        if not found:
            return None
        pause = np.zeros(int(JOIN_PAUSE_S * self.rate), dtype=np.int16)
        joined = [self.pieces[found[0]]]
        for key in found[1:]:
            joined += [pause, self.pieces[key]]
        return to_wav(np.concatenate(joined), self.rate)


def save_piece(book, text, wav_bytes, folder=PHRASE_FOLDER):
    """Trim one rendered sentence and add it to the book's index."""
    samples, rate = read_wav(wav_bytes)
    if samples is None:
        raise ValueError(f"not a 16-bit mono WAV: {text}")
    target = os.path.join(folder, book)
    os.makedirs(target, exist_ok=True)
    name = file_name(text)
    with open(os.path.join(target, name), "wb") as f:
        f.write(to_wav(levelled(trimmed(samples, rate)), rate))
    index_path = os.path.join(target, "index.json")
    index = {}
    if os.path.exists(index_path):
        with open(index_path, encoding="utf8") as f:
            index = json.load(f)
    index[text] = name
    with open(index_path, "w", encoding="utf8") as f:
        json.dump(index, f, indent=0, sort_keys=True)


def missing(book, wanted, folder=PHRASE_FOLDER):
    index_path = os.path.join(folder, book, "index.json")
    have = {}
    if os.path.exists(index_path):
        with open(index_path, encoding="utf8") as f:
            have = json.load(f)
    return sorted(text for text in wanted if text not in have)
