"""What he said, heard: which question it is, or an order, a mark or a "copy", and whether
only the coach can answer it.

Whisper's words are fixed where it mishears racing words ("Caulif" is quali), judged garbled
or not from its own confidence, then matched against the questions the code answers by itself
(INTENTS). A question with words beyond its intent, or asking for a judgment, goes to the
coach instead (needs_agent).

Matching: every intent has phrases that ask for it. The intent with the LONGEST matching
phrase wins, so "where am I losing time" is about lap time, not position ("where am i")."""

import re

from radio.persona import words_to_digits


DEFAULT_QUIET_LAPS = 2


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


def plain_words(text):
    """His words made plain for matching: lower case, numbers as digits, no punctuation.
    (Not the "clean" of clean mode, which means no swearing.)"""
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
    for word in plain_words(text).split():
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
    heard = " " + plain_words(text) + " "
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
        for word in plain_words(text).split()
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
    words = plain_words(text).split()
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
    words = plain_words(text).split()
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
    heard = (" " + plain_words(text) + " ").replace(" " + phrase + " ", " ")
    for words in JUDGMENT_WORDS:
        if " " + words + " " in heard:
            return True
    return len(plain_words(text).split()) > FIXED_ANSWER_MAX_WORDS


def laps_asked(text):
    """ "quiet for 3 laps" -> 3."""
    match = re.search(r"(\d+)\s*lap", plain_words(text))
    if match:
        return max(1, int(match.group(1)))
    if re.search(r"\ba lap\b|\bthis lap\b", plain_words(text)):
        return 1
    return DEFAULT_QUIET_LAPS
