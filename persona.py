"""The voice of Apex: the Verstappen persona, and the gate every live line must pass.

Code decides WHAT is said (a Call's conclusion and facts). The model only decides HOW it
sounds. Then the gate checks the line before it reaches the driver's ears; a line that
fails is replaced by the call's template, or dropped. Silence is always safe.

The persona is Gourav's own July design (notion_apex.md section 13), with one change he
made on 23 Sep 2026: aggressive but TIMED. It never tells him to back down from racing, it
tells him WHERE the move works, because hasty moves in close racing cost him safety rating.
"""
import os
import re
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODEL = "deepseek-v4-flash"
MAX_WORDS = 12            # the prompt asks for 10; the gate allows a little slack, never more
FAILURES_TO_OPEN = 3      # consecutive LLM failures before the circuit opens
CIRCUIT_COOLDOWN_S = 120

PERSONA = """You are APEX, Gourav's race engineer, on the team radio during a race.
You talk like Max Verstappen would if he were in the engineer's seat: his attitude, with
the precision of his engineer GP Lambiase.

MAX'S ATTITUDE
- Blunt and short. Plain words. Call him "mate".
- Zero patience for passivity: "I'm not sitting behind like a grandma." "You're not here to finish fourth."
- Contempt for the other drivers, never for Gourav. Call other drivers by their name
  or "the car ahead" / "the car behind". Never guess he or she: they are real people.
- Understatement when it's perfect: "Simply lovely." When it's bad, dry sarcasm about the
  situation or the other car, never about Gourav.
- SWEAR. It is how Max talks on the radio: "what a fucking idiot", "the fucking tyre is dead".
  Use it in most lines: fuck, fucking, shit, bloody hell, damn. Aim it at the situation or the
  other drivers, NEVER at Gourav. Calm-down lines can swear too ("Forget that shit. Next lap.").
  Write the words in full, never with asterisks: the line is read aloud.
- Aggressive but TIMED, like Max picking his moment: never tell him to back down from
  racing, tell him WHERE the move works. "Not here, mate. He's slow out of Ascari. Parabolica."

GP'S PRECISION
- Exact place and number: "Main loss is the exit of Ascari, 5 down."
- Calm and deadpan when things go wrong: "Calm down for the moment, mate. Next lap."
- Short rule-like lines: "No risk, no push."

YOUR JOB: you get "tell him", the message the team decided on, plus the facts.
Say that message in this voice. Keep its meaning exactly:
- never turn a defend into an attack, or a calm-down into a push
- never add an instruction, a blame, an opponent or a target that is not in it

VOICE RULES:
- Maximum 10 words. One line.
- Commands, not suggestions. Never hedge. Never sound worried.
- Use ONLY the facts you are given. Every number you say must be in the facts.
  Write numbers as digits (61, not sixty-one).
  Never invent a number, a cause, a setup change or a consequence.
- No greetings, no "copy", no "understood". Never ask a question.
- Never say a label like FLAG_ONCE, EXECUTABLE or PHYSICS_ABORT, or a seat name.

BANNED WORDS: think, maybe, try, consider, perhaps, "you should", "I think",
"good luck", "stay safe", manage, "back off" (back off only for PHYSICS_ABORT).

Examples of the voice:
"Not here, mate. He's slow out of Ascari. Parabolica."
"Car ahead brakes like a fucking grandma. Inside. Take it."
"Hold the inside, mate. He's got nothing on you."
"Forget that shit, mate. Car's fine. Next lap."
"Simply lovely. That's your lap."
"Fuel's fine. Push, mate. No fucking saving."
"""

CLEAN_RULE = "\nThis is a recording for other people: NO swearing at all. Keep the attitude."

BANNED = ["think", "maybe", "try", "consider", "perhaps", "you should", "i think",
          "good luck", "stay safe", "manage", "back off"]
LABELS = ["flag_once", "executable", "physics_abort", "race_engineer", "spotter",
          "strategist", "racecraft"]
PROFANITY = ["fuck", "fucking", "shit", "damn", "hell", "bastard", "bloody", "crap", "ass"]
ACKNOWLEDGEMENTS = ["copy", "understood", "roger"]
# other drivers are real people: the radio never guesses their gender (23 Sep 2026, the model
# called a rival "her" from the name alone, even when told not to)
GENDERED = ["he", "she", "him", "her", "his", "hers"]


NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}


def words_to_digits(text):
    """'sixty-one' -> '61', 'two tenths' -> '2 tenths', so spelled-out numbers get checked too."""
    words = re.split(r"(\W+)", text)
    out = []
    i = 0
    while i < len(words):
        word = words[i].lower()
        if word in NUMBER_WORDS:
            value = NUMBER_WORDS[word]
            # "sixty-one" / "sixty one": a tens word followed by a units word
            if value >= 20 and i + 2 < len(words) and words[i + 1] in ("-", " "):
                unit = NUMBER_WORDS.get(words[i + 2].lower())
                if unit is not None and unit < 10:
                    value += unit
                    i += 2
            out.append(str(value))
        else:
            out.append(words[i])
        i += 1
    return "".join(out)


def numbers_in(text):
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


def has_phrase(line, phrase):
    return re.search(r"\b" + re.escape(phrase) + r"\b", line) is not None


def gate(line, call, clean=False):
    """Returns (ok, reason). Only a line that passes every check may be spoken."""
    if not line or not line.strip():
        return False, "empty"
    lowered = line.lower()
    if len(line.split()) > MAX_WORDS:
        return False, "too long"
    for phrase in BANNED:
        if phrase == "back off" and call.kind == "PHYSICS_ABORT":
            continue
        if has_phrase(lowered, phrase):
            return False, f"banned word: {phrase}"
    for label in LABELS:
        if label in lowered.replace(" ", "_") or label in lowered:
            return False, f"label: {label}"
    for word in ACKNOWLEDGEMENTS:
        if has_phrase(lowered, word):
            return False, f"acknowledgement: {word}"
    if clean:
        for word in PROFANITY:
            if has_phrase(lowered, word):
                return False, f"profanity in clean mode: {word}"
    if "?" in line:
        return False, "asks a question"
    if "*" in line:
        return False, "asterisks would be read aloud"
    # he drives by feel and never looks at the speedo: a speed on the radio means nothing (24 Sep)
    if re.search(r"km/h|\bkph\b|\bkmh\b|kilomet|\bmph\b|miles an hour", lowered):
        return False, "says a speed"
    # a coach never tells a driver not to brake (23 Sep: "No fucking braking at Arnage")
    if re.search(r"\b(no|don'?t|never|stop)\b(\s+\w+)?\s+brak", lowered):
        return False, "tells him not to brake"
    for word in GENDERED:
        if has_phrase(lowered, word):
            return False, f"guessed a gender: {word}"
    # digits inside a name are not claims: "T11 Parabolica", "Turn 3"
    numbers_part = lowered
    for value in call.facts.values():
        if isinstance(value, str):
            for word in value.lower().split():
                numbers_part = re.sub(r"\b" + re.escape(word) + r"\b", " ", numbers_part)
    for number in numbers_in(numbers_part):
        if not number_is_backed(number, call.facts):
            return False, f"invented number: {number:g}"
    return True, "ok"


# pure number reports stay clean so the numbers are easy to hear; everything else swears
# (Gourav, 23 Sep 2026: "that part is what makes it closer to Verstappen")
CLEAN_KINDS = {"GAP_REPORT", "QUALI_LAP", "TRACK_LIMITS", "PENALTY", "PACE_TARGET"}


def facts_text(call, clean=False):
    parts = [f"tell him: {call.conclusion}"]
    if not clean and call.kind not in CLEAN_KINDS:
        parts.append("swear in this line: YES, one full swear word, aimed at the situation or the other car")
    for name, value in call.facts.items():
        parts.append(f"{name}: {value}")
    return "\n".join(parts)


class Persona:
    def __init__(self, clean=False, client=None):
        self.clean = clean
        self.client = client
        self.failures = 0
        self.circuit_open_since = None

    def llm(self):
        if self.client is None:
            self.client = OpenAI(base_url="https://aicredits.in/v1",
                                 api_key=os.environ["AICREDITS_API_KEY"],
                                 timeout=4, max_retries=0)
        return self.client

    def online(self):
        if self.circuit_open_since is None:
            return True
        if time.perf_counter() - self.circuit_open_since > CIRCUIT_COOLDOWN_S:
            self.circuit_open_since = None
            self.failures = 0
            return True
        return False

    def system_prompt(self):
        if self.clean:
            return PERSONA + CLEAN_RULE
        return PERSONA

    def phrase(self, call):
        """Ask the model for one line. Returns (line, tokens_in, tokens_out, seconds).
        line is None when the model is offline or failed."""
        if not self.online():
            return None, 0, 0, 0.0
        started = time.perf_counter()
        try:
            response = self.llm().chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": self.system_prompt()},
                          {"role": "user", "content": facts_text(call, self.clean)}],
                max_tokens=60,
                # thinking OFF: measured 23 Sep, thinking was 96% of a live line's tokens
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception as error:
            self.failures += 1
            if self.failures >= FAILURES_TO_OPEN:
                self.circuit_open_since = time.perf_counter()
                print(f"[radio: LLM offline after {self.failures} failures, template lines only]")
            print(f"[radio: LLM call failed: {error.__class__.__name__}]")
            return None, 0, 0, time.perf_counter() - started
        self.failures = 0
        seconds = time.perf_counter() - started
        tokens_in = response.usage.prompt_tokens if response.usage else 0
        tokens_out = response.usage.completion_tokens if response.usage else 0
        line = response.choices[0].message.content
        # a line cut off by the token limit is half a sentence: never say it
        if response.choices[0].finish_reason != "stop" or not line:
            return None, tokens_in, tokens_out, seconds
        return line.strip().strip('"'), tokens_in, tokens_out, seconds
