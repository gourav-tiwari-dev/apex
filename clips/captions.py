"""What a short shows on top of the picture, as an ASS subtitle file: who is talking and
their words a few at a time (swear words starred), his question, the gap counter and
the place for the pass, and the cards. It also keeps where the sound needs a bleep, a
squelch or a hit (make_short.sound_design reads them)."""

import re

from clips.beats import GAP_AHEAD, SENTENCE_ENDS, SIM, SPEED


# the short's canvas: vertical 9:16
WIDTH = 1080
HEIGHT = 1920

SWEARS = re.compile(
    r"\b(fuck\w*|shit\w*|bollocks|bastard\w*|twat\w*|arse\w*|dick\w*|wank\w*)\b", re.I
)
SEAT_NAMES = {
    "racecraft": "RACECRAFT",
    "race_engineer": "RACE ENGINEER",
    "strategist": "STRATEGY",
    "spotter": "SPOTTER",
    "memory": "TEAM MEMORY",
    "performance": "PERFORMANCE",
    "setup": "SETUP",
}
# ASS colours are &HAABBGGRR
ORANGE = "&H001F5BFF"
WHITE = "&H00F4F1EE"
INK = "&H000C0907"


BREAKS_AFTER = (".", "?", "!", ",")  # a caption chunk may end after these

KMH_PER_METRE_A_SECOND = 3.6


# the captions
LAST_WORD_MARGIN_S = 0.1  # a word that starts in a beat's last 0.1 s is not shown
TAG_TAIL_S = 0.4  # the seat's tag stays up a little after the last word
QUESTION_SHOWN_S = 0.8  # his question stays up a little after he stops talking
CLOSE_METRES = 3  # the gap counter turns orange inside 3 m
WHOLE_METRES_FROM = 10  # and drops the decimal from 10 m
PLACE_SHOWN_S = 1.8
FIRST_FRAME_S = 0.05


STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
    "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
    "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
)
STYLES = [
    f"Style: Tag,Bahnschrift,40,{ORANGE},{ORANGE},{INK},&H99000000,"
    "1,0,0,0,100,100,4,0,3,10,0,2,80,80,1000,1",
    f"Style: Radio,Bahnschrift,92,{WHITE},{WHITE},{INK},&H00000000,"
    "1,0,0,0,100,100,0,0,1,7,3,2,70,70,820,1",
    f"Style: Ask,Bahnschrift,76,{INK},{INK},{WHITE},&H00F4F1EE,"
    "1,0,0,0,100,100,0,0,3,22,0,2,90,90,820,1",
    f"Style: Name,Bahnschrift,70,{WHITE},{WHITE},{INK},&H00000000,"
    "1,0,0,0,100,100,2,0,1,6,2,8,80,80,380,1",
    f"Style: CardBig,Bahnschrift,230,{WHITE},{WHITE},{INK},&H00000000,"
    "1,0,0,0,100,100,6,0,1,0,0,5,60,60,0,1",
    f"Style: CardText,Bahnschrift,68,{WHITE},{WHITE},{INK},&H00000000,"
    "1,0,0,0,100,100,0,0,1,0,0,5,80,80,0,1",
    f"Style: CardHead,Bahnschrift,56,{ORANGE},{ORANGE},{INK},&H00000000,"
    "1,0,0,0,100,100,8,0,1,0,0,5,80,80,0,1",
    f"Style: Button,Bahnschrift,62,{INK},{INK},{ORANGE},&H001F5BFF,"
    "1,0,0,0,100,100,2,0,3,26,0,5,80,80,0,1",
    f"Style: Gap,Bahnschrift,84,{WHITE},{WHITE},{INK},&H00000000,"
    "1,0,0,0,100,100,3,0,1,7,2,8,80,80,560,1",
    f"Style: Place,Bahnschrift,150,{WHITE},{WHITE},{INK},&H00000000,"
    "1,0,0,0,100,100,4,0,1,9,3,8,80,80,520,1",
]
EVENT_FORMAT = (
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
)


# ---------- captions ----------
def ass_time(seconds):
    """Seconds as an ASS subtitle time, H:MM:SS.ss (never below zero)."""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int(seconds % 3600 // 60)
    return f"{hours}:{minutes:02d}:{seconds % 60:05.2f}"


def chunks(words, size=3):
    """About 3 words at a time, breaking at punctuation. A word that would flash up
    alone ("2.", "there.", "Esses,") joins the chunk before it (or after it, if it opens
    the line)."""
    return without_lonely_words(first_cut(words, size), size)


def first_cut(words, size):
    """Groups of `size` words, cut early after punctuation."""
    groups = []
    current = []
    for index, word in enumerate(words):
        current.append(word)
        ends = word["raw"].endswith(BREAKS_AFTER)
        if not ends and len(current) == size and next_ends_sentence(words, index):
            continue  # it waits: the next word ends a sentence and would sit alone
        if len(current) >= size or ends:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def next_ends_sentence(words, index):
    """The word after this one ends a sentence."""
    if index + 1 >= len(words):
        return False
    return words[index + 1]["raw"].endswith(SENTENCE_ENDS)


def without_lonely_words(groups, size):
    """A one-word group joins the one before it (or after it, when it comes first)."""
    joined = []
    for group in groups:
        if len(group) == 1 and joined and len(joined[-1]) <= size + 1:
            joined[-1] = joined[-1] + group
        else:
            joined.append(group)
    if len(joined) > 1 and len(joined[0]) == 1:
        joined[1] = joined[0] + joined[1]
        joined.pop(0)
    return joined


def star(word):
    """A swear word as the screen shows it: its first letter, then stars ("f******")."""
    return SWEARS.sub(starred, word)


def starred(match):
    """One swear word, starred."""
    swear = match.group(0)
    return swear[0] + "*" * (len(swear) - 1)


def shown_words(group):
    """A caption chunk as the screen shows it."""
    shown = []
    for word in group:
        shown.append(star(word["raw"]))
    return " ".join(shown)


def gap_rows(race, beat, clock):
    """The race data rows the gap counter shows: a measured gap and a speed, inside the
    beat, before the pass."""
    pass_sim = beat.overtake["pass_sim"]
    rows = []
    for row in race:
        if row[GAP_AHEAD] is None or len(row) <= SPEED:
            continue
        if clock.video_time(row[SIM]) < beat.start or row[SIM] >= pass_sim:
            continue
        if clock.video_time(row[SIM]) < beat.end:
            rows.append(row)
    return rows


def ass_header():
    """The top of the ASS file: the canvas, the caption styles, the event format."""
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {WIDTH}",
        f"PlayResY: {HEIGHT}",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        STYLE_FORMAT,
    ]
    for style in STYLES:
        lines.append(style)
    lines.append("")
    lines.append("[Events]")
    lines.append(EVENT_FORMAT)
    return "\n".join(lines) + "\n"


def fade_for(start, style):
    """How a caption comes in: tags and names fade, the words pop. On frame one nothing
    fades in (most viewers start muted, and a caption that's already there holds them:
    frame-one captions lift muted retention 25-40%), and the race's own numbers never
    fade."""
    if start < FIRST_FRAME_S or style in ("Gap", "Place"):
        return ""
    if style in ("Tag", "Ask", "Name"):
        return r"{\fad(120,80)}"
    return r"{\fad(60,0)}"


class Captions:
    """Everything put on top of the beats: the caption events, the bleeps over the
    swearing, the radio's squelch as each line opens, and a low hit under the pass."""

    def __init__(self):
        self.events = []  # (start, end, style, text)
        self.bleeps = []  # (start, end) of every swear word
        self.hits = []  # a low hit under the pass
        self.squelches = []  # the radio's click as each line opens

    def radio(self, line, shift, beat_start, beat_end):
        """One radio line: a seat tag for the whole line, the words 3 at a time."""
        words = []
        for word in line["timed"]:
            if word["start"] < beat_end - LAST_WORD_MARGIN_S:
                words.append(word)
        start = max(words[0]["start"], beat_start)
        end = min(words[-1]["end"] + TAG_TAIL_S, beat_end)
        tag = "APEX · " + SEAT_NAMES.get(line["seat"], line["seat"].upper())
        self.events.append((start + shift, end + shift, "Tag", tag))
        self.squelches.append(start + shift)
        groups = chunks(words)
        for index, group in enumerate(groups):
            group_start = max(group[0]["start"], beat_start)
            group_end = end
            if index + 1 < len(groups):
                group_end = groups[index + 1][0]["start"]
            group_end = min(group_end, beat_end)
            if group_end <= group_start:
                continue
            text = shown_words(group)
            self.events.append((group_start + shift, group_end + shift, "Radio", text))
        for word in words:
            if SWEARS.search(word["raw"]) and beat_start <= word["start"] < beat_end:
                bleep_end = min(word["end"], beat_end) + shift
                self.bleeps.append((word["start"] + shift, bleep_end))

    def question(self, question, shift):
        """His push-to-talk question: a YOU tag and his words while he speaks."""
        start = question["video_t"] + shift
        end = question["video_end"] + QUESTION_SHOWN_S + shift
        self.events.append((start, end, "Tag", "YOU · PUSH-TO-TALK"))
        self.events.append((start, end, "Ask", question["text"]))

    def card(self, start, end, style, text):
        """A card or title, drawn from start to end."""
        self.events.append((start, end, style, text))

    def gap_ticker(self, race, beat, clock, shift):
        """The gap to the car ahead as Apex measured it, every 0.1 s up to the pass, in
        metres (gap x speed): in a tow 0.14 s -> 0.01 s looks frozen, 7.8 m -> 0.6 m you
        can feel. White, then orange inside 3 m."""
        pass_sim = beat.overtake["pass_sim"]
        rows = gap_rows(race, beat, clock)
        for index, row in enumerate(rows):
            # abs: never "-0.0 m"
            metres = abs(max(row[GAP_AHEAD], 0.0)) * row[SPEED] / KMH_PER_METRE_A_SECOND
            start = clock.video_time(row[SIM]) + shift
            if index + 1 < len(rows):
                end = clock.video_time(rows[index + 1][SIM]) + shift
            else:
                end = clock.video_time(pass_sim) + shift
            colour = WHITE
            if metres < CLOSE_METRES:
                colour = ORANGE
            shown = f"{metres:.0f}"
            if metres < WHOLE_METRES_FROM:
                shown = f"{metres:.1f}"
            self.events.append((start, end, "Gap", f"{{\\c{colour}}}GAP {shown} m"))

    def place_pop(self, at, before, after):
        """P8 > P7, popping in at the moment of the pass."""
        pop = r"{\fscx140\fscy140\t(0,180,\fscx100\fscy100)}"
        text = f"{pop}P{before} {{\\c{ORANGE}}}▸ P{after}"
        self.events.append((at, at + PLACE_SHOWN_S, "Place", text))
        self.hits.append(at)

    def write(self, path):
        """The captions as an ASS subtitle file."""
        body = []
        for start, end, style, text in sorted(self.events):
            fade = fade_for(start, style)
            times = f"{ass_time(start)},{ass_time(end)}"
            body.append(f"Dialogue: 0,{times},{style},,0,0,0,,{fade}{text}")
        with open(path, "w", encoding="utf-8") as f:
            f.write(ass_header() + "\n".join(body) + "\n")
