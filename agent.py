"""The race engineer who can answer anything (M9 part 2, 24 Sep 2026).

The fixed push-to-talk list (answers.py) covers the everyday questions in ~50 ms. Everything
else - "the car behind is diving at me, he's 2 seconds faster, do I defend or let him go?" -
comes here: a model with tools that look at the live race, decides, and answers in the
engineer's voice. Gourav: "in real racing there will be countless possible questions and
only an agent can answer them. I can tolerate some latency."

Measured on aicredits (24 Sep), three real questions at lap 4 of his 23 Sep race:
  thinking ON  6.0-9.5 s, Rs 0.29-0.46 a question
  thinking OFF 2.1-4.5 s, Rs 0.12-0.35 a question, the same calls on all three
so thinking is off. Most of the cost is INPUT: every tool round sends the whole conversation
again, so the race picture goes with the question (one round fewer) and empty fields are
dropped from what the tools return.

How it stays honest:
  - The tools read a SNAPSHOT taken the moment he asked (built on the main thread), so the
    agent never reads the seats while the race loop is changing them.
  - Every number in the answer must come from a tool result or from his own question, and the
    answer passes its own gate (no speeds, no he/she for other drivers, no hedging). A refused
    answer gets one rewrite with the reason; a second refusal is replaced by an honest
    "no clean answer" line.
"""
import json
import math
import os
import re
import threading
import time
from queue import Queue, Empty

from persona import MODEL, BANNED, PROFANITY, GENDERED, numbers_in, number_is_backed, words_to_digits, has_phrase
from radio import Call, RACE_CONTROL
from race_state import same_class_neighbours, laps_to_go
import race_tools

MAX_ROUNDS = 4                 # tool rounds before it must answer
# asked for 35, refused only past 55: on 24 Sep every answer ran 41-50 words, got refused at 40
# and rewritten, and the rewrite lost the swearing and cost a whole extra round
MAX_WORDS = 55
ANSWER_TTL_S = 30.0            # an answer about the race 30 s ago is out of date
MAX_TOKENS = 1500
# 24 Sep: the provider went erratic - three identical calls took 60 s (timed out), 26 s, 1.3 s -
# and an answer came 38 s after the question. On track that is worse than no answer, so each
# model call gets 10 s, and past that the team's own call is said at once (see fallback()).
MODEL_TIMEOUT_S = 10
FOLLOW_UP_WINDOW_S = 60.0     # a question within a minute of the last one may be a follow-up
FOLLOW_UP_EXCHANGES = 3       # v3 5b: "and what about the one behind?" can lean on 3 exchanges

# v3 5b: explain mode. "What's the plan?" or "why?" deserves more than 35 words, and he said
# he can wait for it: a longer answer and 20 s per model call instead of 10.
EXPLAIN_WORDS = ("explain", "why", "walk me through", "whats the plan", "what is the plan", "the plan",
                 "strategy", "tell me more", "in detail", "break it down", "go through", "talk me through")
MAX_WORDS_EXPLAIN = 110
HEAVY_TIMEOUT_S = 20
# he drives by feel, so no speeds on the radio - unless he asks for one (v3 5b)
SPEED_WORDS = ("speed", "km/h", "kmh", "kph", "how fast", "mph", "top speed")

AGENT_PROMPT = """You are APEX, Gourav's race engineer. He just asked you something on the radio,
mid-session, in an online ranked race in Le Mans Ultimate. He drives a GT3 on a controller.
Safety rating matters: contact costs him rating. From his own words: in close racing he gets
hasty and commits too early (my_habits has the measured numbers).

YOUR VOICE: Max Verstappen's attitude in the engineer's seat, with GP Lambiase's precision.
Blunt, short, plain words, call him "mate". Commands, not suggestions. Contempt for the
situation and the other cars, never for Gourav. Dry sarcasm when it's bad, "simply lovely"
when it's perfect. Aggressive but TIMED: never "give up", always WHERE and WHEN.

SWEAR IN THIS ANSWER, at least once, the way Max does on the radio - fuck, fucking, shit,
bloody, damn - aimed at the situation or the other cars, NEVER at Gourav. Words in full,
never with asterisks. Examples of the voice:
  "Let the car behind go, mate. A fucking second a lap quicker, you won't hold that. Clean exit, then chase P4."
  "No. 3 seconds with 3 laps left is fucking fantasy. Hold P5, defend one line into the Esses."
  "Understeer on entry at Arnage, mate. Brake a touch earlier, trail it in, stop fighting the bloody wheel."

HOW TO ANSWER
1. Get the facts with the tools first. Never guess a gap, a lap time, a lap count or someone's
   pace. If he states a number ("he's 2 seconds faster"), check it; if the data disagrees, say so.
2. Make ONE clear call and give the reason: what to do, where, and why.
3. Racecraft judgment, like a real pit wall:
   - A car more than about a second a lap quicker will get by anyway. Fighting it costs you
     both time and risks contact: let it by cleanly (a straight or a corner exit), then stay
     with it or focus on the car ahead.
   - A car on similar pace: defend ONE line into the braking zone of its strong corner. One
     move only, no weaving, no moving under braking.
   - Never recommend a dangerous move: no brake-testing, no weaving, no moving in the braking zone,
     and NEVER "lift" or slow down in front of a car that is diving: letting a car by means
     holding a predictable line and not covering the inside, never lifting in its path.
   - Never end on a target the maths rules out: if race_maths says a car ahead is out of reach,
     do not tell him to chase it.
   - Weigh laps left, the gap to the car ahead, the place at stake and his contact history.
4. Apex cannot see mirrors, racing lines or intentions. If the data cannot answer something,
   say what you CAN see and answer from that.
5. The race maths is DONE for you in race_picture ("race_maths", "fight"): use those numbers,
   never do your own arithmetic, so every answer in a race agrees with the last one.
   In a fight, race_picture has a "team_call" for that car (DEFEND / LET BY / ATTACK / FOLLOW).
   That is the pit wall's RECOMMENDATION from the numbers. Follow it, unless the data shows one
   of these, which the numbers alone do not see - then you may override it:
     damage   the car has damage (car tool)
     contact  that driver has hit him this race or has contact history with him (driver tool)
     class    a faster-class car is closing (race_picture "other_class_cars_near")
     tyres    his tyres are overheating (car tool)
   An override must be said out loud with its reason ("Team says defend, but that car's already
   hit you twice: let it go"). Code checks every override against the data.
   Never promise a later call ("I'll tell you where"): nothing will call him back. Say where NOW.
   Never tell him to let a car by unless its team call (or team_call_when_it_reaches_you) says
   LET BY, or you override it for a reason the data backs. NEVER let a SLOWER car by. One
   contact alone does not justify giving a place: the car must also be clearly quicker, or have
   hit him twice. If he says he wants to fight it, help him fight it cleanly.
6. A missing fact stays missing: if a pace or lap time says "not known", say you don't have it.
   Never fill it in ("same pace") from nothing.
7. Answer the question he asked: asked for a lap time, give the lap time first. Mention the
   car ahead or behind ONLY when it changes what he should do (on 24 Sep a tyre question got a
   warning about the car behind tacked on: noise).
8. Handling questions (understeer, oversteer, the rear stepping out): the corner tool has a
   measured "balance" per phase (entry, mid, exit) once Apex has 3 laps there - use it, and
   if it disagrees with what he feels, say what the data shows. Without it, say Apex has not
   measured that corner yet, then give the standard driver fix for the phase
   he names - entry: brake a touch earlier and in a straighter line, trail the brake to keep
   the nose loaded, less steering; mid-corner: be patient, wait for the car to turn before
   the throttle; exit: straighten the wheel before full throttle. Use the corner data only if
   it agrees; never answer a balance question with an unrelated speed diagnosis.

9. The wider tools (anything he asks, push-to-talk is his last resort):
   standings (every car, class positions), car (full: fuel, energy, tyre temps, pressures,
   wear, brakes, damage, settings), session (weather, time left, flags), lap_history (his laps
   and sectors this session), race_events (offs, contacts, passes, what the radio said),
   setup (in-car settings and evidence-based advice), strategy (the plan: fuel, who to catch,
   who is coming, where the time is), knowledge (rules, flags, penalties, ratings, technique,
   what Apex can see), calculator (ANY sum: never do arithmetic in your head),
   remind_me (a reminder on a later lap), database (read-only SQL over every past session:
   the last resort, for questions about past races).
   Rules and penalties come ONLY from the knowledge tool. A fact it marks UNVERIFIED is said
   as "not confirmed". Never invent a rule, a number or a penalty.
   Never promise a change to how the radio works: the only switches are "quiet for N laps",
   "radio back on" and remind_me. The knowledge tool's "The radio itself" section says what
   the radio already does.
   Brake words mean different things: "brake later" / "brake earlier" is the braking POINT;
   "off the brake earlier, let it roll" means he releases too late and over-slows mid-corner,
   his braking point is fine. Never answer "am I braking too early" with the release advice.
   THE RACE MODEL (race_picture): "field_around_you" = the cars 3 places either side, measured on
   the road: same-point gap, the trend ("sure, 2 laps" or "1 lap only, NOT sure") and whether
   one catches the other ("yes, within N laps", before the flag or not). "battles_near_you",
   "just_pitted_near_you". The driver tool has "corners_where_they_gain_time_s" and
   "corners_where_you_gain_time_s": seconds through each corner, from the road - set a pass up
   where he gains, defend where they gain. Between fighting cars the gap moves ~1.2 s a lap for
   reasons that are not pace: a "NOT sure" trend is said as "early to tell", never as a fact.
   A catch time is an upper bound ("within N laps"), never an exact lap.
10. When the data does not have it, say EXACTLY which data is missing ("the game doesn't send
   other cars' tyre wear", "no timed lap yet"), then the best call from what IS known.

THE SPOKEN ANSWER (it is read aloud to him while he drives)
- At most 3 short sentences, about 35 words. The call first. (When he asks to explain, or
  for the plan: up to about 70 words, the reasons in order.)
- NEVER say a speed or km/h unless he asked about speed: he drives by feel. Use time, laps,
  gaps, corners, car lengths.
- Every number must come from a tool result or from his question. Write numbers as digits.
- His position is ONLY race_picture "place" (on 24 Sep an answer said P5 when he was P4:
  5 was another car's place). Never take his place from another car's data.
- NEVER say a driver's name: he can't look names up mid-race and the voice mispronounces them.
  Say "the car ahead", "the car behind", or its position ("P9"). Never he, she, him, her or his.
- No questions back. No "maybe", "try", "consider", "think", "perhaps", "manage", "back off".
- No asterisks, no lists, no markdown.

FORMAT: the FIRST line is for the pit wall and is never read out:
  CALL: DEFEND            (or LET BY, ATTACK, FOLLOW - the call you are making)
  CALL: LET BY | OVERRIDE: contact     (when you override the team call; reason = damage, contact, class or tyres)
  CALL: NONE              (no fight in the question)
Then a new line, then the spoken answer."""

CLEAN_RULE = "OVERRIDE: stay clean. No swearing at all in this answer, whatever the examples say."
VOICE_REMINDER = ("Answer ONLY this question, in Max's voice: blunt, 'mate', and SWEAR in this answer "
                  "(fuck, fucking, shit, bloody), aimed at the situation or the other cars, never at Gourav. "
                  "About 35 words.")
VOICE_REMINDER_CLEAN = "Answer ONLY this question, in Max's voice: blunt, 'mate', no swearing. About 35 words."
# 25 Sep bank run: "the car behind is 0.7 a lap quicker" was tacked onto ~45 of 78 answers
# (tyres, ABS, sectors, history) although rule 7 forbids it. The rule now sits next to the
# question, and code refuses the answer when it happens anyway.
NO_TACK_ON = ("Do NOT mention the car ahead or behind unless the question is about them or a car is "
              "within 1 second (IN A FIGHT).")

TOOLS = [
    {"name": "race_picture",
     "description": "ALREADY SENT with the question; call it only to refresh. "
                    "The race right now: session, laps to go, his place, his last and best lap, "
                    "the same-class cars just ahead and behind with gaps, their last laps and how "
                    "the gaps changed over the last lap, flags, and whether the radio is on quiet.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "driver",
     "description": "One other driver: place, gap to him, last and best lap, pace difference per "
                    "lap, corners where they are quicker or slower than him, and the history "
                    "between them (this race and past races).",
     "parameters": {"type": "object", "properties": {
         "who": {"type": "string", "description": "'ahead', 'behind', or the driver's name"}},
         "required": ["who"]}},
    {"name": "my_habits",
     "description": "What team memory knows about Gourav from past races: habits by corner, lap 1, "
                    "pass attempts and how they ended, clean-race record. Every fact is measured.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "corner",
     "description": "One corner: his time there against his own best and the fastest car, the one "
                    "thing to change, and his history there.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string", "description": "the corner's name"}},
         "required": ["name"]}},
    {"name": "car",
     "description": "His car in full: fuel litres and spare at the flag, virtual energy, battery, tyre "
                    "compound, temperatures (average and inner/centre/outer), pressures, wear, brake "
                    "temperatures, damage, brake bias, TC, ABS, motor map, anti-roll bars, track-limit "
                    "steps against the penalty limit, penalties, pit stops.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "standings",
     "description": "Every car in order: overall and class position, class, car model, gap to the "
                    "leader or laps down, best and last lap, pit stops. His row says you.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "session",
     "description": "Track, session, phase, time left, laps to go, air and track temperature, rain, "
                    "wetness, grip, yellow flags, blue flag for him.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "lap_history",
     "description": "His laps this session: times, sectors, fuel used per lap, valid or not, best, "
                    "average and spread of the last 3, best sectors and the best possible lap.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "race_events",
     "description": "What happened to him this session: offs, spins, contacts, lock-ups, track "
                    "limits, pass attempts and how they ended, the last lines the radio said.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "setup",
     "description": "His in-car settings now (brake bias, TC, ABS, motor map) and the setup "
                    "engineer's advice from this session's evidence.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "strategy",
     "description": "The plan, worked out by code: fuel or energy to the flag, whether the car "
                    "ahead can be caught, whether the car behind is coming, tyres, and the corners "
                    "where the time is. Use it for 'what's the plan', push or save, what to do now.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "knowledge",
     "description": "Rules and know-how: flags, track limits, penalties, safety and driver rank, "
                    "starts, safety car, tyres, car balance fixes, in-car settings, tow, what Apex "
                    "can and cannot see. Facts marked UNVERIFIED are not confirmed.",
     "parameters": {"type": "object", "properties": {
         "topic": {"type": "string", "description": "what he asked about, in a few words"}},
         "required": ["topic"]}},
    {"name": "calculator",
     "description": "Arithmetic: + - * / ** %, brackets, min max round abs ceil floor. Use it for "
                    "every sum instead of working it out yourself.",
     "parameters": {"type": "object", "properties": {
         "expression": {"type": "string", "description": "e.g. (242.1 - 240.5) * 3"}},
         "required": ["expression"]}},
    {"name": "remind_me",
     "description": "Set a reminder the radio says at the start of a later lap, e.g. 'box this lap' "
                    "or 'check fuel'. Only when he asks for a reminder.",
     "parameters": {"type": "object", "properties": {
         "lap": {"type": "integer", "description": "the lap number to say it on"},
         "what": {"type": "string", "description": "the reminder, a few words"}},
         "required": ["lap", "what"]}},
    {"name": "database",
     "description": "LAST RESORT, for past races only: one read-only SQL SELECT over apex.db. Tables: "
                    "sessions(id, started_at, track, session_type, final_place, grid, car_class, car_model), "
                    "events(session_id, kind, sim_time, lap_count, corner, other_car), "
                    "radio_log(session_id, sim_time, seat, kind, status, line), "
                    "pass_attempts(session_id, driver, corner, lap_count, outcome), "
                    "rivals_seen(session_id, driver, car_class, best_lap, final_place), "
                    "profile_facts(kind, track, subject, summary). session_type 10-13 = race.",
     "parameters": {"type": "object", "properties": {
         "sql": {"type": "string", "description": "one SELECT, at most 30 rows come back"}},
         "required": ["sql"]}},
    {"name": "track_ahead",
     "description": "The next corners from where he is now, in order, with the distance to each, "
                    "and whether the cars around him are quicker or slower there.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
]


CALL_WORDS = ("DEFEND", "LET BY", "ATTACK", "FOLLOW")
OVERRIDE_REASONS = ("damage", "contact", "class", "tyres")
HOT_TYRE_C = 105              # the strategist's "cooking" line
OTHER_CLASS_NEAR_M = 400      # an other-class car this close behind is about to arrive


FALLBACK_WARNINGS = {
    "contact": "Careful: that car has already hit you.",
    "damage": "And the car's damaged.",
    "class": "Faster class coming through behind.",
    "tyres": "Tyres are cooked.",
}


FIGHT_WORDS = ("defend", "block", "div", "attack", "overtak", "pass", "hit", "let him", "let them",
               "behind", "ahead", "aggress", "fight", "battle")


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
            words = f"Radio's lagging, mate. Team says {call}"
            for reason in ("contact", "damage", "class", "tyres"):
                if reason in snapshot.override_evidence():
                    words += " " + FALLBACK_WARNINGS[reason]
            return words
    return "Radio's lagging, mate. Ask me again."


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


def lap_text(seconds):
    if seconds is None or seconds <= 0:
        return None
    minutes = int(seconds // 60)
    rest = round(seconds - minutes * 60, 1)
    if rest >= 60.0:               # 239.96 s is 4:00.0, not 3:60.0
        minutes += 1
        rest = round(rest - 60.0, 1)
    return f"{minutes}:{rest:04.1f}"


NOT_A_FIGHT_S = 1.0     # further apart than this, nobody is diving at anybody yet


def recent_lap(car):
    """(lap time, where it came from). The game posts -1 for a lap it did not count
    (Kossman on 23 Sep), so the best lap stands in, labelled as such. A car that has only
    done lap 1 has no pace yet: lap 1 carries the start."""
    if car.laps < 2:
        return None, None
    if car.last_lap > 0:
        return car.last_lap, "last lap"
    if car.best_lap > 0:
        return car.best_lap, "best lap, last lap not posted"
    return None, None


# From 278 fights on his four race tapes (tools/pass_study.py, 25 Sep): how often the car behind
# got past within a lap, by how much quicker it was on the road. Recompute as tapes grow.
PASS_ODDS = [(-99.0, "slower than you", 0.45), (-0.5, "about your pace", 0.55),
             (0.5, "0.5 to 2 s a lap quicker", 0.81), (2.0, "2+ s a lap quicker", 0.86)]


def pass_odds(quicker):
    """(words, share) for a car behind that is `quicker` s a lap quicker on the road."""
    found = PASS_ODDS[0]
    for row in PASS_ODDS:
        if quicker >= row[0]:
            found = row
    return found[1], found[2]


CONTACT_LET_BY_QUICKER_S = 0.5   # contact once + at least this much quicker: a let-by is fair
CATCH_UPPER = 1.5     # field study (25 Sep): 20 of 21 sure forecasts were caught, the time was
                      # off by a median 64%, and within 1.5x the forecast 95% of the time for
                      # forecasts over a minute: say WHETHER and an upper bound, never "in 1.6 laps"


def catch_words(laps, to_go):
    within = max(1, math.ceil(laps * CATCH_UPPER))
    words = f"yes, within {within} laps at this trend"
    if to_go is not None:
        if within <= to_go:
            words += ", before the flag"
        elif laps > to_go:
            words += ", but NOT before the flag"
        else:
            words += ", maybe before the flag (tight)"
    return words


def pace_pair(car, theirs, source, mine, engineer, racecraft, model=None):
    """(their lap, my lap, measured?, how it was measured) for the pace comparison.
    First choice: the race model's road trend (same-point gaps, median of 8 stretches a lap, over
    two laps when it has them). The field study of his tapes: between fighting cars the gap moves
    ~1.2 s a lap for reasons that are not pace; with 2 laps of trend the direction was right ~80%.
    One lap = "not sure": it can inform, never decide a let-by. Then two posted laps. Else unknown."""
    base = mine or engineer.my_lap or 240.0          # only the difference matters to the maths
    if model is not None:
        found = model.quicker(car.id, "me")
        if found is not None:
            quicker, sure = found
            how = ("measured on the road over the last 2 laps" if sure
                   else "on the road over 1 lap only: NOT SURE yet, say so")
            return round(base - quicker, 2), base, sure, how
    quicker = None
    if racecraft is not None:
        quicker = racecraft.clock.pace_vs_me(car.id)
    if quicker is not None:
        return round(base - quicker, 2), base, True, "measured on the road over the last lap"
    if theirs is not None and mine is not None:
        clean = source == "last lap"
        return theirs, mine, clean, "from lap times" if clean else "from their best lap, their last not posted"
    return None, None, False, None


def my_pace(me, engineer):
    """His lap for pace comparisons: the game's last lap, or Apex's own clock for it when the
    game posted -1. Never his best lap: live 25 Sep his lap 2 was invalid, his only valid lap
    was lap 1 with the start (4:17.9), and the coach said the car behind was "17 seconds a lap
    quicker" three times. Lap 1 is never pace."""
    if me.laps < 2:
        return None
    if me.last_lap > 0:
        return me.last_lap
    return engineer.my_lap


def race_maths(side, gap, their_lap, my_lap, laps_to_go):
    """The arithmetic, done by code so every answer uses the same numbers (on 24 Sep the model
    said "hunt Kossman" and "you won't catch him" a minute apart)."""
    maths = {}
    per_lap = round(their_lap - my_lap, 2)          # > 0: I am quicker
    if side == "ahead":
        if laps_to_go:
            needed = gap / laps_to_go
            maths["to_catch_by_the_flag"] = (f"find {round(needed, 1)} s a lap on them: "
                                             f"a {lap_text(their_lap - needed)} lap, every lap")
        if per_lap > 0:
            laps = round(gap / per_lap, 1)
            maths["at_this_pace"] = (f"you reach them in about {laps} laps: "
                                     + ("before the flag" if laps_to_go and laps <= laps_to_go else "not before the flag"))
        else:
            maths["at_this_pace"] = "you are not catching them"
    else:
        if per_lap < 0:
            laps = round(gap / -per_lap, 1)
            maths["at_this_pace"] = (f"they reach you in about {laps} laps: "
                                     + ("before the flag" if laps_to_go and laps <= laps_to_go else "not before the flag"))
            maths["to_keep_them_behind"] = f"lap {lap_text(their_lap)} or quicker"
        else:
            maths["at_this_pace"] = "they are not catching you"
    return maths


# The fight call is the team's decision, made by code; the model explains it and says where.
# 24 Sep, his situation tests: on the LAST LAP with a car 0.2 s behind and only 0.3 s a lap
# quicker, the model said "that fight is lost, let it go" - and in the sandwich it said "you
# won't hold that" and "hold P5" in one breath. Left to itself it leans to "let it go".
# his rule, live 25 Sep: "nobody gets past without a fight unless someone is genuinely fast or
# my situation is bad or conditions say so". So LET BY needs a car at least 2 s a lap quicker,
# MEASURED on the road over a lap (was 1.0 s, from lap times); damage, contact, a faster class
# and hot tyres stay the override reasons.
LET_BY_QUICKER_S = 2.0
ATTACK_QUICKER_S = 0.2        # this much quicker than the car ahead, in a fight: go


def team_call(side, gap, their_lap, my_lap, laps_to_go, measured=True):
    """DEFEND / LET BY / ATTACK / FOLLOW for a car within a second, or None outside a fight.
    measured: the pace came from the road (or two clean laps); without it, never LET BY."""
    if gap >= NOT_A_FIGHT_S:
        return None
    last_lap = laps_to_go is not None and laps_to_go <= 1
    if side == "behind":
        if last_lap:
            return "DEFEND: last lap, every place counts. One line, no weaving, no moving in the braking zone."
        if measured and their_lap is not None and my_lap is not None and my_lap - their_lap >= LET_BY_QUICKER_S:
            quicker = round(my_lap - their_lap, 1)
            return (f"LET BY: {quicker} s a lap quicker, it gets by anyway. Hold a predictable line, "
                    "don't cover the inside, never lift in its path, then stay with it.")
        return "DEFEND: similar pace, this place is yours to keep. One line into its strong corner, no weaving."
    if their_lap is not None and my_lap is not None and their_lap - my_lap >= ATTACK_QUICKER_S:
        return ("ATTACK: you are quicker. Set it up on the exit of a corner where you carry more speed "
                "and pass into the next braking zone. One clean move, no lunge.")
    return "FOLLOW: not quicker than this car. Stay close, pressure, wait for the mistake, no lunge."


# Directions go to the model in WORDS, never as a signed number: on 24 Sep it read
# "quicker_per_lap_s: -0.4" as "0.4 a lap quicker" when the car was 0.4 slower.
def pace_words(their_lap, my_lap):
    difference = round(my_lap - their_lap, 1)
    if difference > 0:
        return f"{difference} s a lap quicker than you"
    if difference < 0:
        return f"{-difference} s a lap slower than you"
    return "the same pace as you"


def trend_words(side, shrink):
    """shrink: how much the gap got smaller since the last time he crossed the line."""
    amount = abs(round(shrink, 1))
    if amount < 0.1:
        return "gap steady since the last lap"
    if side == "ahead":
        if shrink > 0:
            return f"you are catching: {amount} s gained since the last lap"
        return f"pulling away from you: {amount} s lost since the last lap"
    if shrink > 0:
        return f"closing on you: {amount} s since the last lap"
    return f"dropping back: {amount} s since the last lap"


def road_words(me, opponent):
    from race_state import same_lap
    if not same_lap(me, opponent):
        return "on a different lap"
    gap = round(me.time_behind_leader - opponent.time_behind_leader, 1)
    if gap > 0:
        return f"{gap} s ahead of you"
    return f"{-gap} s behind you"


# ---- the snapshot: everything the tools can see, taken on the main thread when he asks ------

class Snapshot:
    """Built by the race loop the moment he asks. Plain dicts only, so the agent's thread
    reads a still picture while the race moves on."""

    def __init__(self, race, lap, lap_dist, corners, engineer, strategist, performance,
                 racecraft, governor, habits, contacts_this_race, db_path=None, session_id=None, model=None):
        self.model = model                 # the race model: the one picture (25 Sep)
        self.quicker_by = {}               # "ahead" / "behind" -> s a lap that car is quicker than me
        self.corners_map = corners or []
        self.picture = self.race_picture(race, lap, engineer, governor, racecraft)
        self.lap = lap
        self.race = race
        self.db_path = db_path
        self.session_id = session_id
        self.actions = []              # reminders he asked for; the race loop carries them out
        self.standings = race_tools.standings(race)
        self.session = race_tools.session_info(race, self.picture.get("laps_to_go"))
        self.laps = race_tools.lap_history(list(getattr(strategist, "lap_records", [])))
        self.drivers = self.every_driver(race, performance, racecraft, contacts_this_race, engineer)
        self.habits = habits
        self.corners = self.every_corner(performance)
        self.car_state = self.car(race, strategist)
        self.car_state.update(race_tools.full_car(race, strategist))
        self.ahead_of_me = self.track_ahead(lap_dist, corners, race, racecraft)
        self.picture["other_class_cars_near"] = self.other_class_near(race, lap_dist, corners)
        self.team_calls = {side: self.picture[side]["team_call"] for side in ("ahead", "behind")
                           if self.picture.get(side) and self.picture[side].get("team_call")}
        self.contacts_in_fight = 0
        self.history_in_fight = ""
        for side in self.team_calls:
            entry = self.drivers.get(side, {})
            self.contacts_in_fight += entry.get("contacts_with_you_this_race", 0) or 0
            self.history_in_fight += " " + (entry.get("history") or "")

    def other_class_near(self, race, lap_dist, corners):
        """Cars of another class just behind him on the road: the ones about to lap him."""
        lengths = [c["end"] for c in corners or []] + [o.lap_dist for o in race.opponents] + [lap_dist]
        lap_length = max(lengths) if lengths else 0
        near = []
        for opponent in race.opponents:
            if opponent.car_class == race.me.car_class or opponent.in_pits or lap_length <= 0:
                continue
            behind_m = (lap_dist - opponent.lap_dist) % lap_length
            if behind_m <= OTHER_CLASS_NEAR_M:
                near.append({"driver": opponent.driver, "class": opponent.car_class,
                             "metres_behind": round(behind_m)})
        return near

    def override_evidence(self):
        """The override reasons the data actually supports right now."""
        supported = set()
        if self.car_state.get("damage"):
            supported.add("damage")
        if self.contacts_in_fight > 0 or "contact" in self.history_in_fight.lower():
            supported.add("contact")
        if self.picture.get("other_class_cars_near"):
            supported.add("class")
        if any(t > HOT_TYRE_C for t in self.car_state.get("tyre_temps_c", [])):
            supported.add("tyres")
        return supported

    def check_call(self, call, override):
        """(ok, reason): in a fight the call must be the team's, or an override the data backs."""
        if not self.team_calls:
            return True, "ok"
        team_words = {text.split(":")[0] for text in self.team_calls.values()}
        if call is None:
            return False, "start with the CALL line (CALL: DEFEND / LET BY / ATTACK / FOLLOW), then the answer"
        if call in team_words and override is None:
            return True, "ok"
        # live 25 Sep: "that Mercedes has hit you once and it's 1.9 s a lap SLOWER, let it go".
        # Never give a place to a slower car; contact alone is not enough - the car must also be
        # quicker, or have hit him at least twice (his rule: no place without a fight unless it is
        # genuinely fast, or his situation is bad).
        if call == "LET BY":
            quicker = self.quicker_by.get("behind")
            if quicker is not None and quicker < 0:
                return False, (f"the car behind is {-quicker:.1f} s a lap SLOWER: never let a slower car by. "
                               "Defend one line, or follow the team call")
            if override == "contact" and self.contacts_in_fight < 2 and (quicker is None or quicker < CONTACT_LET_BY_QUICKER_S):
                return False, ("one contact is not a reason to give the place to a car that is not clearly quicker: "
                               "defend, give it room, one line")
        supported = self.override_evidence()
        if override in supported:
            return True, "ok"
        if not supported:
            return False, (f"the team call is {' / '.join(sorted(team_words))} and the data supports no "
                           "override (no damage, no contact history, no other class near, tyres fine): follow it")
        return False, (f"the team call is {' / '.join(sorted(team_words))}; an override needs a reason the "
                       f"data supports, and it supports only: {', '.join(sorted(supported))}")

    def race_picture(self, race, lap, engineer, governor, racecraft=None):
        me = race.me
        session = race.session
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race, self.model)
        picture = {"session": {10: "race", 11: "race", 12: "race", 13: "race"}.get(session.session, "practice or qualifying"),
                   "lap": lap, "place": me.place,
                   "laps_to_go": engineer.to_go_at_line if engineer.to_go_at_line is not None
                   else laps_to_go(race, me.last_lap if me.last_lap > 0 else None),
                   "my_last_lap": lap_text(me.last_lap), "my_best_lap": lap_text(me.best_lap),
                   "time_left_s": round(session.time_remaining) if session.time_remaining > 0 else None,
                   "quiet_until_lap": governor.quiet_until_lap}
        for side, car, gap in (("ahead", ahead, gap_ahead), ("behind", behind, gap_behind)):
            if car is None or gap is None:
                picture[side] = None
                continue
            entry = {"driver": car.driver, "gap_s": round(gap, 1)}
            before = engineer.gaps_at_line.get(side)
            if self.model is not None:
                # one number for one thing (25 Sep): the race model's road trend, not the
                # line-to-line game gap, which said "steady" while the road said "growing 0.6"
                front, back = (car.id, "me") if side == "ahead" else ("me", car.id)
                entry["gap_trend"] = self.trend_words(front, back)
            elif before is not None and before[1] is not None:
                entry["gap_trend"] = trend_words(side, before[1] - gap)
            theirs, source = recent_lap(car)
            mine = my_pace(me, engineer)
            if theirs is not None:
                entry["their_lap"] = f"{lap_text(theirs)} ({source})"
            theirs, mine, measured, how = pace_pair(car, theirs, source, mine, engineer, racecraft, self.model)
            if theirs is not None and mine is not None:
                self.quicker_by[side] = round(mine - theirs, 2)      # + = that car is quicker than me
            if theirs is not None and mine is not None:
                entry["their_pace"] = f"{pace_words(theirs, mine)} ({how})"
                entry["race_maths"] = race_maths(side, round(gap, 1), theirs, mine, picture["laps_to_go"])
            else:
                entry["their_pace"] = "not known yet: no lap measured on the road or posted. Say so, never guess it."
            call = team_call(side, round(gap, 1), theirs, mine, picture["laps_to_go"], measured)
            if side == "behind" and theirs is not None and mine is not None and gap < 1.5:
                words, share = pass_odds(mine - theirs)
                entry["pass_odds_from_his_races"] = (f"in fights on his tapes, cars {words} got past within a lap "
                                                     f"{round(share * 100)}% of the time")
            if call is not None:
                entry["team_call"] = call
            elif side == "behind":
                # 25 Sep bank run: a car 1.7 s back, 0.7 s a lap quicker, got "don't fight it,
                # let it go" five times. The team's rule for when it arrives is decided now.
                entry["team_call_when_it_reaches_you"] = team_call(side, NOT_A_FIGHT_S / 2, theirs, mine,
                                                                   picture["laps_to_go"])
            if gap >= NOT_A_FIGHT_S:
                entry["fight"] = f"not a fight yet: {round(gap, 1)} s is more than {NOT_A_FIGHT_S:g} s"
            else:
                entry["fight"] = f"IN A FIGHT NOW: {round(gap, 1)} s, within {NOT_A_FIGHT_S:g} s"
            picture[side] = entry
        if self.model is not None:
            self.picture_laps_to_go = picture.get("laps_to_go")
            picture["field_around_you"] = self.field(race)
            picture["battles_near_you"] = self.battles_near(race)
            pitted = [f"P{self.model.car(key).place}" for _, key in self.model.pitting_near()
                      if self.model.car(key) is not None]
            if pitted:
                picture["just_pitted_near_you"] = pitted
        return picture

    def trend_words(self, front, back):
        t = self.model.trend(front, back)
        if t is None:
            return "not measured yet"
        amount = abs(t["closing_per_lap"])
        sure = "sure, 2 laps" if t["sure"] else "1 lap only, NOT sure"
        if amount < 0.1:
            return f"gap steady ({sure})"
        who = "the car behind is catching" if t["closing_per_lap"] > 0 else "the gap is growing"
        return f"{who} {amount:.1f} s a lap ({sure})"

    def field(self, race):
        """The same-class cars 3 places either side, as the race model sees them on the road."""
        me = race.me
        rows = []
        for o in sorted(race.opponents, key=lambda o: o.place):
            if o.car_class != me.car_class or abs(o.place - me.place) > 3 or o.in_pits:
                continue
            ahead = o.place < me.place
            gap = self.model.gap(o.id, "me") if ahead else self.model.gap("me", o.id)
            row = {"place": o.place, "side": "ahead" if ahead else "behind",
                   "gap_s": gap, "car": o.car_model or o.car_name}
            to_go = self.picture_laps_to_go
            if ahead:
                row["trend"] = self.trend_words(o.id, "me").replace("the car behind is", "you are")
                catch = self.model.catch("me", o.id)
                if catch is not None:
                    row["you_catch_it"] = catch_words(catch[1], to_go)
            else:
                row["trend"] = self.trend_words("me", o.id)
                catch = self.model.catch(o.id, "me")
                if catch is not None:
                    row["it_catches_you"] = catch_words(catch[1], to_go)
            rows.append(row)
        return rows

    def battles_near(self, race):
        places = {o.id: o.place for o in race.opponents}
        places["me"] = race.me.place
        out = []
        for front, back, gap in self.model.battles():
            if front in places and back in places and min(abs(places[front] - race.me.place),
                                                          abs(places[back] - race.me.place)) <= 4:
                who = [("you" if k == "me" else f"P{places[k]}") for k in (front, back)]
                out.append(f"{who[0]} and {who[1]}, {gap} s apart")
        return out[:5]

    def every_driver(self, race, performance, racecraft, contacts_this_race, engineer):
        me = race.me
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race, self.model)
        drivers = {}
        for opponent in race.opponents:
            if opponent.car_class != me.car_class:
                continue
            entry = {"driver": opponent.driver, "place": opponent.place,
                     "car": opponent.car_model or opponent.car_name,
                     "last_lap": lap_text(opponent.last_lap), "best_lap": lap_text(opponent.best_lap),
                     "in_pits": opponent.in_pits,
                     "where": road_words(me, opponent)}
            theirs, source = recent_lap(opponent)
            mine = my_pace(me, engineer)
            theirs, mine, _, how = pace_pair(opponent, theirs, source, mine, engineer, racecraft, self.model)
            if theirs is not None and mine is not None:
                entry["their_pace"] = f"{pace_words(theirs, mine)} ({how})"
            else:
                entry["their_pace"] = "not known: no lap time posted yet"
            key = racecraft_key(opponent)
            edges = racecraft.edges_against(key)
            # named for what they measure: speed carried through the middle, NOT braking (24 Sep:
            # "corners_they_are_quicker" came back as "quicker in every braking zone")
            if self.model is None or not self.corners_map:
                entry["corners_where_they_carry_more_speed_mid_corner"] = sorted(c for c, edge in edges.items() if edge <= -3.0)
                entry["corners_where_you_carry_more_speed_mid_corner"] = sorted(c for c, edge in edges.items() if edge >= 3.0)
            entry["contacts_with_you_this_race"] = contacts_this_race.get(key, 0)
            entry["pass_attempts_this_race"] = [a[4] for a in racecraft.attempts if a[0] == key]
            entry["history"] = racecraft.rivals.get(key)
            if self.model is not None and self.corners_map:
                # seconds THEY gain on you through each corner, from the road (race model)
                gains = self.model.corner_gains(opponent.id, "me", self.corners_map)
                entry["corners_where_they_gain_time_s"] = {c: g for c, g in gains.items() if g >= 0.1}
                entry["corners_where_you_gain_time_s"] = {c: -g for c, g in gains.items() if g <= -0.1}
            drivers[opponent.driver.lower()] = entry
            if opponent is ahead:
                drivers["ahead"] = entry
            if opponent is behind:
                drivers["behind"] = entry
        return drivers

    def every_corner(self, performance):
        corners = {}
        rival = {item[1]: item for item in performance.rival_gaps()}
        for corner, passes in performance.my_passes.items():
            timed = [p.time_s for p in passes if p.time_s is not None]
            entry = {"corner": corner, "laps_measured": len(timed)}
            if timed:
                entry["my_best_s"] = round(min(timed), 2)
                entry["my_last_s"] = round(timed[-1], 2)
            measured = performance.balance.corner_balance(corner)
            if measured is not None:
                from balance import describe
                entry["balance"] = describe(measured)
            if corner in rival:
                gap, _, fastest, (change, lengths) = rival[corner]
                from seats.performance import advice_text
                entry["fastest_car"] = fastest["driver"]
                entry["fastest_gains_s"] = gap
                entry["what_to_change"] = advice_text(change, lengths)
            corners[corner.lower()] = entry
        return corners

    def car(self, race, strategist):
        me = race.me
        state = {"fuel": strategist.fuel_now,
                 "tyre_temps_c": [round(sum(z) / len(z)) for z in me.tyre_temps if z and min(z) > -200],
                 "damage": sum(me.dents) > 0,
                 "tyres_overheating": any(round(sum(z) / len(z)) > HOT_TYRE_C for z in me.tyre_temps
                                          if z and min(z) > -200) or bool(me.overheating),
                 "track_limit_steps": me.track_limit_steps,
                 "penalty_at_steps": race.session.limit_steps_per_penalty,
                 "penalties": me.penalties}
        return state

    def track_ahead(self, lap_dist, corners, race, racecraft):
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race, self.model)
        if not corners:
            return []
        ordered = sorted(corners, key=lambda c: c["start"])
        lap_length = max(c["end"] for c in ordered)
        upcoming = []
        for corner in ordered:
            distance = corner["start"] - lap_dist
            if distance < 0:
                distance += lap_length
            upcoming.append((distance, corner["name"]))
        upcoming.sort()
        result = []
        for distance, name in upcoming[:4]:
            entry = {"corner": name, "metres_away": round(distance)}
            for side, car in (("car_ahead", ahead), ("car_behind", behind)):
                if car is None:
                    continue
                edge = racecraft.edges_against(racecraft_key(car)).get(name)
                if edge is not None and abs(edge) >= 3.0:
                    entry[side] = "you are quicker here" if edge > 0 else "they are quicker here"
            result.append(entry)
        return result

    def driver_names(self):
        return [key for key in self.drivers if key not in ("ahead", "behind")]

    def strategy(self):
        """The plan, in order, from code's numbers: the model explains it, never re-derives it."""
        plan = []
        fuel = self.car_state.get("fuel_at_the_flag")
        if isinstance(fuel, dict):
            spare, what = fuel["spare_laps"], fuel["limit"]
            if spare < 0:
                plan.append(f"SAVE {what}: {-spare} laps short at the flag. Lift and coast before the longest "
                            "braking zones until it is back above zero.")
            elif spare < 0.5:
                plan.append(f"{what} is tight: {spare} laps spare. No wasted laps.")
            else:
                plan.append(f"{what} is no limit: {spare} laps spare. Push.")
        else:
            plan.append("fuel to the flag: not known yet (needs 2 laps measured at the line)")
        for side in ("ahead", "behind"):
            car = self.picture.get(side)
            if not car:
                continue
            maths = car.get("race_maths", {})
            reach = maths.get("at_this_pace", "")
            if car.get("team_call"):
                plan.append(f"car {side}, {car['gap_s']} s: {car['team_call']}")
            elif side == "ahead" and "before the flag" in reach and "reach them" in reach:
                plan.append(f"PUSH: the car ahead, {car['gap_s']} s up, {reach}.")
            elif side == "behind" and "before the flag" in reach and "reach you" in reach:
                plan.append(f"DEFEND LATER: the car behind, {car['gap_s']} s back, {reach}. "
                            f"Keep it behind with a {maths.get('to_keep_them_behind', 'quicker')} lap.")
            else:
                plan.append(f"car {side}, {car['gap_s']} s: {reach or car.get('their_pace', 'pace not known')}")
        if self.car_state.get("tyres_overheating"):
            plan.append("TYRES are cooking (over 105 C): smoother, less sliding, or the pace goes.")
        losing = sorted((entry for entry in self.corners.values() if entry.get("fastest_gains_s")),
                        key=lambda entry: -entry["fastest_gains_s"])[:2]
        where = [f"{entry['corner']}: the fastest car gains {entry['fastest_gains_s']} s. {entry.get('what_to_change', '')}".strip()
                 for entry in losing]
        return {"laps_to_go": self.picture.get("laps_to_go"), "place": self.picture.get("place"),
                "plan_in_order": plan, "where_the_time_is": where or ["not measured yet"],
                "his_habits": (self.habits or [])[:2]}

    def remind(self, arguments):
        try:
            lap = int(arguments.get("lap"))
        except (TypeError, ValueError):
            return {"error": "lap must be a lap number"}
        what = str(arguments.get("what", "")).strip()
        if lap <= self.lap or not what:
            return {"error": f"the reminder needs a lap after this one (this is lap {self.lap}) and words"}
        to_go = self.picture.get("laps_to_go")
        if to_go and lap > self.lap + to_go - 1:
            # 25 Sep bank run: "reminder set for lap 10" in a race that ends on lap 6
            return {"error": f"the race ends on lap {self.lap + to_go - 1}: there is no lap {lap}"}
        self.actions.append({"remind_lap": lap, "what": what[:80]})
        return {"ok": f"reminder set for lap {lap}: {what[:80]}"}

    def run_tool(self, name, arguments):
        if name == "race_picture":
            return self.picture
        if name == "standings":
            return self.standings
        if name == "session":
            return self.session
        if name == "lap_history":
            return self.laps
        if name == "strategy":
            return self.strategy()
        if name == "race_events":
            events = race_tools.race_events(self.db_path, self.session_id)
            # the other car in a contact, as its place now (never a name): "who hit me?"
            from race_state import identity
            places = {identity(o): f"P{o.place}" for o in self.race.opponents}
            for contact in events.get("contacts", []):
                if contact.get("other_car"):
                    contact["other_car"] = places.get(contact["other_car"], "a car no longer in the race")
            return events
        if name == "setup":
            return race_tools.setup_advice(self.db_path, self.session_id, self.race)
        if name == "knowledge":
            return race_tools.knowledge(arguments.get("topic", ""))
        if name == "calculator":
            return race_tools.calculate(arguments.get("expression", ""))
        if name == "remind_me":
            return self.remind(arguments)
        if name == "database":
            return race_tools.query_db(self.db_path, arguments.get("sql", ""))
        if name == "my_habits":
            return self.habits or ["no measured habits yet"]
        if name == "car":
            return self.car_state
        if name == "track_ahead":
            return self.ahead_of_me
        if name == "driver":
            who = str(arguments.get("who", "")).lower().strip()
            if who in self.drivers:
                return self.drivers[who]
            for key, entry in self.drivers.items():
                if who and (who in key or key in who):
                    return entry
            return {"error": f"no driver '{who}' in your class; ask for 'ahead', 'behind' or a name from race_picture"}
        if name == "corner":
            wanted = str(arguments.get("name", "")).lower().strip()
            for key, entry in self.corners.items():
                if wanted and (wanted in key or key in wanted):
                    return entry
            # a misheard name ("four chickens" for Ford Chicanes, 24 Sep) is still that corner
            import difflib
            close = difflib.get_close_matches(wanted, list(self.corners), n=1, cutoff=0.55)
            if close:
                return dict(self.corners[close[0]], heard_as=wanted)
            return {"error": f"no data for corner '{wanted}'", "known": sorted(self.corners)}
        return {"error": f"no tool {name}"}


def racecraft_key(opponent):
    from race_state import identity
    return identity(opponent)


def without_empty(value):
    """Tool results without None, empty lists and empty dicts: fewer tokens sent every round."""
    if isinstance(value, dict):
        kept = {}
        for key, item in value.items():
            item = without_empty(item)
            if item not in (None, [], {}):
                kept[key] = item
        return kept
    if isinstance(value, list):
        return [without_empty(item) for item in value]
    return value


# ---- the gate for agent answers --------------------------------------------------------------

def asks_to_explain(question):
    heard = " " + re.sub(r"[^a-z0-9 ]+", " ", question.lower().replace("'", "")) + " "
    return any(" " + words + " " in heard for words in EXPLAIN_WORDS)


def asks_about_speed(question):
    lowered = question.lower()
    return any(words in lowered for words in SPEED_WORDS)


NEIGHBOUR_WORDS = ("car behind", "car ahead", "one behind", "car in front", "the fucker behind")
ABOUT_NEIGHBOURS = ("behind", "ahead", "in front", "gap", "catch", "defend", "attack", "pass", "fight", "hit",
                    "dive", "diving", "him", "he ", "car", "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9",
                    "strategy", "plan", "position", "place", "push", "update", "lost it", "doing ok", "move",
                    "what the fuck", "straights", "every lap", "last lap", "risk")


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


FUEL_QUESTION = ("fuel", "energy", "pit", "box", "stop", "make it", "tank", "litre", "liter", "refuel")
NO_STOP_WORDS = ("no need to pit", "no stop", "don't need to pit", "dont need to pit", "no need to box",
                 "you'll make it", "you will make it", "enough fuel", "fuel's fine", "fuel is fine")


CATCHING_WORDS = ("catching you", "closing on you", "closing you", "reeling you in", "on you by", "coming at you")
DROPPING_WORDS = ("dropping back", "falling back", "pulling away from you", "not catching you")


def trend_honest(text, picture):
    """(ok, reason). The direction of a SURE road trend is a fact: an answer may not say the car
    behind is catching when the race model is sure it is dropping back, or the reverse."""
    said = text.lower()
    for row in (picture or {}).get("field_around_you", []):
        if row.get("side") != "behind" or row.get("place") is None:
            continue
        trend = row.get("trend", "")
        if "sure, 2 laps" not in trend:
            continue
        nearest = min((r for r in picture["field_around_you"] if r.get("side") == "behind"),
                      key=lambda r: r["place"])
        if row is not nearest:
            continue
        if "growing" in trend and any(w in said for w in CATCHING_WORDS):
            return False, f"the road says the car behind is NOT catching ({trend}): don't say it is"
        if "catching" in trend and any(w in said for w in DROPPING_WORDS):
            return False, f"the road says the car behind IS catching ({trend}): don't say it isn't"
    return True, "ok"


def fuel_honest(question, text, picture):
    """(ok, reason). Live 25 Sep: with 0.6 laps of energy for 1.7 laps of race the coach said
    "no need to pit". The fuel verdict is code's; the answer must carry it."""
    asked = question.lower()
    if not any(word in asked for word in FUEL_QUESTION):
        return True, "ok"
    said = text.lower()
    verdict = picture.get("verdict") if isinstance(picture, dict) else None
    if verdict == "box" and not any(w in said for w in ("box", "pit")):
        return False, "the fuel verdict is BOX THIS LAP (car tool, fuel_at_the_flag): say it first"
    if verdict == "save" and not any(w in said for w in ("lift", "coast", "save", "short")):
        return False, "the fuel verdict is SHORT: lift and coast every braking zone, say it first"
    if verdict in ("box", "save") and any(w in said for w in NO_STOP_WORDS):
        return False, "that contradicts the fuel verdict: he does NOT make it as he is"
    if verdict is None and any(w in said for w in NO_STOP_WORDS):
        return False, "fuel usage is not measured yet: say it is not known and to check the screen, never 'no need to pit'"
    return True, "ok"


PRONOUNS = [(r"\bhe's\b", "it's"), (r"\bshe's\b", "it's"), (r"\bhe\b", "it"), (r"\bshe\b", "it"),
            (r"\bhim\b", "it"), (r"\bhis\b", "its"), (r"\bhers\b", "its"), (r"\bher\b", "its")]


def neutral_pronouns(text):
    """Other drivers are "it" (the car), never he/she. Rewritten in code: on 25 Sep two answers
    were refused for "he" and each refusal cost a whole extra model round."""
    for pattern, word in PRONOUNS:
        text = re.sub(pattern, lambda m: word.capitalize() if m.group(0)[0].isupper() else word, text,
                      flags=re.IGNORECASE)
    return text


def check_answer(text, known_numbers, clean=False, names=(), max_words=MAX_WORDS, speeds_ok=False):
    """(ok, reason). known_numbers: every number the tools returned or he said.
    names: the other drivers in this race; none may be said (v3, 24 Sep)."""
    if not text or not text.strip():
        return False, "empty"
    lowered = text.lower()
    for name in names:
        parts = [name.lower()] + [part for part in name.lower().split() if len(part) >= 4]
        for part in parts:
            if has_phrase(lowered, part):
                return False, "says a driver's name: say the car ahead, the car behind, or its position"
    if len(text.split()) > max_words:
        return False, f"too long: keep it to about {round(max_words * 0.65)} words"
    if "?" in text:
        return False, "asks a question back"
    if "*" in text or "\n-" in text:
        return False, "markdown would be read aloud"
    if not speeds_ok and re.search(r"km/h|\bkph\b|\bkmh\b|kilomet|\bmph\b", lowered):
        return False, "says a speed; use time, gaps, laps or car lengths"
    for word in GENDERED:
        if has_phrase(lowered, word):
            return False, f"uses '{word}' for a real person; use their name or 'the car behind'"
    for phrase in BANNED:
        if has_phrase(lowered, phrase):
            return False, f"banned word '{phrase}'"
    if clean:
        for word in PROFANITY:
            if has_phrase(lowered, word):
                return False, "swears in clean mode"
    facts = {str(i): n for i, n in enumerate(known_numbers)}
    for number in numbers_in(lowered):
        if not number_is_backed(number, facts):
            return False, f"the number {number:g} is not in the data"
    return True, "ok"


def numbers_seen(*texts):
    found = []
    for text in texts:
        found.extend(numbers_in(words_to_digits(str(text).lower())))
    return found


# ---- the agent -------------------------------------------------------------------------------

class RaceAgent:
    """Runs on its own thread. ask() returns at once; finished() hands back the answer Calls
    and the cost of every model call, for the race loop to put on air and log."""

    def __init__(self, budget, clean=False, client=None, thinking=False):
        self.budget = budget
        self.clean = clean
        self.thinking = thinking
        self.exchanges = []         # the last questions and answers, for follow-ups
        # live 24 Sep: the first question timed out on a cold connection (TLS, DNS) while the
        # provider was fine a minute later. One tiny call at startup opens it before he asks.
        if client is None:
            threading.Thread(target=self.warm_up, daemon=True).start()
        self.client = client
        self.jobs = Queue()
        self.results = Queue()
        threading.Thread(target=self.work, daemon=True).start()

    def connect(self):
        if self.client is None:
            from openai import OpenAI
            from dotenv import load_dotenv
            load_dotenv(".env")
            self.client = OpenAI(base_url="https://aicredits.in/v1",
                                 api_key=os.environ["AICREDITS_API_KEY"], timeout=MODEL_TIMEOUT_S, max_retries=0)
        return self.client

    def warm_up(self):
        try:
            self.connect().chat.completions.create(model=MODEL, messages=[{"role": "user", "content": "ok"}],
                                                   max_tokens=1, extra_body={"thinking": {"type": "disabled"}})
        except Exception:
            pass                       # a failed warm-up changes nothing: the first question retries

    def ask(self, question, snapshot, sim_time):
        self.jobs.put((question, snapshot, sim_time, time.perf_counter()))

    def earlier(self, sim_time):
        """The recent exchanges, oldest first: "but he keeps hitting me" only makes sense after
        the question before it (24 Sep). Each must be within a minute of the next one."""
        recent = []
        after = sim_time
        for exchange in reversed(self.exchanges[-FOLLOW_UP_EXCHANGES:]):
            if after - exchange["sim_time"] > FOLLOW_UP_WINDOW_S:
                break
            recent.insert(0, exchange)
            after = exchange["sim_time"]
        if not recent:
            return ""
        lines = [f'he asked "{e["question"]}" and you answered "{e["answer"]}"' for e in recent]
        return "Earlier on the radio, oldest first: " + "; then ".join(lines) + "."

    def finished(self):
        done = []
        while True:
            try:
                done.append(self.results.get_nowait())
            except Empty:
                return done

    def work(self):
        while True:
            question, snapshot, sim_time, asked_at = self.jobs.get()
            try:
                answer, info = self.think(question, snapshot, self.earlier(sim_time))
            except Exception as error:
                answer, info = "Lost the data on that one. Ask me again.", {"error": error.__class__.__name__, "costs": []}
            info["seconds"] = round(time.perf_counter() - asked_at, 2)
            self.exchanges = (self.exchanges + [{"question": question, "answer": answer, "sim_time": sim_time}])[-FOLLOW_UP_EXCHANGES:]
            call = Call(seat="race_engineer", kind="ANSWER_AGENT", sim_time=sim_time,
                        priority=RACE_CONTROL, ttl=ANSWER_TTL_S, conclusion=answer, template=answer,
                        facts={"heard": question, "tools": info.get("tools", []), "rounds": info.get("rounds"),
                               "seconds": info["seconds"], "refused": info.get("refused"),
                               "call": info.get("call"), "override": info.get("override")},
                        asked=True, phrase=False)
            self.results.put({"call": call, "costs": info.get("costs", []), "actions": list(snapshot.actions)})

    def model_turn(self, messages, timeout=MODEL_TIMEOUT_S):
        started = time.perf_counter()
        response = self.connect().chat.completions.create(
            model=MODEL, messages=messages, max_tokens=MAX_TOKENS, timeout=timeout,
            tools=[{"type": "function", "function": tool} for tool in TOOLS],
            extra_body={"thinking": {"type": "enabled" if self.thinking else "disabled"}})
        usage = response.usage
        cost = self.budget.charge(usage.prompt_tokens, usage.completion_tokens)
        spent = {"tokens_in": usage.prompt_tokens, "tokens_out": usage.completion_tokens,
                 "seconds": round(time.perf_counter() - started, 3), "cost_rs": round(cost, 5)}
        return response.choices[0].message, spent

    def think(self, question, snapshot, earlier=""):
        # no budget check: push-to-talk never stops (his call, 25 Sep - the Rs 5 cap silenced the
        # coach after 7 answers in a live race). Every call is still charged and logged.
        system = AGENT_PROMPT + ("\n" + CLEAN_RULE if self.clean else "")
        picture = json.dumps(without_empty(snapshot.picture))
        # the voice goes right next to the question: in the system prompt alone it got lost
        # (1 answer in 4 swore on 24 Sep), the same lesson as the persona's per-line flag
        voice = VOICE_REMINDER_CLEAN if self.clean else VOICE_REMINDER
        explain = asks_to_explain(question)
        max_words = MAX_WORDS_EXPLAIN if explain else MAX_WORDS
        timeout = HEAVY_TIMEOUT_S if explain else MODEL_TIMEOUT_S
        speeds_ok = asks_about_speed(question)
        if explain:
            voice = voice.replace("About 35 words.", "He asked for the reasons: up to about 70 words, the reasons in order.")
        if speeds_ok:
            voice += " He asked about speed: speeds in km/h are allowed in this answer."
        if not snapshot.team_calls:
            voice += " " + NO_TACK_ON
        # live 25 Sep: 25 model calls for 7 answers - nearly every answer first asked for the cars
        # ahead/behind and his habits, a whole extra round (2-8 s, ~Rs 0.25). They go with the
        # question now; the tools stay for everything else.
        near = {side: without_empty(snapshot.drivers[side]) for side in ("ahead", "behind") if side in snapshot.drivers}
        habits = (snapshot.habits or [])[:3]
        given = json.dumps({"driver_ahead": near.get("ahead"), "driver_behind": near.get("behind"), "his_habits": habits})
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": f"{earlier}\n\n{question}\n\n(Race picture right now, from race_picture: {picture})"
                                                f"\n(Already given, no need to call driver or my_habits for these: {given})\n\n{voice}"}]
        costs = []
        tools_used = ["race_picture"]
        tool_texts_given = [given]
        tool_texts = [picture] + tool_texts_given
        refused = None
        # tool rounds, then the answer, then at most one rewrite
        for round_number in range(1, MAX_ROUNDS + 3):
            try:
                message, spent = self.model_turn(messages, timeout)
            except Exception as error:
                # slow or down: the decision still gets through, from code
                return fallback(snapshot, question), {"costs": costs, "tools": tools_used, "rounds": None,
                                            "refused": f"model {error.__class__.__name__}", "call": "TEAM"}
            costs.append(spent)
            if message.tool_calls and round_number <= MAX_ROUNDS:
                messages.append(message.model_dump(exclude_none=True))
                for tool_call in message.tool_calls:
                    try:
                        arguments = json.loads(tool_call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    result = json.dumps(without_empty(snapshot.run_tool(tool_call.function.name, arguments)))
                    tools_used.append(tool_call.function.name)
                    tool_texts.append(result)
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                continue
            raw = message.content or ""
            call, override, text = split_call(raw)                 # the CALL line is never spoken
            text = neutral_pronouns(text)     # a free fix instead of a paid rewrite round (25 Sep)
            ok, reason = snapshot.check_call(call, override)
            if ok:
                ok, reason = check_answer(text, numbers_seen(question, *tool_texts), self.clean,
                                          snapshot.driver_names(), max_words, speeds_ok)
            if ok:
                ok, reason = fuel_honest(question, text, snapshot.car_state.get("fuel_at_the_flag"))
            if ok:
                ok, reason = trend_honest(text, snapshot.picture)
            if ok and tacked_on(question, text, bool(snapshot.team_calls)):
                ok, reason = False, ("it talks about the car ahead or behind, but he did not ask about them "
                                     "and nobody is within a second: answer only his question")
            if ok:
                return text, {"costs": costs, "tools": tools_used, "rounds": round_number, "refused": refused,
                              "call": call, "override": override}
            if refused is not None:
                break                   # one rewrite only
            refused = reason
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"That answer was refused: {reason}. Rewrite it, same call, following the spoken-answer rules. {voice}"})
        return ("No clean answer on that one, mate. Ask it another way.",
                {"costs": costs, "tools": tools_used, "rounds": None, "refused": refused})
