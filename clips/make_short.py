"""Turn a race recording + its moments.json into a vertical product short.

    python -m clips.make_short RECORDING.mp4 [--moments X.moments.json]
        [--out short.mp4] [--max 30] [--ending launch|driver] [--race-data X.json]

The story, in order (each beat is skipped if the race didn't have it):
  1. HOOK - the called shot: an attack plan, then the pass, then the praise.
  2. ASK  - the driver's push-to-talk question and Apex's answer.
  3. MEMORY - "Indianapolis next. You've had trouble there."
  4. The ending: "launch" = COMING SOON + the early-access card (our own marketing);
     "driver" = one short "radio by Apex" card on the driver's own auto-clip.
Captions are team-radio style: who is talking (APEX · RACECRAFT or YOU) and the words, a
few at a time, timed from Whisper's word timings. Swear words are bleeped in sound and
starred in text; a line with a slur is never used. The picture is a 9:16 crop of the
cockpit view.

clips/beats.py picks the beats and times every word, clips/captions.py draws on top of
them, and this file cuts the recording, lays the sound and renders the short.
"""

import argparse
import json
import os
import subprocess

from clips.beats import (
    VideoClock,
    pick_ask,
    pick_hook,
    pick_hook_measured,
    pick_memory,
    timed_line_words,
)
from clips.captions import HEIGHT, ORANGE, WIDTH, Captions
from clips.recorder import SHORT_ENCODERS, working_encoder

FRAMES_PER_SECOND = 30
HOOK_BEATS = ("hook", "pass")

# the titles on the beats
HOOK_TITLE_S = 3.0
NAME_CARD_IN_S = 0.1
NAME_CARD_OUT_S = 2.8

# the end cards
COMING_SOON = ["Auto-clips of your races", "More voices", "iRacing support"]
CTA_TOP = "APEX"
CTA_MID = "AI race engineer\\Nfor Le Mans Ultimate"
CTA_BUTTON = "FREE EARLY ACCESS · LINK IN BIO"
COMING_SOON_S = 2.4
CALL_TO_ACTION_S = 3.2
DRIVER_CARD_S = 2.6


# ---------- rendering ----------
def run(args):
    """ffmpeg, quiet unless something fails."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def audio_count(video):
    """How many sound tracks the recording has (2 when the mic has its own)."""
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
    ).stdout
    return len(out.split())


short_encoder = None  # looked up once, the first time a short is rendered


def video_encoder():
    """The best encoder this machine has for the finished short (clips/recorder.py lists
    them); libx264, the last one, when none of them answers."""
    global short_encoder
    if short_encoder is None:
        short_encoder = working_encoder(SHORT_ENCODERS)
    if short_encoder is None:
        short_encoder = SHORT_ENCODERS[-1]
    return short_encoder


def render_segment(video, start, end, path, mics):
    """One beat of the race, cropped to 9:16; the game and the mic mixed when there are
    two sound tracks."""
    crop = f"scale=-2:{HEIGHT},crop={WIDTH}:{HEIGHT},setsar=1,fps={FRAMES_PER_SECOND}"
    if mics > 1:
        audio = (
            "[0:a:0][0:a:1]amix=inputs=2:duration=first:normalize=0,aresample=48000,"
            "aformat=channel_layouts=stereo[a]"
        )
    else:
        audio = "[0:a:0]aresample=48000,aformat=channel_layouts=stereo[a]"
    run(
        [
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{end - start:.3f}",
            "-i",
            video,
            "-filter_complex",
            f"[0:v]{crop}[v];{audio}",
            "-map",
            "[v]",
            "-map",
            "[a]",
            *video_encoder(),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            path,
        ]
    )


def render_card_bed(video, at, length, path):
    """A blurred, darkened stretch of the race behind the end cards."""
    picture = (
        f"[0:v]scale=-2:{HEIGHT},crop={WIDTH}:{HEIGHT},setsar=1,"
        f"fps={FRAMES_PER_SECOND},"
        "gblur=sigma=28,hue=s=0.35,colorchannelmixer=rr=0.5:gg=0.5:bb=0.5[v]"
    )
    run(
        [
            "-ss",
            f"{at:.3f}",
            "-t",
            f"{length:.3f}",
            "-i",
            video,
            "-f",
            "lavfi",
            "-t",
            f"{length:.3f}",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-filter_complex",
            picture,
            "-map",
            "[v]",
            "-map",
            "1:a",
            *video_encoder(),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            path,
        ]
    )


def sound_design(captions, total):
    """The short's sound: the race as recorded, the bleeps over the swearing, a short
    radio squelch as each line opens, and one low hit under the pass. Nothing else on
    top."""
    graph = []
    mix = ["[g]"]
    if captions.bleeps:
        times = []
        for start, end in captions.bleeps:
            times.append(f"between(t,{start:.3f},{end:.3f})")
        on = "+".join(times)
        graph.append(f"[0:a]volume=0:enable='{on}'[g]")
        graph.append(
            f"sine=f=1000:r=48000:d={total:.2f},volume=0.18,"
            f"volume=0:enable='not({on})',"
            f"aformat=channel_layouts=stereo[bl]"
        )
        mix.append("[bl]")
    else:
        graph.append("[0:a]anull[g]")
    for index, at in enumerate(captions.squelches):
        delay = max(int((at - 0.06) * 1000), 0)
        graph.append(
            f"anoisesrc=d=0.07:c=pink:r=48000:a=0.25,highpass=f=1500,lowpass=f=6000,"
            f"afade=t=out:st=0.02:d=0.05,aformat=channel_layouts=stereo,"
            f"adelay={delay}|{delay}[q{index}]"
        )
        mix.append(f"[q{index}]")
    for index, at in enumerate(captions.hits):
        delay = max(int(at * 1000), 0)
        graph.append(
            f"sine=f=52:r=48000:d=0.5,volume=1.4,afade=t=out:st=0.04:d=0.46,"
            f"aformat=channel_layouts=stereo,adelay={delay}|{delay}[h{index}]"
        )
        mix.append(f"[h{index}]")
    graph.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[a]")
    return ";".join(graph)


# ---------- the short ----------
def read_json(path):
    """A JSON file's contents."""
    with open(path) as f:
        return json.load(f)


def recording_time(line):
    """When a radio line is said in the recording."""
    return line["video_t"]


def timed_radio(moments):
    """The radio lines in recording order, each word with its time."""
    radio = sorted(moments["radio"], key=recording_time)
    for line in radio:
        line["timed"] = timed_line_words(line, moments["words"])
    return radio


def pick_beats(radio, moments, race):
    """The story in order: the called shot (measured from the race data when there is
    some), his question and its answer, a team-memory line."""
    hook = []
    if race:
        hook = pick_hook_measured(radio, race)
    if not hook:
        hook = pick_hook(radio)
    return hook + pick_ask(radio, moments["questions"]) + pick_memory(radio)


def fit_beats(beats, max_s, cards_length):
    """The beats that fit in the short with its end cards, in order; never a question
    without its answer."""
    kept = []
    total = 0.0
    for beat in beats:
        length = beat.end - beat.start
        if total + length + cards_length > max_s and kept:
            if beat.kind == "answer" and kept[-1].kind == "ask":
                total -= kept[-1].end - kept[-1].start
                kept.pop()
            continue
        kept.append(beat)
        total += length
    return kept


def ending_length(ending):
    """How long the end cards run."""
    if ending == "launch":
        return COMING_SOON_S + CALL_TO_ACTION_S
    return DRIVER_CARD_S


def name_card_beat(kept):
    """Where the product's name goes: the first beat after the called shot (on the pass
    it would sit on top of the gap counter), or the last beat when the short is all
    hook."""
    for index, beat in enumerate(kept):
        if beat.kind not in HOOK_BEATS:
            return index
    return len(kept) - 1


def launch_cards(captions, start):
    """Our own marketing: COMING SOON, then the early-access card. Returns when they
    end. The cards stay inside the safe zone: app UI covers the top 14% and the bottom
    35% of a vertical video."""
    captions.card(
        start, start + COMING_SOON_S, "CardHead", "{\\pos(540,560)}COMING SOON"
    )
    soon = "{\\pos(540,800)}" + "\\N".join(COMING_SOON)
    captions.card(start + 0.1, start + COMING_SOON_S, "CardText", soon)
    action = start + COMING_SOON_S
    over = action + CALL_TO_ACTION_S
    captions.card(action, over, "CardBig", "{\\pos(540,560)}" + CTA_TOP)
    captions.card(action + 0.15, over, "CardText", "{\\pos(540,800)}" + CTA_MID)
    captions.card(action + 0.5, over, "Button", "{\\pos(540,1010)}" + CTA_BUTTON)
    return action + CALL_TO_ACTION_S


def driver_card(captions, start):
    """The driver's own clip: every one he posts carries the name (the growth loop,
    feature
    11). Returns when the card ends."""
    over = start + DRIVER_CARD_S
    captions.card(start, over, "CardHead", "{\\pos(540,600)}RADIO BY")
    captions.card(start + 0.1, over, "CardBig", "{\\pos(540,780)}" + CTA_TOP)
    captions.card(start + 0.3, over, "CardText", "{\\pos(540,1000)}" + CTA_MID)
    return start + DRIVER_CARD_S


class Short:
    """A short being built: its parts (one per beat, then the end cards' background),
    its captions, and how long it is so far."""

    def __init__(self, video, race, clock):
        self.video = video
        self.race = race
        self.clock = clock
        self.work = os.path.splitext(video)[0] + "_work"
        self.captions = Captions()
        self.parts = []
        self.length = 0.0

    def add_beats(self, kept):
        """Every beat cut from the recording, with its captions."""
        os.makedirs(self.work, exist_ok=True)
        mics = audio_count(self.video)
        name_at = name_card_beat(kept)
        for index, beat in enumerate(kept):
            part = os.path.join(self.work, f"part{index}.mp4")
            render_segment(self.video, beat.start, beat.end, part, mics)
            self.parts.append(part)
            self.caption_beat(beat, self.length - beat.start)
            self.name_cards(index, beat, name_at)
            self.length += beat.end - beat.start

    def caption_beat(self, beat, shift):
        """A beat's captions: his question, the gap counter and the place for the pass,
        and every radio line in it."""
        if beat.kind == "ask":
            self.captions.question(beat.question, shift)
        if beat.kind == "pass":
            self.captions.gap_ticker(self.race, beat, self.clock, shift)
            overtake = beat.overtake
            pass_at = self.clock.video_time(overtake["pass_sim"]) + shift
            self.captions.place_pop(pass_at, overtake["before"], overtake["after"])
        for line in beat.lines:
            self.captions.radio(line, shift, beat.start, beat.end)

    def name_cards(self, index, beat, name_at):
        """The called shot's title on frame one; the product's name once, at name_at."""
        if index == 0 and beat.kind in HOOK_BEATS:
            title = "MY AI ENGINEER\\N{\\c" + ORANGE + "}CALLED THIS PASS"
            self.captions.card(0.0, HOOK_TITLE_S, "Name", title)
        if index == name_at and (index > 0 or beat.kind not in HOOK_BEATS):
            name = "AI RACE ENGINEER\\N{\\c" + ORANGE + "}FOR LE MANS ULTIMATE"
            start = self.length + NAME_CARD_IN_S
            self.captions.card(start, self.length + NAME_CARD_OUT_S, "Name", name)

    def add_ending(self, ending, bed_from, cards_length):
        """The end cards, over a blurred stretch of the race."""
        bed = os.path.join(self.work, "cards.mp4")
        render_card_bed(self.video, bed_from, cards_length, bed)
        self.parts.append(bed)
        if ending == "launch":
            self.length = launch_cards(self.captions, self.length)
        else:
            self.length = driver_card(self.captions, self.length)

    def render(self, out):
        """The parts joined, the captions burned in, the sound laid over it, written to
        out."""
        listing = os.path.join(self.work, "parts.txt")
        with open(listing, "w") as f:
            for part in self.parts:
                f.write(f"file '{part}'\n")
        joined = os.path.join(self.work, "joined.mp4")
        run(["-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", joined])
        captions_file = os.path.join(self.work, "captions.ass")
        self.captions.write(captions_file)
        captions_path = captions_file.replace("\\", "/").replace(":", "\\:")
        sound = sound_design(self.captions, self.length)
        run(
            [
                "-i",
                joined,
                "-filter_complex",
                f"[0:v]subtitles='{captions_path}'[v];{sound}",
                "-map",
                "[v]",
                "-map",
                "[a]",
                *video_encoder(),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                out,
            ]
        )


def make(video, moments_path, out=None, max_s=30.0, ending="launch", race_data=None):
    """Builds the short and returns its path, or None when the race gave nothing to
    show. race_data (clips/race_data.py) turns on the measured hook: the real pass, the
    gap ticking down to it, the place changing."""
    video = os.path.abspath(video)
    moments = read_json(moments_path)
    radio = timed_radio(moments)
    race = None
    if race_data:
        race = read_json(race_data)
    beats = pick_beats(radio, moments, race)
    if not beats:
        print("[clips] nothing worth a short in this race")
        return None
    if beats[0].kind not in HOOK_BEATS:
        print(
            "[clips] no called shot in this race"
            " - the short opens on the next best beat"
        )
    cards_length = ending_length(ending)
    kept = fit_beats(beats, max_s, cards_length)
    clock = None
    if race:
        clock = VideoClock(radio)
    short = Short(video, race, clock)
    short.add_beats(kept)
    short.add_ending(ending, kept[0].start, cards_length)
    if not out:
        out = os.path.splitext(video)[0] + ".short.mp4"
    short.render(out)
    kinds = []
    for beat in kept:
        kinds.append(beat.kind)
    bleeps = len(short.captions.bleeps)
    print(f"[clips] beats: {kinds}; {short.length:.1f} s; {bleeps} bleeps; wrote {out}")
    return out


def main():
    """The command line: python -m clips.make_short RECORDING.mp4 [options]."""
    parser = argparse.ArgumentParser()
    parser.add_argument("video")
    parser.add_argument("--moments")
    parser.add_argument("--out")
    parser.add_argument("--max", type=float, default=30.0)
    parser.add_argument("--ending", choices=["launch", "driver"], default="launch")
    parser.add_argument(
        "--race-data", help="clips/race_data.py output: the measured hook and overlays"
    )
    args = parser.parse_args()
    moments = args.moments
    if not moments:
        moments = os.path.splitext(os.path.abspath(args.video))[0] + ".moments.json"
    make(args.video, moments, args.out, args.max, args.ending, args.race_data)


if __name__ == "__main__":
    main()
