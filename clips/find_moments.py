"""Find every Apex radio call inside a race recording.

    python -m clips.find_moments RECORDING.mp4 [--session ID] [--out moments.json]

How it works:
1. faster-whisper (small.en, GPU) transcribes the recording with word timings. With
   the NVIDIA App's "separate both tracks", audio stream 0 is the game + Apex and
   stream 1 is the mic; both are transcribed, and mic speech becomes the driver's
   questions.
2. Each spoken line in apex.db's radio_log is searched for in the transcript
   (word-window fuzzy match). A match pins that line's sim time to a video time.
3. Only matches that keep time order are trusted (a line said later in the race must sit
   later in the video). Lines Whisper missed get a video time from their nearest matched
   neighbour, so pauses in the game don't break the timing.
If --session is not given, the session whose lines match best is picked.
"""

import argparse
import bisect
import json
import os
import re
import sqlite3
import subprocess
from difflib import SequenceMatcher

APEX_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apex.db"
)
# Whisper writes small numbers as words, Apex as digits
NUMBERS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
}
MIN_RATIO = 0.72  # a window at least this much like the line is where it was said
SHORTEST_LINE = 3  # words; shorter lines match anything
SHORTEST_WINDOW = 2  # words
FIRST_WORDS = 3  # a window starts at one of the line's first 3 words,
WORDS_BACK = 3  # or up to 2 words before it
QUESTION_PAUSE_S = 0.8  # a longer pause ends what he was saying
SHORTEST_QUESTION = 2  # words
# said after the race, in the debrief: never in the recording
SKIP_KINDS = {"VERDICT", "DEBRIEF", "DEBRIEF_TC_UP", "DEBRIEF_INCIDENTS"}
SPOKEN_LINES = (
    "SELECT id, sim_time, seat, kind, line FROM radio_log WHERE session_id = ? "
    "AND status = 'spoken' AND line IS NOT NULL AND sim_time > 0 ORDER BY sim_time"
)
SESSIONS_WITH_RADIO = (
    "SELECT session_id FROM radio_log WHERE status='spoken' GROUP BY session_id "
    "HAVING COUNT(*) >= 5"
)


def norm_words(text):
    """Words as both sides can match them: lower case, no punctuation, numbers as
    digits."""
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower().replace("'", ""))
    normal = []
    for word in words:
        normal.append(NUMBERS.get(word, word))
    return normal


def audio_streams(video):
    """How many sound tracks the recording has."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            video,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return len(out.split())


def transcribe(video, stream, model):
    """Every word Whisper hears on one sound track, with its time."""
    wav = os.path.splitext(video)[0] + f".a{stream}.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            video,
            "-map",
            f"0:a:{stream}",
            "-ac",
            "1",
            "-ar",
            "16000",
            wav,
        ],
        check=True,
    )
    segments, _ = model.transcribe(
        wav, word_timestamps=True, vad_filter=True, language="en"
    )
    words = []
    for segment in segments:
        for heard in segment.words:
            for token in norm_words(heard.word):
                words.append(
                    {
                        "w": token,
                        "start": round(heard.start, 3),
                        "end": round(heard.end, 3),
                        "raw": heard.word.strip(),
                    }
                )
    return words


def spoken_lines(conn, session_id):
    """The session's spoken lines in sim-time order, without the debrief's."""
    lines = []
    for row_id, sim_time, seat, kind, line in conn.execute(SPOKEN_LINES, (session_id,)):
        if kind in SKIP_KINDS:
            continue
        lines.append(
            {
                "id": row_id,
                "sim_time": sim_time,
                "seat": seat,
                "kind": kind,
                "line": line,
            }
        )
    return lines


def best_window(tokens, words, index):
    """The transcript window that looks most like this line: (ratio, first word, last
    word)."""
    count = len(tokens)
    best = (0.0, None, None)
    for start in window_starts(tokens, index):
        for size in (count - 1, count, count + 1):
            if size < SHORTEST_WINDOW or start + size > len(words):
                continue
            ratio = window_ratio(tokens, words, start, size)
            if ratio > best[0]:
                best = (ratio, start, start + size - 1)
    return best


def window_starts(tokens, index):
    """Where a window may start: at, or up to 2 words before, any place the transcript
    has one of the line's first 3 words."""
    starts = set()
    for token in tokens[:FIRST_WORDS]:
        for position in index.get(token, []):
            add_starts_back_from(starts, position)
    return starts


def add_starts_back_from(starts, position):
    """This word and the 2 before it."""
    for back in range(WORDS_BACK):
        if position - back >= 0:
            starts.add(position - back)


def window_ratio(tokens, words, start, size):
    """How alike the line and this window of the transcript are, 0 to 1."""
    window = []
    for word in words[start : start + size]:
        window.append(word["w"])
    return SequenceMatcher(None, tokens, window, autojunk=False).ratio()


def match_session(lines, words):
    """Every line found in the transcript, kept only while they stay in time order."""
    index = {}
    for position, word in enumerate(words):
        index.setdefault(word["w"], []).append(position)
    hits = []
    for line in lines:
        tokens = norm_words(line["line"])
        if len(tokens) < SHORTEST_LINE:
            continue
        ratio, first, last = best_window(tokens, words, index)
        if ratio < MIN_RATIO:
            continue
        hit = dict(line)
        hit["ratio"] = round(ratio, 3)
        hit["w0"] = first
        hit["w1"] = last
        hit["video_t"] = words[first]["start"]
        hit["video_end"] = words[last]["end"]
        hits.append(hit)
    return keep_in_order(hits)


def sim_time_of(hit):
    """When a hit's line was said in the race."""
    return hit["sim_time"]


def keep_in_order(hits):
    """Longest run of matches whose video times rise with their sim times."""
    hits = sorted(hits, key=sim_time_of)
    tails = []  # tails[n]: the lowest video time that ends a rising run of n + 1 hits
    tail_hits = []  # which hit that is
    previous = [None] * len(hits)  # the hit before each one in its run
    for position, hit in enumerate(hits):
        run = bisect.bisect_left(tails, hit["video_t"])
        if run > 0:
            previous[position] = tail_hits[run - 1]
        if run == len(tails):
            tails.append(hit["video_t"])
            tail_hits.append(position)
        else:
            tails[run] = hit["video_t"]
            tail_hits[run] = position
    chain = []
    position = None
    if tail_hits:
        position = tail_hits[-1]
    while position is not None:
        chain.append(hits[position])
        position = previous[position]
    chain.reverse()
    return chain


def place_all(lines, hits):
    """Every line gets a video time: its own match, or the offset of its nearest matched
    neighbour."""
    if not hits:
        return []
    sims = []
    by_id = {}
    for hit in hits:
        sims.append(hit["sim_time"])
        by_id[hit["id"]] = hit
    placed = []
    for line in lines:
        if line["id"] in by_id:
            found = dict(by_id[line["id"]])
            found["matched"] = True
            placed.append(found)
            continue
        offset = nearest_offset(line, hits, sims)
        guessed = dict(line)
        guessed["matched"] = False
        guessed["video_t"] = round(line["sim_time"] + offset, 3)
        placed.append(guessed)
    return placed


def nearest_offset(line, hits, sims):
    """Video time minus sim time of the matched line nearest in sim time."""
    after_index = bisect.bisect_left(sims, line["sim_time"])
    before = hits[max(after_index - 1, 0)]
    after = hits[min(after_index, len(hits) - 1)]
    to_before = abs(line["sim_time"] - before["sim_time"])
    to_after = abs(after["sim_time"] - line["sim_time"])
    if to_before <= to_after:
        return before["video_t"] - before["sim_time"]
    return after["video_t"] - after["sim_time"]


def questions(words, radio_hits, from_mic):
    """Speech that is not Apex: the driver's push-to-talk questions."""
    used = set()
    for hit in radio_hits:
        used.update(range(hit["w0"], hit["w1"] + 1))
    free = []
    for position, word in enumerate(words):
        if from_mic or position not in used:
            free.append(word)
    asked = []
    for group in pauses_apart(free):
        if len(group) >= SHORTEST_QUESTION:
            asked.append(question_of(group))
    return asked


def pauses_apart(words):
    """Words grouped by what he said in one go: a pause over 0.8 s starts a group."""
    groups = []
    current = []
    for word in words:
        if current and word["start"] - current[-1]["end"] > QUESTION_PAUSE_S:
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


def question_of(group):
    """A question: when he started and stopped, and his words."""
    said = []
    for word in group:
        said.append(word["raw"])
    return {
        "video_t": group[0]["start"],
        "video_end": group[-1]["end"],
        "text": " ".join(said),
    }


def whisper_model():
    """small.en on the graphics card, or on the processor when that fails."""
    from faster_whisper import WhisperModel

    try:
        return WhisperModel("small.en", device="cuda", compute_type="float16")
    except Exception:
        return WhisperModel("small.en", device="cpu", compute_type="int8")


def best_session(db_path, session, game_words):
    """(session id, hits, lines) of the session whose lines match best: the one asked
    for, or any session with radio. (None, [], []) when nothing matches."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    if session:
        candidates = [session]
    else:
        candidates = []
        for row in conn.execute(SESSIONS_WITH_RADIO):
            candidates.append(row[0])
    best = (None, [], [])
    for session_id in candidates:
        lines = spoken_lines(conn, session_id)
        hits = match_session(lines, game_words)
        if len(hits) > len(best[1]):
            best = (session_id, hits, lines)
    conn.close()
    return best


def without_word_numbers(line):
    """A placed line as moments.json keeps it: w0 and w1 only matter while matching."""
    kept = {}
    for key, value in line.items():
        if key not in ("w0", "w1"):
            kept[key] = value
    return kept


def find(video, db_path, session=None, out=None):
    """Writes VIDEO.moments.json and returns its path."""
    model = whisper_model()
    streams = audio_streams(video)
    game_words = transcribe(video, 0, model)
    mic_words = None
    if streams > 1:
        mic_words = transcribe(video, 1, model)
    session_id, hits, lines = best_session(db_path, session, game_words)
    radio = []
    for line in place_all(lines, hits):
        radio.append(without_word_numbers(line))
    if mic_words is not None:
        asked = questions(mic_words, [], True)
    else:
        asked = questions(game_words, hits, False)
    result = {
        "video": os.path.abspath(video),
        "session": session_id,
        "matched": len(hits),
        "lines": len(lines),
        "radio": radio,
        "words": game_words,
        "questions": asked,
    }
    path = out
    if not path:
        path = os.path.splitext(video)[0] + ".moments.json"
    with open(path, "w") as f:
        json.dump(result, f, indent=1)
    print(
        f"[clips] session {session_id}: {len(hits)} of {len(lines)} radio lines "
        f"found by ear; {len(asked)} questions; wrote {path}"
    )
    return path


def main():
    """The command line: python -m clips.find_moments RECORDING.mp4 [options]."""
    parser = argparse.ArgumentParser()
    parser.add_argument("video")
    parser.add_argument("--session", type=int)
    parser.add_argument("--db", default=APEX_DB)
    parser.add_argument("--out")
    args = parser.parse_args()
    find(args.video, args.db, args.session, args.out)


if __name__ == "__main__":
    main()
