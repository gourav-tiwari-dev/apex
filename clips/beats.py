"""The edit of a short: which stretches of the recording go in (the beats), and when
each word Apex said is heard in it.

Apex's own words come from apex.db; Whisper only says when they were heard (it
mishears names, so its text is never shown). The story: the called shot (an attack
plan, the pass, the praise), his question and its answer, a team-memory line.
make_short.py cuts and renders these beats; captions.py draws on top of them."""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from clips.find_moments import norm_words

SLURS = re.compile(r"\b(cunt\w*|retard\w*)\b", re.I)
SETUP_KINDS = ("ATTACK_PLAN", "STICK_IT")  # the calls that set a pass up
MEMORY_KINDS = ("CORNER_HABIT", "LAP_ONE_HABIT")
SENTENCE_ENDS = (".", "!", "?")

# a race data row (clips/race_data.py): sim time, gap ahead, gap behind, place, speed
SIM = 0
GAP_AHEAD = 1
PLACE = 3
SPEED = 4

# timing a line's words
SECONDS_PER_WORD = 0.34  # a line Whisper did not hear: about this long a word
HEARD_SLACK_S = 0.05  # Whisper's words may start or end a touch outside the line
DROPPED_WORD_S = 0.3  # a dropped last word: this long after the word before it
SHORTEST_WORD_S = 0.05

# the edit: how each beat is cut
CALLED_SHOT_WINDOW_S = 45  # a call more than 45 s before its pass did not set it up
PRAISE_EARLY_S = 1  # the praise can land a second before the data sees the pass,
PRAISE_LATE_S = 30  # or up to 30 s after it (it waits for a quiet radio)
LEAD_IN_S = 0.3  # a beat opens just before its line
CALL_LONGEST_S = 6.0  # the call is cut at the last sentence end inside 6 s
CALL_TAIL_S = 0.3
PRAISE_LONGEST_S = 3.0
PRAISE_TAIL_S = 0.5
PASS_BEFORE_S = 5.0  # the pass beat: the run-up to the place changing,
PASS_AFTER_S = 1.2  # and a moment after it
JOIN_UNDER_S = 1.0  # pieces closer than a second play straight through
PASS_BEFORE_PRAISE_S = 3.0  # without race data: the pass happens just before the praise
HOOK_JOIN_UNDER_S = 1.5
ANSWER_WITHIN_S = 8  # an answer must start within 8 s of his question
ASK_LONGEST_S = 11.0  # the question and its answer, cut at a sentence end inside 11 s
JUMP_CUT_OVER_S = 1.2  # a longer wait for the answer is cut out
QUESTION_LEAD_IN_S = 0.4
QUESTION_TAIL_S = 0.3
ANSWER_TAIL_S = 0.6
MEMORY_LEAD_IN_S = 0.5
MEMORY_TAIL_S = 0.9


@dataclass
class Beat:
    """One stretch of the recording that goes into the short."""

    kind: str  # "hook", "pass", "ask", "answer" or "memory"
    start: float  # seconds into the recording
    end: float
    lines: list  # the radio lines captioned in it
    question: dict | None = None  # an "ask" beat: his question
    overtake: dict | None = (
        None  # a "pass" beat: its sim time, his place before and after
    )


class VideoClock:
    """Sim time -> time in the recording. Every radio line Whisper heard pins one sim
    time to one moment of the video; a sim time takes the offset of the nearest pin."""

    def __init__(self, radio):
        self.pins = []  # (sim time, video time - sim time)
        for line in radio:
            if line.get("matched"):
                offset = line["video_t"] - line["sim_time"]
                self.pins.append((line["sim_time"], offset))
        self.pins.sort()

    def video_time(self, sim):
        """Where sim time `sim` is in the recording."""
        nearest = self.pins[0]
        for pin in self.pins:
            if abs(pin[0] - sim) < abs(nearest[0] - sim):
                nearest = pin
        return sim + nearest[1]


# ---------- the words of a line, in time ----------
def usable(line):
    """A line worth showing: it has words, and no slur."""
    if not line.get("line"):
        return False
    return SLURS.search(line["line"]) is None


def line_end(line):
    """When a line ends in the recording: its last timed word, or a guess from its
    length."""
    if line.get("timed"):
        return line["timed"][-1]["end"]
    return line["video_t"] + SECONDS_PER_WORD * len(line["line"].split())


def timed_line_words(line, words):
    """Apex's exact words (from apex.db), each given a time from Whisper's words for
    that stretch. Whisper mishears names ("toe" for tow, "S's" for Esses), so its text
    is never shown."""
    exact = line["line"].split()
    if not line.get("matched"):
        end = line["video_t"] + SECONDS_PER_WORD * len(exact)
        return spread_words(line["line"], line["video_t"], end)
    keys = []
    for word in exact:
        key = " ".join(norm_words(word))
        if not key:
            key = word
        keys.append(key)
    times = times_from_whisper(keys, heard_during(line, words))
    fill_dropped_words(times, line)
    timed = []
    for word, (start, end) in zip(exact, times):
        timed.append({"raw": word, "start": start, "end": end})
    return timed


def heard_during(line, words):
    """Whisper's words inside the line's stretch of the recording."""
    heard = []
    for word in words:
        if word["start"] < line["video_t"] - HEARD_SLACK_S:
            continue
        if word["end"] <= line["video_end"] + HEARD_SLACK_S:
            heard.append(word)
    return heard


def times_from_whisper(keys, heard):
    """(start, end) for each of Apex's words that lines up with a word Whisper heard,
    None for the words Whisper dropped. Where the two disagree on a stretch ("toe" for
    tow), that stretch's times are shared out in order."""
    times = [None] * len(keys)
    heard_keys = []
    for word in heard:
        heard_keys.append(word["w"])
    matcher = SequenceMatcher(None, keys, heard_keys, autojunk=False)
    for tag, exact_from, exact_to, heard_from, heard_to in matcher.get_opcodes():
        if tag not in ("equal", "replace") or heard_to <= heard_from:
            continue
        exact_count = exact_to - exact_from
        heard_count = heard_to - heard_from
        for position in range(exact_count):
            share = position * heard_count // exact_count
            heard_word = heard[heard_from + min(heard_count - 1, share)]
            times[exact_from + position] = (heard_word["start"], heard_word["end"])
    return times


def fill_dropped_words(times, line):
    """The words Whisper dropped are squeezed in between their neighbours."""
    for index in range(len(times)):
        if times[index] is not None:
            continue
        before = time_before(times, index, (line["video_t"], line["video_t"]))
        guess = before[1] + DROPPED_WORD_S
        after = time_after(times, index, (guess, guess))
        times[index] = (before[1], max(before[1] + SHORTEST_WORD_S, after[0]))


def time_before(times, index, default):
    """The nearest word time before this word, or default."""
    for earlier in range(index - 1, -1, -1):
        if times[earlier] is not None:
            return times[earlier]
    return default


def time_after(times, index, default):
    """The nearest word time after this word, or default."""
    for later in range(index + 1, len(times)):
        if times[later] is not None:
            return times[later]
    return default


def cut_at_sentence(line, longest):
    """End time of the last full sentence that fits in `longest` seconds (at least one
    sentence)."""
    timed = line["timed"]
    start = timed[0]["start"]
    best = None
    for word in timed:
        if not word["raw"].endswith(SENTENCE_ENDS):
            continue
        if best is None or word["end"] - start <= longest:
            best = word["end"]
        else:
            break
    if best is None:
        return timed[-1]["end"]
    return best


# ---------- picking the beats ----------
def place_at(race, sim):
    """His place at sim time `sim`, from the race data (clips/race_data.py)."""
    place = None
    for row in race:
        if row[SIM] > sim:
            break
        place = row[PLACE]
    return place


def passes(race):
    """Every time his place got better: (sim time, place before, place after). The
    praise can come 15 s after the pass (it waits for a quiet moment on the radio:
    session 59, pass at ~359 s, praise at 373.8 s), so the clip cuts to the moment the
    data says it happened."""
    found = []
    last = None
    for row in race:
        place = row[PLACE]
        if place is None:
            continue
        if last is not None and place < last:
            found.append((row[SIM], last, place))
        last = place
    return found


def pick_hook_measured(radio, race):
    """The called shot from the race data: the attack plan, the real pass (gap ticking
    down, the place changing), the praise. The first pass that has both a call and a
    praise."""
    clock = VideoClock(radio)
    for pass_sim, before, after in passes(race):
        call = setup_call_for(radio, pass_sim)
        praise = praise_for(radio, pass_sim)
        if call is None or praise is None:
            continue
        overtake = {"pass_sim": pass_sim, "before": before, "after": after}
        return join_close_beats(measured_hook_beats(call, praise, overtake, clock))
    return []


def setup_call_for(radio, pass_sim):
    """The call that set this pass up: the last attack plan in the 45 s before it, or
    the last "stick it" when there was no plan; None when there was neither."""
    last_plan = None
    last_stick_it = None
    for line in radio:
        if line["kind"] not in SETUP_KINDS or not usable(line):
            continue
        if not pass_sim - CALLED_SHOT_WINDOW_S <= line["sim_time"] < pass_sim:
            continue
        if line["kind"] == "ATTACK_PLAN":
            last_plan = line
        else:
            last_stick_it = line
    if last_plan is not None:
        return last_plan
    return last_stick_it


def praise_for(radio, pass_sim):
    """The first praise from just before the pass to 30 s after it."""
    for line in radio:
        if line["kind"] != "PASS_PRAISE" or not usable(line):
            continue
        if pass_sim - PRAISE_EARLY_S <= line["sim_time"] <= pass_sim + PRAISE_LATE_S:
            return line
    return None


def measured_hook_beats(call, praise, overtake, clock):
    """The called shot as three beats: the call, the pass on screen, the praise."""
    pass_video = clock.video_time(overtake["pass_sim"])
    call_end = cut_at_sentence(call, CALL_LONGEST_S) + CALL_TAIL_S
    praise_end = cut_at_sentence(praise, PRAISE_LONGEST_S) + PRAISE_TAIL_S
    return [
        Beat("hook", call["video_t"] - LEAD_IN_S, call_end, [call]),
        Beat(
            "pass",
            pass_video - PASS_BEFORE_S,
            pass_video + PASS_AFTER_S,
            [],
            overtake=overtake,
        ),
        Beat("hook", praise["video_t"] - LEAD_IN_S, praise_end, [praise]),
    ]


def join_close_beats(beats):
    """Beats closer than a second play straight through as one."""
    joined = [beats[0]]
    for beat in beats[1:]:
        last = joined[-1]
        if beat.start - last.end < JOIN_UNDER_S:
            joined[-1] = merged(last, beat)
        else:
            joined.append(beat)
    return joined


def merged(first, second):
    """Two beats played as one; one that holds the pass is a "pass" beat."""
    overtake = second.overtake
    if overtake is None:
        overtake = first.overtake
    kind = first.kind
    if overtake:
        kind = "pass"
    end = max(first.end, second.end)
    lines = first.lines + second.lines
    return Beat(kind, first.start, end, lines, overtake=overtake)


def pick_hook(radio):
    """Without race data: the attack plan (or "stick it") followed soonest by a praise
    for the pass, within 45 s."""
    best = None
    best_gap = None
    for index, call in enumerate(radio):
        if call["kind"] not in SETUP_KINDS or not usable(call):
            continue
        found = praise_after(radio, index, call)
        if found is None:
            continue
        praise, gap = found
        if best is None or gap < best_gap:
            best = (call, praise)
            best_gap = gap
    if best is None:
        return []
    return hook_beats(best[0], best[1])


def praise_after(radio, index, call):
    """(praise, seconds from the end of the call) for the first praise within 45 s, or
    None."""
    for later in radio[index + 1 :]:
        gap = later["video_t"] - line_end(call)
        if gap > CALLED_SHOT_WINDOW_S:
            return None
        if later["kind"] == "PASS_PRAISE" and usable(later):
            return later, gap
    return None


def hook_beats(call, praise):
    """The call and the praise: one beat when they are close, else two."""
    first_start = call["video_t"] - LEAD_IN_S
    first_end = cut_at_sentence(call, CALL_LONGEST_S) + CALL_TAIL_S
    second_start = praise["video_t"] - PASS_BEFORE_PRAISE_S
    second_end = cut_at_sentence(praise, PRAISE_LONGEST_S) + PRAISE_TAIL_S
    if second_start - first_end < HOOK_JOIN_UNDER_S:
        return [Beat("hook", first_start, second_end, [call, praise])]
    return [
        Beat("hook", first_start, first_end, [call]),
        Beat("hook", second_start, second_end, [praise]),
    ]


def pick_ask(radio, questions):
    """His first question that got an answer within 8 s, and the answer."""
    for question in questions:
        for line in radio:
            if is_answer_to(line, question):
                return ask_beats(question, line)
    return []


def is_answer_to(line, question):
    """An answer to this question: said by Apex, heard, and started within 8 s of it."""
    if not line["kind"].startswith("ANSWER") or line["kind"] == "ANSWER_UNHEARD":
        return False
    if not usable(line):
        return False
    wait = line["video_t"] - question["video_end"]
    return 0 <= wait <= ANSWER_WITHIN_S


def ask_beats(question, answer):
    """The question and its answer; a long wait for the answer is jump-cut."""
    asked_for = answer["video_t"] - question["video_t"]
    end = cut_at_sentence(answer, ASK_LONGEST_S - asked_for)
    question_start = question["video_t"] - QUESTION_LEAD_IN_S
    if answer["video_t"] - question["video_end"] > JUMP_CUT_OVER_S:
        question_end = question["video_end"] + QUESTION_TAIL_S
        answer_start = answer["video_t"] - LEAD_IN_S
        return [
            Beat("ask", question_start, question_end, [], question=question),
            Beat("answer", answer_start, end + ANSWER_TAIL_S, [answer]),
        ]
    return [
        Beat("ask", question_start, end + ANSWER_TAIL_S, [answer], question=question)
    ]


def pick_memory(radio):
    """The first team-memory line Whisper heard ("Indianapolis next...")."""
    for line in radio:
        if line["kind"] not in MEMORY_KINDS or not usable(line):
            continue
        if not line.get("matched"):
            continue
        start = line["video_t"] - MEMORY_LEAD_IN_S
        return [Beat("memory", start, line_end(line) + MEMORY_TAIL_S, [line])]
    return []


def spread_words(text, start, end):
    """A line's words spread evenly from start to end."""
    words = text.split()
    step = (end - start) / max(len(words), 1)
    spread = []
    for index, word in enumerate(words):
        word_start = start + index * step
        word_end = start + (index + 1) * step
        spread.append({"raw": word, "start": word_start, "end": word_end})
    return spread
