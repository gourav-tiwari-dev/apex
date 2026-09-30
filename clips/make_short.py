"""Turn a race recording + its moments.json into a vertical product short.

    python -m clips.make_short RECORDING.mp4 [--moments X.moments.json] [--out short.mp4]
                               [--max 30] [--ending launch|driver]

The story, in order (each beat is skipped if the race didn't have it):
  1. HOOK - the called shot: an attack plan, then the pass, then the praise.
  2. ASK  - the driver's push-to-talk question and Apex's answer.
  3. MEMORY - "Indianapolis next. You've had trouble there."
  4. The ending: "launch" = COMING SOON + the early-access card (our own marketing);
     "driver" = one short "radio by Apex" card on the driver's own auto-clip.
Captions are team-radio style: who is talking (APEX · RACECRAFT or YOU) and the words, a few at a
time, timed from Whisper's word timings. Swear words are bleeped in sound and starred in text; a
line with a slur is never used. The picture is a 9:16 crop of the cockpit view.
"""
import argparse, json, os, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
W, H, FPS = 1080, 1920, 30
SWEARS = re.compile(r"\b(fuck\w*|shit\w*|bollocks|bastard\w*|twat\w*|arse\w*|dick\w*|wank\w*)\b", re.I)
SLURS = re.compile(r"\b(cunt\w*|retard\w*)\b", re.I)
SEAT_NAMES = {"racecraft": "RACECRAFT", "race_engineer": "RACE ENGINEER", "strategist": "STRATEGY",
              "spotter": "SPOTTER", "memory": "TEAM MEMORY", "performance": "PERFORMANCE", "setup": "SETUP"}
ORANGE, WHITE, INK = "&H001F5BFF", "&H00F4F1EE", "&H000C0907"     # ASS colours are &HAABBGGRR
COMING_SOON = ["Auto-clips of your races", "More voices", "iRacing support"]
CTA_TOP = "APEX"
CTA_MID = "AI race engineer\\Nfor Le Mans Ultimate"
CTA_BUTTON = "FREE EARLY ACCESS · LINK IN BIO"


# ---------- picking the beats ----------
def usable(line):
    return line.get("line") and not SLURS.search(line["line"])


def line_end(line):
    return line["timed"][-1]["end"] if line.get("timed") else line["video_t"] + 0.34 * len(line["line"].split())


def timed_line_words(line, words):
    """Apex's exact words (from apex.db), each given a time from Whisper's words for that stretch.
    Whisper mishears names ("toe" for tow, "S's" for Esses), so its text is never shown."""
    from difflib import SequenceMatcher
    from clips.find_moments import norm_words
    exact = line["line"].split()
    if not line.get("matched"):
        return spread_words(line["line"], line["video_t"], line["video_t"] + 0.34 * len(exact))
    heard = [w for w in words if w["start"] >= line["video_t"] - 0.05 and w["end"] <= line["video_end"] + 0.05]
    keys = [" ".join(norm_words(w)) or w for w in exact]
    ops = SequenceMatcher(None, keys, [w["w"] for w in heard], autojunk=False).get_opcodes()
    timed = [None] * len(exact)
    for tag, i1, i2, j1, j2 in ops:
        if tag in ("equal", "replace") and j2 > j1:
            for k in range(i1, i2):
                j = j1 + min(j2 - j1 - 1, (k - i1) * (j2 - j1) // max(i2 - i1, 1))
                timed[k] = (heard[j]["start"], heard[j]["end"])
    for k in range(len(exact)):                       # words Whisper dropped: squeeze between neighbours
        if timed[k] is None:
            prev = next((timed[x] for x in range(k - 1, -1, -1) if timed[x]), (line["video_t"], line["video_t"]))
            nxt = next((timed[x] for x in range(k + 1, len(exact)) if timed[x]), (prev[1] + 0.3, prev[1] + 0.3))
            timed[k] = (prev[1], max(prev[1] + 0.05, nxt[0]))
    return [{"raw": w, "start": a, "end": b} for w, (a, b) in zip(exact, timed)]


def cut_at_sentence(line, max_len):
    """End time of the last full sentence that fits in max_len seconds (at least one sentence)."""
    tw = line["timed"]
    start, best = tw[0]["start"], None
    for w in tw:
        if w["raw"].endswith((".", "!", "?")):
            if best is None or w["end"] - start <= max_len:
                best = w["end"]
            else:
                break
    return best if best is not None else tw[-1]["end"]


def pick_hook(radio):
    """The attack plan that is followed soonest by a completed pass."""
    best = None
    for i, a in enumerate(radio):
        if a["kind"] not in ("ATTACK_PLAN", "STICK_IT") or not usable(a):
            continue
        for p in radio[i + 1:]:
            gap = p["video_t"] - line_end(a)
            if gap > 45:
                break
            if p["kind"] == "PASS_PRAISE" and usable(p):
                if best is None or gap < best[2]:
                    best = (a, p, gap)
                break
    if not best:
        return []
    a, p, gap = best
    first = (a["video_t"] - 0.3, cut_at_sentence(a, 6.0) + 0.3)
    second = (p["video_t"] - 3.0, cut_at_sentence(p, 3.0) + 0.5)   # the pass happens just before the praise
    if second[0] - first[1] < 1.5:
        return [("hook", first[0], second[1], [a, p])]
    return [("hook", first[0], first[1], [a]), ("hook", second[0], second[1], [p])]


def pick_ask(radio, questions):
    for q in questions:
        for r in radio:
            if r["kind"].startswith("ANSWER") and r["kind"] != "ANSWER_UNHEARD" and usable(r) \
                    and 0 <= r["video_t"] - q["video_end"] <= 8:
                end = cut_at_sentence(r, 11.0 - (r["video_t"] - q["video_t"]))
                if r["video_t"] - q["video_end"] > 1.2:        # jump cut over the wait for the answer
                    return [("ask", q["video_t"] - 0.4, q["video_end"] + 0.3, [], q),
                            ("answer", r["video_t"] - 0.3, end + 0.6, [r])]
                return [("ask", q["video_t"] - 0.4, end + 0.6, [r], q)]
    return []


def pick_memory(radio):
    for r in radio:
        if r["kind"] in ("CORNER_HABIT", "LAP_ONE_HABIT") and usable(r) and r.get("matched"):
            return [("memory", r["video_t"] - 0.5, line_end(r) + 0.9, [r])]
    return []


# ---------- captions ----------
def ts(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def spread_words(text, start, end):
    raw = text.split()
    step = (end - start) / max(len(raw), 1)
    return [{"raw": w, "start": start + i * step, "end": start + (i + 1) * step} for i, w in enumerate(raw)]


def chunks(words, size=3):
    """About 3 words at a time, breaking at punctuation. A word that would flash up alone
    ("2.", "there.", "Esses,") joins the chunk before it (or after it, if it opens the line)."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends = w["raw"].endswith((".", "?", "!", ","))
        if not ends and len(cur) == size and nxt and nxt["raw"].endswith((".", "?", "!")):
            continue
        if len(cur) >= size or ends:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    joined = []
    for chunk in out:
        if len(chunk) == 1 and joined and len(joined[-1]) <= size + 1:
            joined[-1] = joined[-1] + chunk
        else:
            joined.append(chunk)
    if len(joined) > 1 and len(joined[0]) == 1:
        joined[1] = joined[0] + joined[1]
        joined.pop(0)
    return joined


def star(word):
    return SWEARS.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), word)


class Captions:
    def __init__(self):
        self.events, self.bleeps = [], []

    def radio(self, line, shift, seg_start, seg_end):
        """One radio line: a seat tag for the whole line, the words 3 at a time."""
        words = [w for w in line["timed"] if w["start"] < seg_end - 0.1]
        a = max(words[0]["start"], seg_start)
        b = min(words[-1]["end"] + 0.4, seg_end)
        tag = "APEX · " + SEAT_NAMES.get(line["seat"], line["seat"].upper())
        self.events.append((a + shift, b + shift, "Tag", tag))
        groups = chunks(words)
        for i, g in enumerate(groups):
            ga = max(g[0]["start"], seg_start)
            gb = groups[i + 1][0]["start"] if i + 1 < len(groups) else b
            gb = min(gb, seg_end)
            if gb <= ga:
                continue
            self.events.append((ga + shift, gb + shift, "Radio", " ".join(star(w["raw"]) for w in g)))
        for w in words:
            if SWEARS.search(w["raw"]) and seg_start <= w["start"] < seg_end:
                self.bleeps.append((w["start"] + shift, min(w["end"], seg_end) + shift))

    def question(self, q, shift):
        self.events.append((q["video_t"] + shift, q["video_end"] + 0.8 + shift, "Tag", "YOU · PUSH-TO-TALK"))
        self.events.append((q["video_t"] + shift, q["video_end"] + 0.8 + shift, "Ask", q["text"]))

    def card(self, a, b, style, text):
        self.events.append((a, b, style, text))

    def write(self, path):
        head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Tag,Bahnschrift,40,{ORANGE},{ORANGE},{INK},&H99000000,1,0,0,0,100,100,4,0,3,10,0,2,80,80,1000,1
Style: Radio,Bahnschrift,92,{WHITE},{WHITE},{INK},&H00000000,1,0,0,0,100,100,0,0,1,7,3,2,70,70,820,1
Style: Ask,Bahnschrift,76,{INK},{INK},{WHITE},&H00F4F1EE,1,0,0,0,100,100,0,0,3,22,0,2,90,90,820,1
Style: Name,Bahnschrift,70,{WHITE},{WHITE},{INK},&H00000000,1,0,0,0,100,100,2,0,1,6,2,8,80,80,380,1
Style: CardBig,Bahnschrift,230,{WHITE},{WHITE},{INK},&H00000000,1,0,0,0,100,100,6,0,1,0,0,5,60,60,0,1
Style: CardText,Bahnschrift,68,{WHITE},{WHITE},{INK},&H00000000,1,0,0,0,100,100,0,0,1,0,0,5,80,80,0,1
Style: CardHead,Bahnschrift,56,{ORANGE},{ORANGE},{INK},&H00000000,1,0,0,0,100,100,8,0,1,0,0,5,80,80,0,1
Style: Button,Bahnschrift,62,{INK},{INK},{ORANGE},&H001F5BFF,1,0,0,0,100,100,2,0,3,26,0,5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        body = []
        for a, b, style, text in sorted(self.events):
            fade = r"{\fad(120,80)}" if style in ("Tag", "Ask", "Name") else r"{\fad(60,0)}"
            body.append(f"Dialogue: 0,{ts(a)},{ts(b)},{style},,0,0,0,,{fade}{text}")
        open(path, "w", encoding="utf-8").write(head + "\n".join(body) + "\n")


# ---------- rendering ----------
def run(args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def audio_count(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", video], capture_output=True, text=True).stdout
    return len(out.split())


def render_segment(video, a, b, path, mics):
    crop = f"scale=-2:{H},crop={W}:{H},setsar=1,fps={FPS}"
    if mics > 1:
        audio = "[0:a:0][0:a:1]amix=inputs=2:duration=first:normalize=0,aresample=48000,aformat=channel_layouts=stereo[a]"
    else:
        audio = "[0:a:0]aresample=48000,aformat=channel_layouts=stereo[a]"
    run(["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", video, "-filter_complex", f"[0:v]{crop}[v];{audio}",
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", path])


def render_card_bed(video, at, dur, path):
    """A blurred, darkened stretch of the race behind the end cards."""
    run(["-ss", f"{at:.3f}", "-t", f"{dur:.3f}", "-i", video, "-f", "lavfi", "-t", f"{dur:.3f}", "-i",
         "anullsrc=r=48000:cl=stereo", "-filter_complex",
         f"[0:v]scale=-2:{H},crop={W}:{H},setsar=1,fps={FPS},gblur=sigma=28,eq=saturation=0.35,colorchannelmixer=rr=0.5:gg=0.5:bb=0.5[v]",
         "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-shortest", path])


def make(video, moments_path, out=None, max_s=30.0, ending="launch"):
    """Builds the short and returns its path, or None when the race gave nothing to show."""
    video = os.path.abspath(video)
    m = json.load(open(moments_path))
    radio = sorted(m["radio"], key=lambda r: r["video_t"])
    for r in radio:
        r["timed"] = timed_line_words(r, m["words"])

    beats = pick_hook(radio) + pick_ask(radio, m["questions"]) + pick_memory(radio)
    if not beats:
        print("[clips] nothing worth a short in this race")
        return None
    if beats[0][0] != "hook":
        print("[clips] no called shot in this race - the short opens on the next best beat")
    cards_len = 2.4 + 3.2 if ending == "launch" else 2.6
    kept, total = [], 0.0
    for beat in beats:
        length = beat[2] - beat[1]
        if total + length + cards_len > max_s and kept:
            if beat[0] == "answer" and kept[-1][0] == "ask":      # never a question without its answer
                total -= kept[-1][2] - kept[-1][1]
                kept.pop()
            continue
        kept.append(beat); total += length

    work = os.path.splitext(video)[0] + "_work"
    os.makedirs(work, exist_ok=True)
    caps, parts, shift_base = Captions(), [], 0.0
    mics = audio_count(video)
    for i, beat in enumerate(kept):
        name, a, b, lines = beat[:4]
        part = os.path.join(work, f"part{i}.mp4")
        render_segment(video, a, b, part, mics)
        parts.append(part)
        shift = shift_base - a
        if name == "ask":
            caps.question(beat[4], shift)
        for ln in lines:
            caps.radio(ln, shift, a, b)
        if i == 0 and name == "hook":
            caps.card(0.0, 3.0, "Name", "MY AI ENGINEER\\N{\\c" + ORANGE + "}CALLED THIS PASS")
        if (i == 1 and kept[0][0] == "hook") or (i == 0 and name != "hook"):
            caps.card(shift_base + 0.1, shift_base + 2.8, "Name", "AI RACE ENGINEER\\N{\\c" + ORANGE + "}FOR LE MANS ULTIMATE")
        shift_base += b - a

    bed = os.path.join(work, "cards.mp4")
    render_card_bed(video, kept[0][1], cards_len, bed)
    parts.append(bed)
    t0 = shift_base
    # cards stay inside the safe zone: app UI covers the top 14% and bottom 35% of a vertical video
    if ending == "launch":
        caps.card(t0, t0 + 2.4, "CardHead", "{\\pos(540,560)}COMING SOON")
        caps.card(t0 + 0.1, t0 + 2.4, "CardText", "{\\pos(540,800)}" + "\\N".join(COMING_SOON))
        t1 = t0 + 2.4
        caps.card(t1, t1 + 3.2, "CardBig", "{\\pos(540,560)}" + CTA_TOP)
        caps.card(t1 + 0.15, t1 + 3.2, "CardText", "{\\pos(540,800)}" + CTA_MID)
        caps.card(t1 + 0.5, t1 + 3.2, "Button", "{\\pos(540,1010)}" + CTA_BUTTON)
        total = t1 + 3.2
    else:
        # the driver's own clip: every one he posts carries the name (the growth loop, feature 11)
        caps.card(t0, t0 + 2.6, "CardHead", "{\\pos(540,600)}RADIO BY")
        caps.card(t0 + 0.1, t0 + 2.6, "CardBig", "{\\pos(540,780)}" + CTA_TOP)
        caps.card(t0 + 0.3, t0 + 2.6, "CardText", "{\\pos(540,1000)}" + CTA_MID)
        total = t0 + 2.6

    listing = os.path.join(work, "parts.txt")
    open(listing, "w").write("".join(f"file '{p}'\n" for p in parts))
    joined = os.path.join(work, "joined.mp4")
    run(["-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", joined])

    ass = os.path.join(work, "captions.ass")
    caps.write(ass)
    ass_arg = ass.replace("\\", "/").replace(":", "\\:")
    if caps.bleeps:
        on = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in caps.bleeps)
        afilter = (f"[0:a]volume=0:enable='{on}'[g];sine=f=1000:r=48000:d={total:.2f},volume=0.18,"
                   f"volume=0:enable='not({on})',aformat=channel_layouts=stereo[s];[g][s]amix=inputs=2:normalize=0[a]")
    else:
        afilter = "[0:a]anull[a]"
    out = out or os.path.splitext(video)[0] + ".short.mp4"
    run(["-i", joined, "-filter_complex", f"[0:v]subtitles='{ass_arg}'[v];{afilter}", "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", out])
    print(f"[clips] beats: {[b[0] for b in kept]}; {total:.1f} s; {len(caps.bleeps)} bleeps; wrote {out}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--moments")
    ap.add_argument("--out")
    ap.add_argument("--max", type=float, default=30.0)
    ap.add_argument("--ending", choices=["launch", "driver"], default="launch")
    args = ap.parse_args()
    moments = args.moments or os.path.splitext(os.path.abspath(args.video))[0] + ".moments.json"
    make(args.video, moments, args.out, args.max, args.ending)


if __name__ == "__main__":
    main()
