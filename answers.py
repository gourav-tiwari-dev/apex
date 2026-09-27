"""Push-to-talk answers (M9, Gourav's option A, 24 Sep 2026).

He holds R1 and asks. The question is matched to one of a fixed list of things a driver asks
his engineer, and code answers it from the live race. No model in the loop: the answer is
ready as soon as the words are, and he hears it after the voice render (about 1.3 s).
An agent with tools was the first plan; at about 1.8 s per model step it would have taken
about 7 s to answer, which is a corner and a half at Le Mans.

Matching: every intent has phrases that ask for it. The intent with the LONGEST matching
phrase wins, so "where am I losing time" is about lap time, not position ("where am i").
"""

import re

from persona import words_to_digits
from radio import Call, RACE_CONTROL
from race_state import same_class_neighbours, laps_to_go, multiclass
from game.constants import BLUE_FLAG, SAFETY_CAR, SECTOR_YELLOW

ANSWER_TTL_S = 10.0
DEFAULT_QUIET_LAPS = 2

# The fast lane has no model in it, so the Verstappen voice is written here by hand (24 Sep:
# "I need the outputs from the engineer in the same aggressive swearing Verstappen persona").
# The number comes first so it is easy to hear; the attitude comes after. Swearing is aimed at
# the situation or the other cars, never at him. Each intent takes turns through its lines.
# (swearing line, clean line)
CLOSERS = {
    "GAP_AHEAD": [
        ("Go fucking get them.", "Go get them."),
        ("Reel that car in, mate.", "Reel that car in, mate."),
        ("Hunt them down. No fucking mercy.", "Hunt them down."),
    ],
    "GAP_BEHIND": [
        ("Keep them in the fucking mirrors.", "Keep them in the mirrors."),
        ("Don't give them a bloody sniff.", "Don't give them a sniff."),
    ],
    "PACE_TO_CATCH": [
        ("Simple as that. Fucking go.", "Simple as that. Go."),
        ("Every lap, mate. No excuses.", "Every lap, mate. No excuses."),
    ],
    "FUEL": [
        ("Stop worrying about the fucking fuel.", "Stop worrying about the fuel."),
        ("Tank's not your problem, mate.", "Tank's not your problem, mate."),
    ],
    "LAPS_LEFT": [
        ("Head down, mate.", "Head down, mate."),
        ("Bring this fucking thing home.", "Bring it home."),
    ],
    "POSITION": [
        ("Let's fucking do better than that.", "Let's do better than that."),
        ("Plenty of race left, mate.", "Plenty of race left, mate."),
    ],
    "LAP_TIME": [
        ("Simply lovely. Again.", "Simply lovely. Again."),
        ("More of that shit, mate.", "More of that, mate."),
    ],
}

INTENTS = {
    "GAP_AHEAD": [
        "gap",
        "the gap",
        "ahead",
        "in front",
        "car in front",
        "gap ahead",
        "gap in front",
        "gap to the car ahead",
    ],
    "GAP_BEHIND": [
        "behind",
        "car behind",
        "gap behind",
        "who is behind",
        "whos behind",
        "gap back",
    ],
    "PACE_TO_CATCH": [
        "catch",
        "catch him",
        "catch them",
        "catch the car ahead",
        "what lap time do i need",
        "lap time do i need",
        "what pace do i need",
        "pace do i need",
        "need to do",
    ],
    "FUEL": ["fuel", "energy", "tank", "enough fuel", "lift and coast", "save fuel"],
    "LAPS_LEFT": [
        "laps left",
        "how many laps",
        "laps to go",
        "time left",
        "how long left",
        "how long to go",
        "how many to go",
        "is this the last lap",
    ],
    "WHERE_LOSING": [
        "losing time",
        "where am i losing",
        "where am i slow",
        "slow",
        "improve",
        "faster",
        "where can i gain",
        "gain time",
    ],
    "POSITION": ["position", "what position", "where am i", "what place", "what p"],
    "LAP_TIME": ["lap time", "last lap", "my lap", "best lap", "my time"],
    "QUIET": [
        "quiet",
        "be quiet",
        "shut up",
        "silence",
        "mute",
        "leave me",
        "stop talking",
    ],
    "RADIO_ON": ["radio on", "back on", "talk to me", "unmute", "radio back"],
    # v3 5b (his ask, 25 Sep): lookups need no judgment, so code answers them - no model,
    # no 2-4 s wait, no cost. Anything asking what to DO about it still goes to the agent.
    "TYRES": [
        "tyres",
        "tires",
        "tyre",
        "tire",
        "tyre temps",
        "tire temps",
        "tyre temperatures",
        "tire temperatures",
        "fronts",
        "rears",
        "how are the tyres",
        "how are the tires",
    ],
    "TYRE_PRESSURES": ["pressures", "tyre pressures", "tire pressures", "pressure"],
    "TYRE_WEAR": ["wear", "tyre wear", "tire wear"],
    "COMPOUND": [
        "compound",
        "what tyres am i on",
        "what tires am i on",
        "what tyre am i on",
        "which tyres",
    ],
    "BRAKES": ["brakes", "brake temps", "brake temperatures", "how are my brakes"],
    "DAMAGE": [
        "damage",
        "damage report",
        "hows my car",
        "how is my car",
        "is my car ok",
        "car status",
        "anything broken",
        "broken",
        "everything ok with the car",
        "car ok",
    ],
    "ENGINE": ["engine", "is the engine overheating", "hows my engine"],
    "WEATHER": [
        "weather",
        "track temp",
        "track temperature",
        "air temp",
        "air temperature",
        "rain",
        "raining",
        "wet",
        "grip",
    ],
    "FLAGS": [
        "yellow",
        "yellow flag",
        "safety car",
        "full course yellow",
        "blue flag",
        "flags",
        "flag",
    ],
    "SECTORS": [
        "sectors",
        "sector times",
        "my sectors",
        "theoretical best",
        "best possible",
        "best possible lap",
    ],
    "CLASS_POSITION": [
        "position in class",
        "in class",
        "class position",
        "cars in my class",
        "how many cars in my class",
    ],
    "LEADER": [
        "leading",
        "leader",
        "whos leading",
        "who is leading",
        "whos in the lead",
        "leading my class",
    ],
    "FASTEST_LAP": ["fastest lap", "quickest lap", "fastest lap in my class"],
    "PENALTY": ["penalty", "penalties", "do i have a penalty", "served my penalty"],
    "TRACK_LIMITS": [
        "track limits",
        "track limit",
        "cuts",
        "warnings",
        "track limit warnings",
        "how many cuts",
    ],
    "SETTINGS": [
        "brake bias",
        "bias",
        "tc",
        "traction control",
        "abs",
        "motor map",
        "engine map",
        "settings",
    ],
    "FUEL_USAGE": [
        "fuel usage",
        "fuel per lap",
        "fuel use",
        "fuel consumption",
        "litres",
        "liters",
        "litres left",
        "how much fuel",
    ],
    "BATTERY": ["battery"],
    "LAP_VALID": ["valid", "lap valid", "invalid", "was that lap valid"],
    # v3 5b: asking the radio to behave differently. There is no such switch, and on the 25 Sep
    # bank run the model promised one anyway ("I'll only key up on the straights"), twice.
    # Code answers honestly: what the radio already does, and the switches he has.
    "RADIO_REQUEST": [
        "only talk to me",
        "talk to me on",
        "stop telling me",
        "talk less",
        "less talking",
        "every lap",
        "dont tell me",
        "stop calling",
    ],
    # live test marker (25 Sep): he says "mark" when something is wrong or great; the time is in
    # the radio log, so the moment can be found on the tape afterwards
    "MARK": ["mark", "mark that", "mark it", "note that", "bookmark", "flag that"],
    # v3 5b: a line lost under the engine or a spotter call
    "REPEAT": [
        "repeat",
        "repeat that",
        "say again",
        "say that again",
        "come again",
        "what did you say",
        "didnt catch",
        "didnt hear",
        "one more time",
    ],
}


def clean(text):
    text = words_to_digits(text.lower())
    text = text.replace("'", "")
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


# What the recognizer really wrote for his questions, live 25 Sep (engine noise, a
# controller in his hands). Whole phrases only: "how's the car feeling" stays a handling
# question. Fixed before routing, so "how's the feeling" gets the fuel answer in 50 ms
# instead of going to the coach and coming back as a fight call.
MISHEARD = [
    (r"\bhow('s| is| s) the feeling\b", "how's the fuel"),
    (r"\bthe feeling now\b", "the fuel now"),
    (r"\bthought ahead\b", "car ahead"),
    (r"\bcar hat\b", "car ahead"),
    (r"\bcut ahead\b", "car ahead"),
    (r"\b(caulif|cauley|kali)\b", "quali"),  # live 27 Sep, twice: "I crashed in Caulif"
]


def fix_mishearing(text):
    fixed = text
    for pattern, meant in MISHEARD:
        fixed = re.sub(pattern, meant, fixed, flags=re.IGNORECASE)
    return fixed


# Whisper's own rule: a transcription whose mean log-probability is below -1.0 is a guess
# (its logprob_threshold). Live 25 Sep: "3-1-1, Faucet's down" went to the coach and came back
# as a made-up fight call. A guess gets "say again" instead.
# Tuned on his 26 logged questions and marks (26 Sep, test_push_to_talk_5b.HIS_LOGGED_WORDS):
# none scored below -1.0, yet 4 were garbled ("3, 6, 1..." -0.94, "March, Good Ball on the
# Warning." -0.52) and the coach answered them all. No clean one scored below -0.9. Between -0.9
# and -0.5 the score alone cannot tell ("qualifying is fucked up." -0.85 was clean), but every
# clean one there carried a racing word and no garbled one did. A mark is a note, never a guess.
GARBLED_BELOW = -0.9
UNSURE_BELOW = -0.5
RACING_WORDS = (
    "gap",
    "gaps",
    "ahead",
    "behind",
    "leader",
    "fuel",
    "energy",
    "tyre",
    "tyres",
    "tire",
    "tires",
    "plan",
    "position",
    "place",
    "damage",
    "fight",
    "fighting",
    "catch",
    "losing",
    "time",
    "lap",
    "laps",
    "pace",
    "box",
    "pit",
    "push",
    "save",
    "sector",
    "brake",
    "brakes",
    "corner",
    "qualifying",
    "rain",
    "flag",
    "penalty",
    "overtake",
    "pass",
    "defend",
    "attack",
    "car",
    "cars",
    "faster",
    "slower",
    "quick",
    "race",
    "finish",
    "points",
    "performance",
    # live 27 Sep: "What's the problem?" (-0.85) and "I crashed in quali" got "say again"
    "quali",
    "problem",
    "issue",
    "temps",
    "temp",
    "temperature",
)


def has_racing_word(text):
    for word in clean(text).split():
        if word in RACING_WORDS:
            return True
        if len(word) >= 2 and word[0] == "p" and word[1:].isdigit():
            return True  # "p5"
    return False


def garbled(text, confidence):
    if not text.strip():
        return True
    if confidence is None or is_mark(text):
        return False
    if confidence < GARBLED_BELOW:
        return True
    if confidence < UNSURE_BELOW:
        return not has_racing_word(text)
    return False


def matched(text):
    """(intent, the phrase that matched): the longest phrase found in what he said."""
    heard = " " + clean(text) + " "
    best = (None, None)
    for intent, phrases in INTENTS.items():
        for phrase in phrases:
            if " " + phrase + " " in heard and (
                best[1] is None or len(phrase) > len(best[1])
            ):
                best = (intent, phrase)
    return best


def intent_of(text):
    """The intent with the longest phrase found in what he said, or None."""
    if is_mark(text):
        return "MARK"
    if is_acknowledgement(text):
        return "ACK"
    return matched(text)[0]


# v3 5b (25 Sep): the 300-question bank found the fixed lane grabbing anything with a trigger
# word in it: "Is the car ahead in my class?" got the gap, "Stop telling me about the car
# behind" got the gap behind. Now a fixed answer is given only when every word he said is
# either filler or part of what that intent is about; any word left over ("class", "sector",
# "usage", "damage") means he asked something the fixed answer does not cover.
FILLER = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "am",
    "i",
    "im",
    "me",
    "my",
    "we",
    "our",
    "us",
    "you",
    "your",
    "it",
    "its",
    "whats",
    "what",
    "hows",
    "how",
    "was",
    "were",
    "be",
    "to",
    "in",
    "on",
    "at",
    "of",
    "for",
    "about",
    "now",
    "mate",
    "please",
    "sorry",
    "hey",
    "so",
    "just",
    "ok",
    "okay",
    "right",
    "then",
    "this",
    "that",
    "there",
    "doing",
    "going",
    "gonna",
    "like",
    "and",
    "any",
    "do",
    "whos",
    "who",
    "tell",
    "give",
    "quick",
    "again",
    "yet",
    "still",
    "whats",
    "wheres",
    "where",
}
VOCABULARY_EXTRA = {
    "GAP_AHEAD": {"far", "close", "distance", "car"},
    "GAP_BEHIND": {"far", "close", "distance", "car"},
    "FUEL": {"much", "left", "tank", "enough", "level"},
    "LAPS_LEFT": {"many", "long", "much", "last", "lap"},
    "POSITION": {"place", "p", "we", "are"},
    "LAP_TIME": {"time", "lap"},
    "QUIET": {"lap", "laps", "alone", "bit", "while", "for"},
    "REPEAT": {"didnt", "catch", "hear", "said"},
    "PACE_TO_CATCH": {"need", "lap", "time", "pace"},
    "WHERE_LOSING": {"time", "pace", "losing", "gain", "can"},
    "TYRES": {
        "doing",
        "hot",
        "hottest",
        "which",
        "overheating",
        "cooking",
        "temperature",
        "temps",
        "are",
    },
    "TYRE_PRESSURES": {"are", "my"},
    "TYRE_WEAR": {"hows", "are", "my", "much"},
    "BRAKES": {"are", "temp", "temperature", "hot", "ok"},
    "DAMAGE": {"did", "i", "any", "report", "status", "ok"},
    "ENGINE": {"temps", "temperature", "hot", "ok", "overheating"},
    "WEATHER": {
        "going",
        "temperature",
        "temps",
        "much",
        "there",
        "track",
        "air",
        "anywhere",
        "doing",
        "dry",
    },
    "FLAGS": {"out", "there", "any", "getting", "flagged", "blue"},
    "SECTORS": {"times", "time", "best", "possible", "lap"},
    "CLASS_POSITION": {"many", "cars", "position", "my"},
    "LEADER": {"class", "my", "race", "lead"},
    "FASTEST_LAP": {"class", "my", "time", "whats"},
    "PENALTY": {"have", "served", "got", "any"},
    "TRACK_LIMITS": {"many", "have", "got", "steps", "points", "i"},
    "SETTINGS": {"am", "setting", "on", "which", "have", "what", "map", "motor"},
    "FUEL_USAGE": {"left", "have", "got", "per", "lap", "much", "usage"},
    "BATTERY": {"hows", "level"},
    "LAP_VALID": {"that", "lap", "last", "was"},
}


def words_beyond(text, intent):
    vocabulary = {
        word for phrase in INTENTS[intent] for word in phrase.split()
    } | VOCABULARY_EXTRA.get(intent, set())
    return [
        word
        for word in clean(text).split()
        if word not in vocabulary and word not in FILLER and not word.isdigit()
    ]


# the fixed list answers short questions; anything longer is a real question for the agent
# ("the car behind is diving at me, he's 2 seconds faster, defend or let him go?" contains
# "behind" but is not asking for the gap)
FIXED_ANSWER_MAX_WORDS = 7
ALWAYS_FIXED = {"QUIET", "RADIO_ON", "REPEAT"}


# "can I catch him?" is a judgment, not a lookup: on 24 Sep the fixed list answered it with
# the bare gap. Questions that ask what to DO go to the agent however short they are.
# ...anywhere in the question: "realistically which position can we get" got a bare "P5"
JUDGMENT_WORDS = (
    "can i",
    "can we",
    "should",
    "could",
    "do i",
    "what do i",
    "how do i",
    "why",
    "is it worth",
    "will i",
    "will we",
    "realistic",
    "what if",
    # a fight is a judgment too: live 24 Sep, "car ahead is defending" got the bare gap
    "defending",
    "defend",
    "blocking",
    "block",
    "diving",
    "dive",
    "attack",
    "attacking",
    "overtake",
    "pass",
    "passing",
    "hitting",
    "hit me",
    "let him",
    "let them",
    "aggressive",
    "aggressively",
)


def is_mark(text):
    """He starts or ends with "mark". Live 27 Sep: "Wrong advice regarding energy, Mark." lost to the
    fuel question ("energy" is the longer phrase), went to the agent and was never saved as a mark."""
    words = clean(text).split()
    return bool(words) and (words[0].startswith("mark") or words[-1] == "mark")


# "okay, got it" after an answer is him saying he heard it. Live 27 Sep it went to the agent, which
# answered it with a new answer that contradicted its last one (and a model call)
ACKNOWLEDGE = {
    "ok",
    "okay",
    "got",
    "it",
    "copy",
    "copied",
    "that",
    "understood",
    "understand",
    "roger",
    "thanks",
    "thank",
    "you",
    "cheers",
    "alright",
    "right",
    "sure",
    "yeah",
    "yes",
    "yep",
    "noted",
    "cool",
    "nice",
    "good",
    "fine",
    "mate",
    "i",
    "will",
    "do",
    "clear",
}
ACKNOWLEDGE_CORE = {
    "ok",
    "okay",
    "got",
    "copy",
    "copied",
    "understood",
    "understand",
    "roger",
    "thanks",
    "thank",
    "cheers",
    "alright",
    "noted",
}


def is_acknowledgement(text):
    words = clean(text).split()
    if not words:
        return False
    for word in words:
        if word not in ACKNOWLEDGE:
            return False
    for word in words:
        if word in ACKNOWLEDGE_CORE:
            return True
    return False


def needs_agent(text):
    if is_mark(text) or is_acknowledgement(text):
        return False
    intent, phrase = matched(text)
    if intent in ("QUIET", "RADIO_REQUEST", "MARK"):
        return False  # "quiet, I need to focus on this fight": obeyed at once, whatever else he says
    if intent is None or words_beyond(text, intent):
        return True
    if intent in ALWAYS_FIXED:
        return False
    # judgment words that are part of the intent's own phrase ("what pace do i need") do not count
    heard = (" " + clean(text) + " ").replace(" " + phrase + " ", " ")
    for words in JUDGMENT_WORDS:
        if " " + words + " " in heard:
            return True
    return len(clean(text).split()) > FIXED_ANSWER_MAX_WORDS


def laps_asked(text):
    """ "quiet for 3 laps" -> 3."""
    match = re.search(r"(\d+)\s*lap", clean(text))
    if match:
        return max(1, int(match.group(1)))
    if re.search(r"\ba lap\b|\bthis lap\b", clean(text)):
        return 1
    return DEFAULT_QUIET_LAPS


WHEEL_NAMES = ("front left", "front right", "rear left", "rear right")
HOT_TYRE_C = 105  # the strategist's "cooking" line


def lap_text(seconds):
    minutes = int(seconds // 60)
    rest = round(seconds - minutes * 60, 1)
    if rest >= 60.0:
        minutes += 1
        rest = round(rest - 60.0, 1)
    return f"{minutes}:{rest:04.1f}"


class Answers:
    """Builds the answer. Reads the seats it needs, never changes what they will say next,
    except for the governor's quiet laps."""

    def __init__(self, governor, engineer, strategist, performance, clean=False):
        self.governor = governor
        self.engineer = engineer
        self.strategist = strategist
        self.performance = performance
        self.clean = clean
        self.said = {}  # intent -> how many times answered: the closers take turns
        self.last_line = None  # the last engineer line on air, for "say again" (set by the race loop)
        self.model = (
            None  # the race model (set by the race loop): the same gaps everywhere
        )

    def closer(self, intent):
        lines = CLOSERS.get(intent)
        if not lines:
            return ""
        turn = self.said.get(intent, 0)
        self.said[intent] = turn + 1
        swearing, clean = lines[turn % len(lines)]
        return " " + (clean if self.clean else swearing)

    def answer(self, text, race, lap, now):
        intent = intent_of(text)
        seat = "race_engineer"
        if race is None or race.me is None:
            words = "No race data yet."
        elif intent is None:
            words = "Didn't get that, mate. Say again."
        elif intent == "FUEL":
            seat = "strategist"
            words = self.fuel(race)
        elif intent == "WHERE_LOSING":
            seat = "performance"
            words = self.where_losing()
        elif intent == "QUIET":
            laps = laps_asked(text)
            self.governor.quiet_until_lap = lap + laps
            words = f"Fine, I'll shut up for {laps} {'lap' if laps == 1 else 'laps'}. Spotter stays on."
        elif intent == "RADIO_ON":
            self.governor.quiet_until_lap = None
            words = "Radio's back, mate."
        elif intent == "RADIO_REQUEST":
            words = (
                "Can't switch that one, mate. What you can: quiet for some laps, radio back on, gaps every lap "
                "or no gaps, no coaching, we push, we save, fight everyone, back to normal."
            )
        elif intent == "MARK":
            words = "Marked."
        elif intent == "ACK":
            words = "Copy."
        elif intent == "REPEAT":
            words = self.last_line or "Nothing to repeat yet, mate."
        else:
            words = getattr(self, intent.lower())(race)
        # the attitude line; "stop worrying about the fuel" only when the fuel IS fine, and
        # never after a non-answer ("No lap time for it yet. Simple as that. Fucking go.")
        no_answer = words.startswith(("No ", "Nobody", "Need ")) or "yet." in words
        if (
            intent in CLOSERS
            and race is not None
            and race.me is not None
            and not no_answer
        ):
            if intent != "FUEL" or "fine" in words:
                words += self.closer(intent)
        return Call(
            seat=seat,
            kind="ANSWER_" + (intent or "UNHEARD"),
            sim_time=now,
            priority=RACE_CONTROL,
            ttl=ANSWER_TTL_S,
            conclusion=words,
            template=words,
            facts={"heard": text},
            asked=True,
            phrase=False,
        )

    def gap_ahead(self, race):
        ahead, gap, behind, gap_behind = same_class_neighbours(race, self.model)
        if ahead is None or gap is None:
            return "Nobody ahead in your class. You're leading it."
        words = f"Car ahead, {round(gap, 1)}."
        trend = self.trend_words(ahead.id, "me", "You're catching", "It's pulling away")
        if trend:
            return words + " " + trend
        if ahead.last_lap > 0 and ahead.laps >= 2:
            words += f" Lapping {lap_text(ahead.last_lap)}."
        return words

    def trend_words(self, front, back, closing, growing):
        """The race model's road trend in a few words, only when it is sure (2 laps)."""
        if self.model is None:
            return None
        t = self.model.trend(front, back)
        if t is None or not t["sure"]:
            return None
        amount = abs(t["closing_per_lap"])
        if amount < 0.1:
            return "Gap's steady."
        return f"{closing if t['closing_per_lap'] > 0 else growing} {amount:.1f} a lap."

    def gap_behind(self, race):
        ahead, gap_ahead, behind, gap = same_class_neighbours(race, self.model)
        if behind is None or gap is None:
            return "Nobody behind in your class."
        words = f"Car behind, {round(gap, 1)}."
        trend = self.trend_words("me", behind.id, "It's catching", "It's dropping back")
        if trend:
            return words + " " + trend
        if behind.last_lap > 0 and behind.laps >= 2:
            words += f" Lapping {lap_text(behind.last_lap)}."
        return words

    def pace_to_catch(self, race):
        ahead, gap, behind, gap_behind = same_class_neighbours(race, self.model)
        if ahead is None or gap is None:
            return "Nobody ahead to catch. Just bring it home."
        to_go = self.engineer.to_go_at_line
        if to_go is None:
            # the same fallback "laps left" uses (25 Sep bank run: one said "2 laps to go" while
            # the other said "need a timed lap first")
            to_go = laps_to_go(
                race, race.me.last_lap if race.me.last_lap > 0 else None, self.model
            )
        # its last lap, or its best when the game posted -1 for the last one (the agent's rule too)
        theirs = ahead.last_lap if ahead.last_lap > 0 else ahead.best_lap
        if to_go is None or to_go < 1 or theirs <= 0:
            return f"Car ahead is {round(gap, 1)} up. No lap time for it yet."
        target = theirs - gap / to_go
        return f"You need {lap_text(target)} to catch the car ahead by the flag. It's doing {lap_text(theirs)}."

    def fuel(self, race=None):
        picture = self.strategist.fuel_now
        if picture is not None and "verdict" in picture:
            from seats.strategist import fuel_words

            return fuel_words(picture)  # the verdict, not just numbers (25 Sep)
        if picture is None:
            # live 25 Sep: "Need two laps to measure the fuel" was all he got. The tank is known
            # from the first second; only the laps it lasts needs two laps at the line.
            if race is None or race.me is None:
                return "Need two laps to measure the fuel."
            words = f"{round(race.me.fuel, 1)} litres in."
            if race.me.virtual_energy > 0:
                words += f" Energy {round(race.me.virtual_energy * 100)} percent."
            low = (
                race.me.fuel_capacity > 0 and race.me.fuel / race.me.fuel_capacity < 0.1
            )
            if low or 0 < race.me.virtual_energy < 0.1:
                return (
                    words
                    + " That's LOW. Usage in half a lap: check your screen, be ready to box."
                )
            return words + " Usage measured in half a lap."
        spare = picture["spare_laps"]
        what = "Fuel" if picture["limit"] == "fuel" else "Energy"
        from seats.strategist import fine_margin

        if spare >= fine_margin(picture.get("laps_left") or 99.0):
            return f"{what}'s fine. {spare} laps spare. Push."
        if spare >= 0:
            return f"{what}'s tight, {spare} laps spare. Lift and coast into the big stops."
        return (
            f"{what}'s short by {abs(spare)} laps. Lift and coast every braking zone."
        )

    def laps_left(self, race):
        to_go = self.engineer.to_go_at_line
        if to_go is None:
            to_go = laps_to_go(
                race, race.me.last_lap if race.me.last_lap > 0 else None, self.model
            )
        if to_go is None:
            return "Need a timed lap to count it."
        if to_go <= 1:
            return "This is the last lap."
        return f"{to_go} laps to go, this one included."

    def where_losing(self):
        focus = self.performance.focus()
        if focus is None:
            return "Nothing clear yet. Need a couple more laps."
        tenths = round(focus["gap_s"] * 10)
        amount = "a tenth" if tenths <= 1 else f"{tenths} tenths"
        return f"{focus['corner']}. The fastest car finds {amount} there. {focus['advice']}"

    def position(self, race):
        if multiclass(race):
            return self.class_standing(race)  # "P7 in class, of 25. P43 overall."
        return f"P{race.me.place}."

    # ---- v3 5b: lookups answered by code (his ask, 25 Sep) ------------------------------------
    def temps_by_wheel(self, me):
        temps = [round(sum(z) / len(z)) for z in me.tyre_temps if z and min(z) > -200]
        return temps if len(temps) == 4 else None

    def tyres(self, race):
        temps = self.temps_by_wheel(race.me)
        if temps is None:
            return "No tyre temperatures from the game."
        words = f"Fronts {temps[0]} and {temps[1]}. Rears {temps[2]} and {temps[3]}."
        cooking = [name for name, t in zip(WHEEL_NAMES, temps) if t > HOT_TYRE_C]
        if cooking:
            words += (
                f" {' and '.join(cooking).capitalize()} cooking, over {HOT_TYRE_C}."
            )
        else:
            hottest = WHEEL_NAMES[temps.index(max(temps))]
            words += f" Hottest the {hottest}. Nothing cooking."
        if race.me.tyre_wear:
            words += f" Worst tyre {round(min(race.me.tyre_wear) * 100)} percent left."
        return words

    def tyre_pressures(self, race):
        p = race.me.tyre_pressures
        if not p or len(p) != 4:
            return "No pressures from the game."
        return f"Fronts {round(p[0])} and {round(p[1])}. Rears {round(p[2])} and {round(p[3])}. kPa."

    def tyre_wear(self, race):
        wear = race.me.tyre_wear
        if not wear or len(wear) != 4:
            return "No tyre wear from the game."
        left = [round(w * 100) for w in wear]
        worst = WHEEL_NAMES[left.index(min(left))]
        return f"Fronts {left[0]} and {left[1]} percent left, rears {left[2]} and {left[3]}. Worst the {worst}."

    def compound(self, race):
        return (
            f"{race.me.compound}."
            if race.me.compound
            else "The game doesn't say the compound."
        )

    def brakes(self, race):
        b = race.me.brake_temps
        if not b or len(b) != 4:
            return "No brake temperatures from the game."
        return f"Brakes: fronts {round(b[0])} and {round(b[1])}, rears {round(b[2])} and {round(b[3])} degrees."

    def damage(self, race):
        me = race.me
        hit = sum(1 for d in me.dents if d)
        words = (
            "No damage."
            if hit == 0
            else "Damage in one place on the car."
            if hit == 1
            else f"Damage in {hit} places around the car."
        )
        if me.detached:
            words += " Something's come off."
        if me.overheating:
            words += " Engine's overheating."
        return words

    def engine(self, race):
        return (
            "Engine's overheating. Short-shift and get air to it."
            if race.me.overheating
            else "Engine's fine."
        )

    def weather(self, race):
        s = race.session
        rain = (
            "Dry."
            if s.raining < 0.05
            else f"Raining, {round(s.raining * 100)} percent."
        )
        words = f"Air {round(s.ambient_temp)}, track {round(s.track_temp)}. {rain}"
        if s.wetness >= 0.05:
            words += f" Track {round(s.wetness * 100)} percent wet."
        grip = {
            0: "Green track",
            1: "Low rubber",
            2: "Medium rubber",
            3: "High rubber",
            4: "Full rubber",
        }.get(s.grip_level)
        if grip:
            words += f" {grip}."
        return words + " No forecast in the data."

    def flags(self, race):
        s = race.session
        yellow_sectors = [
            str(i + 1) for i, f in enumerate(s.sector_flags) if f == SECTOR_YELLOW
        ]
        if s.game_phase == SAFETY_CAR:
            words = "Full course yellow."
        elif yellow_sectors:
            words = f"Yellow in sector {' and '.join(yellow_sectors)}. Careful there, someone's in trouble."
        else:
            words = "No yellows."
        if race.me.flag == BLUE_FLAG:
            words += " Blue flag for you: let it by on the exit."
        return words

    def sectors(self, race):
        timed = [
            r for r in getattr(self.strategist, "lap_records", []) if r.get("sectors_s")
        ]
        if not timed:
            return "No full lap with sectors timed yet."
        last = timed[-1]["sectors_s"]
        best = [min(r["sectors_s"][i] for r in timed) for i in range(3)]
        words = "Last lap sectors " + ", ".join(f"{t:.1f}" for t in last) + "."
        return words + f" Best possible lap {lap_text(sum(best))}."

    def class_standing(self, race):
        me = race.me
        mine = sorted(
            [me.place]
            + [o.place for o in race.opponents if o.car_class == me.car_class]
        )
        return f"P{mine.index(me.place) + 1} in class, of {len(mine)}. P{me.place} overall."

    def class_position(self, race):
        return self.class_standing(race)

    def leader(self, race):
        me = race.me
        cars = [(o.place, o) for o in race.opponents]
        overall = min(cars, key=lambda c: c[0]) if cars else None
        in_class = [
            (o.place, o)
            for o in race.opponents
            if o.car_class == me.car_class and o.place < me.place
        ]
        if not in_class:
            words = "You're leading your class."
        else:
            place, car = min(in_class, key=lambda c: c[0])
            gap = round(me.time_behind_leader - car.time_behind_leader, 1)
            words = f"Class leader is P{place}, a {car.car_model or car.car_name}, {gap} up the road."
        if (
            overall is not None
            and overall[0] < me.place
            and overall[1].car_class != me.car_class
        ):
            words += f" Overall it's a {overall[1].car_class}."
        return words

    def fastest_lap(self, race):
        me = race.me
        same = [
            o.best_lap
            for o in race.opponents
            if o.car_class == me.car_class and o.best_lap > 0
        ]
        if me.best_lap > 0:
            same.append(me.best_lap)
        if not same:
            return "No laps posted yet."
        best = min(same)
        words = f"Fastest in class {lap_text(best)}."
        if me.best_lap > 0:
            words += (
                " That's yours."
                if me.best_lap == best
                else f" Yours {lap_text(me.best_lap)}."
            )
        return words

    def penalty(self, race):
        n = race.me.penalties
        return (
            "No penalty."
            if n == 0
            else f"{n} penalty to serve."
            if n == 1
            else f"{n} penalties to serve."
        )

    def track_limits(self, race):
        steps, limit = race.me.track_limit_steps, race.session.limit_steps_per_penalty
        if not limit:
            return f"{steps} track limit steps."
        return f"{steps} of {limit} track limit steps. {max(0, limit - steps)} before a penalty."

    def settings(self, race):
        me = race.me
        bias = (
            f"Bias {round(me.brake_bias_rear * 100, 1)} rear. "
            if me.brake_bias_rear
            else ""
        )
        return f"{bias}TC {me.tc}, ABS {me.abs}, map {me.motor_map}."

    def fuel_usage(self, race):
        words = f"{round(race.me.fuel, 1)} litres in."
        used = [
            r["fuel_used"]
            for r in getattr(self.strategist, "lap_records", [])
            if r.get("fuel_used")
        ]
        if used:
            words += f" Using {round(used[-1], 1)} a lap."
        else:
            words += " Need two laps to measure the usage."
        return words

    def battery(self, race):
        if race.me.battery <= 0:
            return "No battery on this car."
        return f"Battery {round(race.me.battery * 100)} percent."

    def lap_valid(self, race):
        records = getattr(self.strategist, "lap_records", [])
        if not records:
            return "No lap timed yet."
        return "Last lap counted." if records[-1]["valid"] else "Last lap didn't count."

    def lap_time(self, race):
        me = race.me
        if me.last_lap <= 0:
            return "No timed lap yet."
        words = f"Last lap {lap_text(me.last_lap)}."
        if me.best_lap > 0:
            words += f" Best {lap_text(me.best_lap)}."
        return words
