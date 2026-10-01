"""What the coach says back: reading it, checking it, and the line to say when it cannot.

split_call and split_orders read the CALL and ORDER lines off an answer (never spoken).
check_answer is the gate every answer passes before it goes on air (no banned or gendered
words, no invented numbers, no speeds, short enough); fuel_honest, trend_honest and tacked_on
catch the mistakes his races showed. fallback is the line when the model is down."""

import re

from radio.words import words_to_digits

BANNED = [
    "think",
    "maybe",
    "try",
    "consider",
    "perhaps",
    "you should",
    "i think",
    "good luck",
    "stay safe",
    "manage",
    "back off",
]
PROFANITY = [
    "fuck",
    "fucking",
    "fucked",
    "fucker",
    "fuckin",
    "shit",
    "shite",
    "shitty",
    "damn",
    "hell",
    "bastard",
    "bloody",
    "crap",
    "ass",
    "arse",
    "bollocks",
    "dickhead",
    "prick",
]
# Apex is a product now (30 Sep): swearing at the situation is a choice (Spicy), but a slur or an
# insult aimed at who someone is never goes on air, in any mode. The model said "the cunt's
# defending aggressive" about a rival on 27 Sep; nothing in the code wrote it.
SLURS = [
    "cunt",
    "cunts",
    "twat",
    "wanker",
    "retard",
    "retarded",
    "spastic",
    "fag",
    "faggot",
    "dyke",
    "tranny",
    "nigger",
    "nigga",
    "paki",
    "chink",
    "kike",
    "spic",
]
# other drivers are real people: the radio never guesses their gender (23 Sep 2026, the model
# called a rival "her" from the name alone, even when told not to)
GENDERED = ["he", "she", "him", "her", "his", "hers"]


def has_phrase(line, phrase):
    """The phrase as whole words: "he" is in "he brakes late", not in "the"."""
    return re.search(r"\b" + re.escape(phrase) + r"\b", line) is not None


def numbers_in(text):
    """Every number in a line, spelled-out ones too ("sixty-one" counts as 61)."""
    return [float(n) for n in re.findall(r"\d+(?:\.\d+)?", words_to_digits(text))]


def number_is_backed(number, facts):
    """A spoken number must match a fact, allowing for rounding (57.2 said as 57)."""
    for value in facts.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if abs(number - value) <= 0.5 or abs(number - abs(value)) <= 0.5:
            return True
        # tenths said as a whole number: 0.3 s -> "3 tenths". Only for values under 1,
        # or a fact of 5.7 would let the model say "57".
        if abs(value) < 1 and abs(number - abs(value) * 10) <= 0.5:
            return True
    return False


# asked for 35, refused only past 55: on 24 Sep every answer ran 41-50 words, got refused at 40
# and rewritten, and the rewrite lost the swearing and cost a whole extra round
MAX_WORDS = 55


# v3 5b: explain mode. "What's the plan?" or "why?" deserves more than 35 words, and he said
# he can wait for it: a longer answer and 20 s per model call instead of 10.
EXPLAIN_WORDS = (
    "explain",
    "why",
    "walk me through",
    "whats the plan",
    "what is the plan",
    "the plan",
    "strategy",
    "tell me more",
    "in detail",
    "break it down",
    "go through",
    "talk me through",
)


# he drives by feel, so no speeds on the radio - unless he asks for one (v3 5b)
SPEED_WORDS = ("speed", "km/h", "kmh", "kph", "how fast", "mph", "top speed")


CALL_WORDS = ("DEFEND", "LET BY", "ATTACK", "FOLLOW")


FALLBACK_WARNINGS = {
    "contact": "Careful: that car has already hit you.",
    "damage": "And the car's damaged.",
    "class": "Faster class coming through behind.",
    "tyres": "Tyres are cooked.",
}


FIGHT_WORDS = (
    "defend",
    "block",
    "div",
    "attack",
    "overtak",
    "pass",
    "hit",
    "let him",
    "let them",
    "behind",
    "ahead",
    "aggress",
    "fight",
    "battle",
)


def fallback(snapshot, question=""):
    """What the radio says when the model is too slow: the team's call, plus the facts that
    could change it - stated, not decided (24 Sep: "but he keeps hitting me" got a bare DEFEND
    while the provider was down). Only for a question about a fight: live 24 Sep, "what's the
    strategy for this race?" got "Team says DEFEND" because a car happened to be close."""
    asked = question.lower()
    if not any(word in asked for word in FIGHT_WORDS):
        return "Radio's lagging, mate. Ask me again."
    for side in ("behind", "ahead"):
        call = snapshot.team_calls.get(side)
        if call:
            return f"Radio's lagging, mate. Team says {call}" + call_warnings(snapshot)
    return "Radio's lagging, mate. Ask me again."


def call_warnings(snapshot):
    """The facts that could change the team call, each said as a warning."""
    words = ""
    for reason, warning in FALLBACK_WARNINGS.items():
        if reason in snapshot.override_evidence():
            words += " " + warning
    return words


def split_orders(raw):
    """The coach's ORDER lines ("ORDER: pace=push") -> ([(topic, stance)], the rest). An ORDER line
    is how the coach records a standing order he gave in words the code's phrases did not catch;
    it is never spoken, and code checks it against the orders that exist."""
    orders = []
    kept = []
    for line in (raw or "").splitlines():
        head = line.strip()
        if head.upper().startswith("ORDER:"):
            orders.extend(orders_in(head[6:]))
            continue
        kept.append(line)
    return orders, "\n".join(kept)


def orders_in(text):
    """[(topic, stance)] from "pace=push, fight=everyone"."""
    found = []
    for part in text.split(","):
        if "=" in part:
            topic, stance = part.split("=", 1)
            found.append((topic.strip().lower(), stance.strip().lower()))
    return found


def split_call(raw):
    """The model's answer -> (call, override reason, spoken text). The CALL line is never spoken."""
    lines = (raw or "").strip().splitlines()
    call = None
    override = None
    if lines and lines[0].strip().upper().startswith("CALL:"):
        head = lines[0].strip()[5:]
        lines = lines[1:]
        parts = [part.strip() for part in head.split("|")]
        word = parts[0].upper()
        if word in CALL_WORDS:
            call = word
        for part in parts[1:]:
            if part.upper().startswith("OVERRIDE:"):
                override = part[9:].strip().lower()
    return call, override, " ".join(" ".join(lines).split())


def asks_to_explain(question):
    heard = " " + re.sub(r"[^a-z0-9 ]+", " ", question.lower().replace("'", "")) + " "
    return any(" " + words + " " in heard for words in EXPLAIN_WORDS)


def asks_about_speed(question):
    lowered = question.lower()
    return any(words in lowered for words in SPEED_WORDS)


NEIGHBOUR_WORDS = (
    "car behind",
    "car ahead",
    "one behind",
    "car in front",
    "the fucker behind",
)
ABOUT_NEIGHBOURS = (
    "behind",
    "ahead",
    "in front",
    "gap",
    "catch",
    "defend",
    "attack",
    "pass",
    "fight",
    "hit",
    "dive",
    "diving",
    "him",
    "he ",
    "car",
    "p1",
    "p2",
    "p3",
    "p4",
    "p5",
    "p6",
    "p7",
    "p8",
    "p9",
    "strategy",
    "plan",
    "position",
    "place",
    "push",
    "update",
    "lost it",
    "doing ok",
    "move",
    "what the fuck",
    "straights",
    "every lap",
    "last lap",
    "risk",
)


# a plan, a place or a strategy may bring in the cars around him, but it is not a fight to call.
# Live 27 Sep, formation lap: "What's the plan for this race?" was refused for having no CALL line
# ("plan" is one of ABOUT_NEIGHBOURS) and he got "No clean answer on that one"
PLANNING_WORDS = (
    "strategy",
    "plan",
    "position",
    "place",
    "update",
    "every lap",
    "last lap",
)


def about_the_fight(question):
    """Is the question about fighting the cars around him (a CALL decision)? Words matched from
    their start ("he" is not inside "the", "div" still finds "diving")."""
    words = "".join(c if c.isalnum() else " " for c in question.lower()).split()
    text = " " + " ".join(words)
    fight_words = [
        word for word in ABOUT_NEIGHBOURS if word not in PLANNING_WORDS
    ] + list(FIGHT_WORDS)
    return any(" " + word.strip() in text for word in fight_words)


def tacked_on(question, text, in_fight):
    """True when the answer talks about the car ahead or behind although the question is not
    about them and nobody is within a second (25 Sep bank run: 45 of 78 answers did)."""
    if in_fight:
        return False
    asked = question.lower()
    if any(word in asked for word in ABOUT_NEIGHBOURS):
        return False
    said = text.lower()
    return any(word in said for word in NEIGHBOUR_WORDS)


FUEL_QUESTION = (
    "fuel",
    "energy",
    "pit",
    "box",
    "stop",
    "make it",
    "tank",
    "litre",
    "liter",
    "refuel",
)
NO_STOP_WORDS = (
    "no need to pit",
    "no stop",
    "don't need to pit",
    "dont need to pit",
    "no need to box",
    "you'll make it",
    "you will make it",
    "enough fuel",
    "fuel's fine",
    "fuel is fine",
)


CATCHING_WORDS = (
    "catching you",
    "closing on you",
    "closing you",
    "reeling you in",
    "on you by",
    "coming at you",
)
DROPPING_WORDS = (
    "dropping back",
    "falling back",
    "pulling away from you",
    "not catching you",
)


def trend_honest(text, picture):
    """(ok, reason). The direction of a SURE road trend is a fact: an answer may not say the car
    behind is catching when the race model is sure it is dropping back, or the reverse."""
    said = text.lower()
    trend = sure_trend_behind(picture)
    if trend is None:
        return True, "ok"
    if "growing" in trend and any(w in said for w in CATCHING_WORDS):
        return (
            False,
            f"the road says the car behind is NOT catching ({trend}): don't say it is",
        )
    if "catching" in trend and any(w in said for w in DROPPING_WORDS):
        return (
            False,
            f"the road says the car behind IS catching ({trend}): don't say it isn't",
        )
    return True, "ok"


def sure_trend_behind(picture):
    """The road trend to the car right behind him when the race model is sure of it (2 laps),
    else None."""
    rows = (picture or {}).get("field_around_you", [])
    for row in rows:
        if row.get("side") != "behind" or row.get("place") is None:
            continue
        trend = row.get("trend", "")
        if "sure, 2 laps" in trend and row is nearest_behind(rows):
            return trend
    return None


def nearest_behind(rows):
    """The row of the car right behind him: the best place among the cars behind."""
    nearest = None
    for row in rows:
        if row.get("side") != "behind":
            continue
        if nearest is None or row["place"] < nearest["place"]:
            nearest = row
    return nearest


def fuel_honest(question, text, picture):
    """(ok, reason). Live 25 Sep: with 0.6 laps of energy for 1.7 laps of race the coach said
    "no need to pit". The fuel verdict is code's; the answer must carry it."""
    asked = question.lower()
    if not any(word in asked for word in FUEL_QUESTION):
        return True, "ok"
    said = text.lower()
    verdict = picture.get("verdict") if isinstance(picture, dict) else None
    if verdict == "box" and not any(w in said for w in ("box", "pit")):
        return (
            False,
            "the fuel verdict is BOX THIS LAP (car tool, fuel_at_the_flag): say it first",
        )
    if verdict == "save" and not any(
        w in said for w in ("lift", "coast", "save", "short")
    ):
        return (
            False,
            "the fuel verdict is SHORT: lift and coast every braking zone, say it first",
        )
    if verdict in ("box", "save") and any(w in said for w in NO_STOP_WORDS):
        return False, "that contradicts the fuel verdict: he does NOT make it as he is"
    if verdict is None and any(w in said for w in NO_STOP_WORDS):
        return (
            False,
            "fuel usage is not measured yet: say it is not known and to check the screen, never 'no need to pit'",
        )
    return True, "ok"


PRONOUNS = [
    (r"\bhe's\b", "it's"),
    (r"\bshe's\b", "it's"),
    (r"\bhe\b", "it"),
    (r"\bshe\b", "it"),
    (r"\bhim\b", "it"),
    (r"\bhis\b", "its"),
    (r"\bhers\b", "its"),
    (r"\bher\b", "its"),
]


def neutral_pronouns(text):
    """Other drivers are "it" (the car), never he/she. Rewritten in code: on 25 Sep two answers
    were refused for "he" and each refusal cost a whole extra model round."""
    for pattern, word in PRONOUNS:
        text = re.sub(
            pattern,
            lambda m: word.capitalize() if m.group(0)[0].isupper() else word,
            text,
            flags=re.IGNORECASE,
        )
    return text


def check_answer(
    text, known_numbers, clean=False, names=(), max_words=MAX_WORDS, speeds_ok=False
):
    """(ok, reason). known_numbers: every number the tools returned or he said.
    names: the other drivers in this race; none may be said (v3, 24 Sep)."""
    if not text or not text.strip():
        return False, "empty"
    lowered = text.lower()
    reason = name_said(lowered, names)
    if reason is None:
        reason = form_problem(text, lowered, max_words, speeds_ok)
    if reason is None:
        reason = word_problem(lowered, clean)
    if reason is None:
        reason = number_problem(lowered, known_numbers)
    if reason is not None:
        return False, reason
    return True, "ok"


def name_said(lowered, names):
    """A driver's name, or a part of it of 4 letters or more: never said."""
    for name in names:
        parts = [name.lower()]
        for part in name.lower().split():
            if len(part) >= 4:
                parts.append(part)
        for part in parts:
            if has_phrase(lowered, part):
                return "says a driver's name: say the car ahead, the car behind, or its position"
    return None


def form_problem(text, lowered, max_words, speeds_ok):
    """Too long, a question back, markdown, or a speed he did not ask for."""
    if len(text.split()) > max_words:
        return f"too long: keep it to about {round(max_words * 0.65)} words"
    if "?" in text:
        return "asks a question back"
    if "*" in text or "\n-" in text:
        return "markdown would be read aloud"
    if not speeds_ok and re.search(r"km/h|\bkph\b|\bkmh\b|kilomet|\bmph\b", lowered):
        return "says a speed; use time, gaps, laps or car lengths"
    return None


def word_problem(lowered, clean):
    """He or she for a real person, a banned word, a slur (any mode), or swearing in clean mode."""
    for word in GENDERED:
        if has_phrase(lowered, word):
            return (
                f"uses '{word}' for a real person; use their name or 'the car behind'"
            )
    for phrase in BANNED:
        if has_phrase(lowered, phrase):
            return f"banned word '{phrase}'"
    for word in SLURS:
        if has_phrase(lowered, word):
            return f"slur '{word}': swear at the situation if you must, never call a person a name"
    if clean:
        for word in PROFANITY:
            if has_phrase(lowered, word):
                return "swears in clean mode"
    return None


def number_problem(lowered, known_numbers):
    """A number no tool returned and he did not say."""
    facts = {str(i): n for i, n in enumerate(known_numbers)}
    for number in numbers_in(lowered):
        if not number_is_backed(number, facts):
            return f"the number {number:g} is not in the data"
    return None


def numbers_seen(*texts):
    found = []
    for text in texts:
        found.extend(numbers_in(words_to_digits(str(text).lower())))
    return found
