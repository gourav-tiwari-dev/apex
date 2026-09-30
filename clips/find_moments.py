"""Find every Apex radio call inside a race recording.

    python -m clips.find_moments RECORDING.mp4 [--session ID] [--out moments.json]

How it works:
1. faster-whisper (small.en, GPU) transcribes the recording with word timings. With the NVIDIA
   App's "separate both tracks", audio stream 0 is the game + Apex and stream 1 is the mic; both
   are transcribed, and mic speech becomes the driver's questions.
2. Each spoken line in apex.db's radio_log is searched for in the transcript (word-window fuzzy
   match). A match pins that line's sim time to a video time.
3. Only matches that keep time order are trusted (a line said later in the race must sit later
   in the video). Lines Whisper missed get a video time interpolated from their neighbours, so
   pauses in the game don't break the timing.
If --session is not given, the session whose lines match best is picked.
"""
import argparse, bisect, json, os, re, sqlite3, subprocess
from difflib import SequenceMatcher

APEX_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apex.db")
NUMBERS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
           "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12"}
MIN_RATIO = 0.72
SKIP_KINDS = {"VERDICT", "DEBRIEF", "DEBRIEF_TC_UP", "DEBRIEF_INCIDENTS"}


def norm_words(text):
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower().replace("'", ""))
    return [NUMBERS.get(w, w) for w in words]


def audio_streams(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", video], capture_output=True, text=True, check=True).stdout
    return len([x for x in out.split() if x.strip()])


def transcribe(video, stream, model):
    wav = os.path.splitext(video)[0] + f".a{stream}.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-map", f"0:a:{stream}", "-ac", "1", "-ar", "16000", wav],
                   check=True)
    segments, _ = model.transcribe(wav, word_timestamps=True, vad_filter=True, language="en")
    words = []
    for seg in segments:
        for w in seg.words:
            for token in norm_words(w.word):
                words.append({"w": token, "start": round(w.start, 3), "end": round(w.end, 3), "raw": w.word.strip()})
    return words


def spoken_lines(conn, session_id):
    rows = conn.execute(
        "SELECT id, sim_time, seat, kind, line FROM radio_log WHERE session_id = ? AND status = 'spoken' "
        "AND line IS NOT NULL AND sim_time > 0 ORDER BY sim_time", (session_id,)).fetchall()
    return [{"id": r[0], "sim_time": r[1], "seat": r[2], "kind": r[3], "line": r[4]} for r in rows
            if r[3] not in SKIP_KINDS]


def best_window(tokens, words, index):
    """The transcript window that looks most like this line: (ratio, first word, last word)."""
    n = len(tokens)
    starts = set()
    for t in tokens[:3]:
        for i in index.get(t, []):
            for back in range(3):
                if i - back >= 0:
                    starts.add(i - back)
    best = (0.0, None, None)
    for s in starts:
        for size in (n - 1, n, n + 1):
            if size < 2 or s + size > len(words):
                continue
            window = [w["w"] for w in words[s:s + size]]
            r = SequenceMatcher(None, tokens, window, autojunk=False).ratio()
            if r > best[0]:
                best = (r, s, s + size - 1)
    return best


def match_session(lines, words):
    index = {}
    for i, w in enumerate(words):
        index.setdefault(w["w"], []).append(i)
    hits = []
    for ln in lines:
        tokens = norm_words(ln["line"])
        if len(tokens) < 3:
            continue
        ratio, a, b = best_window(tokens, words, index)
        if ratio >= MIN_RATIO:
            hits.append({**ln, "ratio": round(ratio, 3), "w0": a, "w1": b,
                         "video_t": words[a]["start"], "video_end": words[b]["end"]})
    return keep_in_order(hits)


def keep_in_order(hits):
    """Longest run of matches whose video times rise with their sim times."""
    hits = sorted(hits, key=lambda h: h["sim_time"])
    tails, tail_idx, prev = [], [], [None] * len(hits)
    for i, h in enumerate(hits):
        k = bisect.bisect_left(tails, h["video_t"])
        if k > 0:
            prev[i] = tail_idx[k - 1]
        if k == len(tails):
            tails.append(h["video_t"]); tail_idx.append(i)
        else:
            tails[k] = h["video_t"]; tail_idx[k] = i
    chain, i = [], tail_idx[-1] if tail_idx else None
    while i is not None:
        chain.append(hits[i]); i = prev[i]
    return chain[::-1]


def place_all(lines, hits):
    """Every line gets a video time: its own match, or interpolated between matched neighbours."""
    if not hits:
        return []
    sims = [h["sim_time"] for h in hits]
    by_id = {h["id"]: h for h in hits}
    placed = []
    for ln in lines:
        if ln["id"] in by_id:
            placed.append({**by_id[ln["id"]], "matched": True}); continue
        k = bisect.bisect_left(sims, ln["sim_time"])
        lo, hi = hits[max(k - 1, 0)], hits[min(k, len(hits) - 1)]
        offset = lo["video_t"] - lo["sim_time"] if abs(ln["sim_time"] - lo["sim_time"]) <= abs(hi["sim_time"] - ln["sim_time"]) \
            else hi["video_t"] - hi["sim_time"]
        placed.append({**ln, "matched": False, "video_t": round(ln["sim_time"] + offset, 3)})
    return placed


def questions(words, radio_hits, from_mic):
    """Speech that is not Apex: the driver's push-to-talk questions."""
    used = set()
    for h in radio_hits:
        used.update(range(h["w0"], h["w1"] + 1))
    free = [w for i, w in enumerate(words) if from_mic or i not in used]
    groups, cur = [], []
    for w in free:
        if cur and w["start"] - cur[-1]["end"] > 0.8:
            groups.append(cur); cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return [{"video_t": g[0]["start"], "video_end": g[-1]["end"], "text": " ".join(x["raw"] for x in g)}
            for g in groups if len(g) >= 2]


def find(video, db_path, session=None, out=None):
    """Writes VIDEO.moments.json and returns its path."""
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel("small.en", device="cuda", compute_type="float16")
    except Exception:
        model = WhisperModel("small.en", device="cpu", compute_type="int8")

    streams = audio_streams(video)
    game_words = transcribe(video, 0, model)
    mic_words = transcribe(video, 1, model) if streams > 1 else None

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    if session:
        candidates = [session]
    else:
        candidates = [r[0] for r in conn.execute(
            "SELECT session_id FROM radio_log WHERE status='spoken' GROUP BY session_id HAVING COUNT(*) >= 5")]
    best = (None, [], [])
    for sid in candidates:
        lines = spoken_lines(conn, sid)
        hits = match_session(lines, game_words)
        if len(hits) > len(best[1]):
            best = (sid, hits, lines)
    conn.close()
    sid, hits, lines = best
    placed = place_all(lines, hits)
    asked = questions(mic_words, [], True) if mic_words is not None else questions(game_words, hits, False)
    result = {"video": os.path.abspath(video), "session": sid, "matched": len(hits), "lines": len(lines),
              "radio": [{k: v for k, v in p.items() if k not in ("w0", "w1")} for p in placed],
              "words": game_words, "questions": asked}
    path = out or os.path.splitext(video)[0] + ".moments.json"
    json.dump(result, open(path, "w"), indent=1)
    print(f"[clips] session {sid}: {len(hits)} of {len(lines)} radio lines found by ear; "
          f"{len(asked)} questions; wrote {path}")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--session", type=int)
    ap.add_argument("--db", default=APEX_DB)
    ap.add_argument("--out")
    args = ap.parse_args()
    find(args.video, args.db, args.session, args.out)


if __name__ == "__main__":
    main()
