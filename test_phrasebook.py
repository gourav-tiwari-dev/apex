"""v3 step 5 (25 Sep 2026): instant lines play from pre-rendered sentences, joined in well under
a millisecond, instead of ~1.3 s (edge-tts) or ~1.8 s (the clone) of live rendering."""
import numpy as np

import phrasebook
from phrasebook import Phrasebook, save_piece, sentences, trimmed, read_wav, to_wav
from voice import Voice, RadioDesk
from radio import Budget, Call, SPOTTER, RACECRAFT
from test_radio import FakeVoice, FakePersona

RATE = 24000


def tone(seconds, silence_s=0.0):
    words = (np.sin(np.arange(int(seconds * RATE)) / 5) * 10000).astype(np.int16)
    quiet = np.zeros(int(silence_s * RATE), dtype=np.int16)
    return to_wav(np.concatenate([quiet, words, quiet]), RATE)


def book_of(tmp_path, texts):
    for text in texts:
        save_piece("spotter", text, tone(0.2, silence_s=0.5), folder=str(tmp_path))
    return Phrasebook("spotter", folder=str(tmp_path))


def test_a_line_splits_at_sentence_ends_but_not_inside_a_number():
    assert sentences("Clear. Great tow. Next one, 1.1 seconds.") == ["Clear.", "Great tow.", "Next one, 1.1 seconds."]
    assert sentences("WHAT A MOVE! Get in there!") == ["WHAT A MOVE!", "Get in there!"]


def test_the_silence_around_a_sentence_is_trimmed_to_a_short_pad():
    samples, _ = read_wav(tone(0.2, silence_s=0.5))
    cut = trimmed(samples, RATE)
    assert abs(len(cut) / RATE - (0.2 + 2 * phrasebook.EDGE_PAD_S)) < 0.01


def test_a_line_is_joined_from_its_sentences_with_a_radio_pause(tmp_path):
    book = book_of(tmp_path, ["Stick it. They're in your tow.", "Cover the inside into Arnage."])
    audio = book.join("Stick it. They're in your tow. Cover the inside into Arnage.")
    samples, rate = read_wav(audio)
    piece = 0.2 + 2 * phrasebook.EDGE_PAD_S
    assert abs(len(samples) / rate - (2 * piece + phrasebook.JOIN_PAUSE_S)) < 0.01
    # the two-sentence unit was used whole, not rebuilt from pieces
    assert book.pieces_for("Stick it. They're in your tow. Cover the inside into Arnage.") == [
        "Stick it. They're in your tow.", "Cover the inside into Arnage."]


def test_one_missing_sentence_means_the_line_is_rendered_live(tmp_path):
    book = book_of(tmp_path, ["Car behind, 6 tenths, closing fast."])
    assert book.join("Car behind, 6 tenths, closing fast. It's already hit you once.") is None
    assert Phrasebook("clone", folder=str(tmp_path)).join("Clear.") is None      # no book at all


# ---- the words the seats say are all in the list of sentences to render --------------------
REAL_INSTANT_LINES = {       # from the 23 and 24 Sep replays, 25 Sep
    "spotter": ["Car behind, 6 tenths, closing fast. It's already hit you once.",
                "Car behind, 1.5 seconds, closing fast.",
                "Slow car ahead, before Mulsanne Chicane 1.", "Car stopped ahead, Arnage.",
                "Three wide ahead. Stay out of it, let them fight.",
                "Hypercar behind, 1.2 seconds. Hold your line, let it by on the exit.",
                "Two LMP2s fighting behind. Stay predictable, hold your line."],
    "engineer": ["Stick it. They're in your tow. Cover the inside into Porsche Curves.",
                 "WHAT A FUCKING MOVE! Get in there! Brave through there. Next one, 1.1 seconds.",
                 "Lovely. That's how you fucking do it. Great tow. Next one, 8 tenths.",
                 "Good job. Clean as you like. Better exit did it.",
                 "They're back past. Go again. You're quicker out of Tertre Rouge.",
                 "Closing fast on the car ahead. 5 tenths.", "Closing fast on the car ahead. A tenth.",
                 "P19. Car ahead's in trouble.", "Mega defending, mate. They've got fucking nothing.",
                 "Stay in the tow. Get it back into Indianapolis.",
                 "This fight's costing you 1.4 seconds a lap. Car behind is coming. Go at Indianapolis this lap or settle in."],
}


def test_every_real_instant_line_is_made_of_sentences_the_bank_renders():
    wanted = phrasebook.units()
    for book_name, lines in REAL_INSTANT_LINES.items():
        book = Phrasebook(book_name, folder="nowhere")
        book.pieces = {text: np.zeros(1, dtype=np.int16) for text in wanted[book_name]}
        for line in lines:
            assert book.pieces_for(line) is not None, line


# ---- which book: the bank never changes who speaks, only how fast ---------------------------
class Books:
    def __init__(self, name):
        self.name = name

    def join(self, text):
        return f"{self.name}:{text}".encode()


class UpClone:
    failed = False

    class ready:
        @staticmethod
        def is_set():
            return True


def voice_with_books(clone=None):
    v = Voice(out_loud=False)
    v.out_loud = True
    v.clone = clone
    v.books = {name: Books(name) for name in ("spotter", "engineer", "clone")}
    return v


def test_the_spotter_book_for_spotter_lines_max_when_the_clone_is_up_else_the_standard_engineer():
    assert voice_with_books().from_bank("Slow car ahead.", spotter=True) == (b"spotter:Slow car ahead.", "standard")
    assert voice_with_books().from_bank("Clear.") == (b"engineer:Clear.", "standard")
    assert voice_with_books(UpClone()).from_bank("Clear.") == (b"clone:Clear.", "clone")
    assert Voice(out_loud=False).from_bank("Clear.") == (None, None)


class BankedVoice(FakeVoice):
    def __init__(self, has):
        super().__init__()
        self.has = has
        self.rendered = []
        self.out_loud = True

    def from_bank(self, text, spotter=False):
        return (b"RIFF-banked", "standard") if self.has else (None, None)

    def render(self, text, voice=None, mood="dry"):
        self.rendered.append(text)
        return text


def cooked(has):
    v = BankedVoice(has)
    desk = RadioDesk(v, FakePersona("unused"), Budget(), clean=False)
    call = Call("spotter", "SLOW_CAR_AHEAD", 1.0, SPOTTER, 3.0, "Slow car ahead, Arnage.",
                template="Slow car ahead, Arnage.", immediate=True, voice="spotter")
    result = desk.cook(call)
    desk.stop()
    return v, call, result


def test_the_desk_plays_a_banked_line_without_rendering_it():
    v, call, result = cooked(has=True)
    assert result["audio"] == b"RIFF-banked" and v.rendered == []
    assert call.facts["banked"] is True


def test_a_line_not_in_the_bank_is_rendered_live_as_before():
    v, call, result = cooked(has=False)
    assert v.rendered == ["Slow car ahead, Arnage."] and "banked" not in call.facts


def test_max_says_each_sentence_in_the_mood_of_the_call_it_belongs_to():
    from voice import mood_of
    moods = phrasebook.units()["engineer"]
    assert "Clear." not in moods                      # dropped from praise (his call, 25 Sep)
    for text in ("Next one, 8 tenths.", "WHAT A FUCKING MOVE! Get in there!", "Great tow."):
        assert moods[text] == mood_of("PASS_PRAISE")
    assert moods["Cover the inside into Arnage."] == mood_of("STICK_IT")
    assert moods["This fight's costing you 1.4 seconds a lap."] == mood_of("FIGHT_COST")
