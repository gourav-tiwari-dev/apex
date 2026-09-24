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
from race_state import same_class_neighbours, laps_to_go

ANSWER_TTL_S = 10.0
DEFAULT_QUIET_LAPS = 2

# The fast lane has no model in it, so the Verstappen voice is written here by hand (24 Sep:
# "I need the outputs from the engineer in the same aggressive swearing Verstappen persona").
# The number comes first so it is easy to hear; the attitude comes after. Swearing is aimed at
# the situation or the other cars, never at him. Each intent takes turns through its lines.
# (swearing line, clean line)
CLOSERS = {
    "GAP_AHEAD": [("Go fucking get them.", "Go get them."), ("Reel that car in, mate.", "Reel that car in, mate."),
                  ("Hunt them down. No fucking mercy.", "Hunt them down.")],
    "GAP_BEHIND": [("Keep them in the fucking mirrors.", "Keep them in the mirrors."),
                   ("Don't give them a bloody sniff.", "Don't give them a sniff.")],
    "PACE_TO_CATCH": [("Simple as that. Fucking go.", "Simple as that. Go."),
                      ("Every lap, mate. No excuses.", "Every lap, mate. No excuses.")],
    "FUEL": [("Stop worrying about the fucking fuel.", "Stop worrying about the fuel."),
             ("Tank's not your problem, mate.", "Tank's not your problem, mate.")],
    "LAPS_LEFT": [("Head down, mate.", "Head down, mate."), ("Bring this fucking thing home.", "Bring it home.")],
    "POSITION": [("Let's fucking do better than that.", "Let's do better than that."),
                 ("Plenty of race left, mate.", "Plenty of race left, mate.")],
    "LAP_TIME": [("Simply lovely. Again.", "Simply lovely. Again."), ("More of that shit, mate.", "More of that, mate.")],
}

INTENTS = {
    "GAP_AHEAD": ["gap", "the gap", "ahead", "in front", "car in front", "gap ahead", "gap in front", "gap to the car ahead"],
    "GAP_BEHIND": ["behind", "car behind", "gap behind", "who is behind", "whos behind", "gap back"],
    "PACE_TO_CATCH": ["catch", "catch him", "catch them", "catch the car ahead", "what lap time do i need", "lap time do i need",
                      "what pace do i need", "pace do i need", "need to do"],
    "FUEL": ["fuel", "energy", "tank", "enough fuel", "lift and coast", "save fuel"],
    "LAPS_LEFT": ["laps left", "how many laps", "laps to go", "time left", "how long left", "how long to go"],
    "WHERE_LOSING": ["losing time", "where am i losing", "where am i slow", "slow", "improve", "faster",
                     "where can i gain", "gain time"],
    "POSITION": ["position", "what position", "where am i", "what place", "what p"],
    "LAP_TIME": ["lap time", "last lap", "my lap", "best lap", "my time"],
    "QUIET": ["quiet", "be quiet", "shut up", "silence", "mute", "leave me", "stop talking"],
    "RADIO_ON": ["radio on", "back on", "talk to me", "unmute", "radio back"],
}


def clean(text):
    text = words_to_digits(text.lower())
    text = text.replace("'", "")
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


def intent_of(text):
    """The intent with the longest phrase found in what he said, or None."""
    heard = " " + clean(text) + " "
    best = None
    best_length = 0
    for intent, phrases in INTENTS.items():
        for phrase in phrases:
            if " " + phrase + " " in heard and len(phrase) > best_length:
                best = intent
                best_length = len(phrase)
    return best


# the fixed list answers short questions; anything longer is a real question for the agent
# ("the car behind is diving at me, he's 2 seconds faster, defend or let him go?" contains
# "behind" but is not asking for the gap)
FIXED_ANSWER_MAX_WORDS = 7
ALWAYS_FIXED = {"QUIET", "RADIO_ON"}


# "can I catch him?" is a judgment, not a lookup: on 24 Sep the fixed list answered it with
# the bare gap. Questions that ask what to DO go to the agent however short they are.
# ...anywhere in the question: "realistically which position can we get" got a bare "P5"
JUDGMENT_WORDS = ("can i", "can we", "should", "could", "do i", "what do i", "how do i", "why",
                  "is it worth", "will i", "will we", "realistic", "what if",
                  # a fight is a judgment too: live 24 Sep, "car ahead is defending" got the bare gap
                  "defending", "defend", "blocking", "block", "diving", "dive", "attack", "attacking",
                  "overtake", "pass", "passing", "hitting", "hit me", "let him", "let them", "aggressive",
                  "aggressively")


def needs_agent(text):
    intent = intent_of(text)
    if intent in ALWAYS_FIXED:
        return False
    if intent is None:
        return True
    heard = " " + clean(text) + " "
    for words in JUDGMENT_WORDS:
        if " " + words + " " in heard:
            return True
    return len(clean(text).split()) > FIXED_ANSWER_MAX_WORDS


def laps_asked(text):
    """ "quiet for 3 laps" -> 3. """
    match = re.search(r"(\d+)\s*lap", clean(text))
    if match:
        return max(1, int(match.group(1)))
    return DEFAULT_QUIET_LAPS


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
        self.said = {}            # intent -> how many times answered: the closers take turns

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
            words = self.fuel()
        elif intent == "WHERE_LOSING":
            seat = "performance"
            words = self.where_losing()
        elif intent == "QUIET":
            laps = laps_asked(text)
            self.governor.quiet_until_lap = lap + laps
            words = f"Fine, I'll shut up for {laps} laps. Spotter stays on."
        elif intent == "RADIO_ON":
            self.governor.quiet_until_lap = None
            words = "Radio's back, mate."
        else:
            words = getattr(self, intent.lower())(race)
        # the attitude line; "stop worrying about the fuel" only when the fuel IS fine
        if intent in CLOSERS and race is not None and race.me is not None:
            if intent != "FUEL" or "fine" in words:
                words += self.closer(intent)
        return Call(seat=seat, kind="ANSWER_" + (intent or "UNHEARD"), sim_time=now,
                    priority=RACE_CONTROL, ttl=ANSWER_TTL_S, conclusion=words, template=words,
                    facts={"heard": text}, asked=True, phrase=False)

    def gap_ahead(self, race):
        ahead, gap, behind, gap_behind = same_class_neighbours(race)
        if ahead is None or gap is None:
            return "Nobody ahead in your class. You're leading it."
        words = f"Car ahead, {round(gap, 1)}."
        if ahead.last_lap > 0:
            words += f" Lapping {lap_text(ahead.last_lap)}."
        return words

    def gap_behind(self, race):
        ahead, gap_ahead, behind, gap = same_class_neighbours(race)
        if behind is None or gap is None:
            return "Nobody behind in your class."
        words = f"Car behind, {round(gap, 1)}."
        if behind.last_lap > 0:
            words += f" Lapping {lap_text(behind.last_lap)}."
        return words

    def pace_to_catch(self, race):
        ahead, gap, behind, gap_behind = same_class_neighbours(race)
        if ahead is None or gap is None:
            return "Nobody ahead to catch. Just bring it home."
        to_go = self.engineer.to_go_at_line
        if to_go is None or to_go < 1 or ahead.last_lap <= 0:
            return f"Car ahead is {round(gap, 1)} up. Need a timed lap first."
        target = ahead.last_lap - gap / to_go
        return f"You need {lap_text(target)} to catch the car ahead by the flag. It's doing {lap_text(ahead.last_lap)}."

    def fuel(self):
        picture = self.strategist.fuel_now
        if picture is None:
            return "Need two laps to measure the fuel."
        spare = picture["spare_laps"]
        what = "Fuel" if picture["limit"] == "fuel" else "Energy"
        if spare >= 0.5:
            return f"{what}'s fine. {spare} laps spare. Push."
        if spare >= 0:
            return f"{what}'s tight, {spare} laps spare. Lift and coast into the big stops."
        return f"{what}'s short by {abs(spare)} laps. Lift and coast every braking zone."

    def laps_left(self, race):
        to_go = self.engineer.to_go_at_line
        if to_go is None:
            to_go = laps_to_go(race, race.me.last_lap if race.me.last_lap > 0 else None)
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
        return f"P{race.me.place}."

    def lap_time(self, race):
        me = race.me
        if me.last_lap <= 0:
            return "No timed lap yet."
        words = f"Last lap {lap_text(me.last_lap)}."
        if me.best_lap > 0:
            words += f" Best {lap_text(me.best_lap)}."
        return words
