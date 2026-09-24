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
import os
import re
import threading
import time
from queue import Queue, Empty

from persona import MODEL, BANNED, PROFANITY, GENDERED, numbers_in, number_is_backed, words_to_digits, has_phrase
from radio import Call, RACE_CONTROL
from race_state import same_class_neighbours, laps_to_go

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
  "Let Zino go, mate. A fucking second a lap quicker, you won't hold that. Clean exit, then chase Kossman."
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
   An override must be said out loud with its reason ("Team says defend, but Zino's already
   hit you twice: let it go"). Code checks every override against the data.
   Never promise a later call ("I'll tell you where"): nothing will call him back. Say where NOW.
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

THE SPOKEN ANSWER (it is read aloud to him while he drives)
- At most 3 short sentences, about 35 words. The call first.
- NEVER say a speed or km/h: he drives by feel. Use time, laps, gaps, corners, car lengths.
- Every number must come from a tool result or from his question. Write numbers as digits.
- His position is ONLY race_picture "place" (on 24 Sep an answer said P5 when he was P4:
  5 was another car's place). Never take his place from another car's data.
- Other drivers are real people: use their name or "the car behind", never he, she, him, her or his.
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
     "description": "His car: fuel or energy spare at the flag, tyre temperatures, damage, "
                    "track-limit steps against the penalty limit, penalties.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
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
    (Kossman on 23 Sep), so the best lap stands in, labelled as such."""
    if car.last_lap > 0:
        return car.last_lap, "last lap"
    if car.best_lap > 0:
        return car.best_lap, "best lap, last lap not posted"
    return None, None


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
LET_BY_QUICKER_S = 1.0        # a car this much quicker a lap gets by anyway (when laps remain)
ATTACK_QUICKER_S = 0.2        # this much quicker than the car ahead, in a fight: go


def team_call(side, gap, their_lap, my_lap, laps_to_go):
    """DEFEND / LET BY / ATTACK / FOLLOW for a car within a second, or None outside a fight."""
    if gap >= NOT_A_FIGHT_S:
        return None
    last_lap = laps_to_go is not None and laps_to_go <= 1
    if side == "behind":
        if last_lap:
            return "DEFEND: last lap, every place counts. One line, no weaving, no moving in the braking zone."
        if their_lap is not None and my_lap is not None and my_lap - their_lap >= LET_BY_QUICKER_S:
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
    if opponent.laps_behind_leader != me.laps_behind_leader:
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
                 racecraft, governor, habits, contacts_this_race):
        self.picture = self.race_picture(race, lap, engineer, governor)
        self.drivers = self.every_driver(race, performance, racecraft, contacts_this_race)
        self.habits = habits
        self.corners = self.every_corner(performance)
        self.car_state = self.car(race, strategist)
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
        supported = self.override_evidence()
        if override in supported:
            return True, "ok"
        if not supported:
            return False, (f"the team call is {' / '.join(sorted(team_words))} and the data supports no "
                           "override (no damage, no contact history, no other class near, tyres fine): follow it")
        return False, (f"the team call is {' / '.join(sorted(team_words))}; an override needs a reason the "
                       f"data supports, and it supports only: {', '.join(sorted(supported))}")

    def race_picture(self, race, lap, engineer, governor):
        me = race.me
        session = race.session
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race)
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
            if before is not None and before[1] is not None:
                entry["gap_trend"] = trend_words(side, before[1] - gap)
            theirs, source = recent_lap(car)
            mine, _ = recent_lap(me)
            if theirs is not None:
                entry["their_lap"] = f"{lap_text(theirs)} ({source})"
            if theirs is not None and mine is not None:
                entry["their_pace"] = pace_words(theirs, mine)
                entry["race_maths"] = race_maths(side, round(gap, 1), theirs, mine, picture["laps_to_go"])
            else:
                entry["their_pace"] = "not known: no lap time posted yet. Say so, never guess it."
            call = team_call(side, round(gap, 1), theirs, mine, picture["laps_to_go"])
            if call is not None:
                entry["team_call"] = call
            if gap >= NOT_A_FIGHT_S:
                entry["fight"] = f"not a fight yet: {round(gap, 1)} s is more than {NOT_A_FIGHT_S:g} s"
            else:
                entry["fight"] = f"IN A FIGHT NOW: {round(gap, 1)} s, within {NOT_A_FIGHT_S:g} s"
            picture[side] = entry
        return picture

    def every_driver(self, race, performance, racecraft, contacts_this_race):
        me = race.me
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race)
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
            mine, _ = recent_lap(me)
            if theirs is not None and mine is not None:
                entry["their_pace"] = f"{pace_words(theirs, mine)} (their {source})"
            else:
                entry["their_pace"] = "not known: no lap time posted yet"
            key = racecraft_key(opponent)
            edges = racecraft.edges_against(key)
            # named for what they measure: speed carried through the middle, NOT braking (24 Sep:
            # "corners_they_are_quicker" came back as "quicker in every braking zone")
            entry["corners_where_they_carry_more_speed_mid_corner"] = sorted(c for c, edge in edges.items() if edge <= -3.0)
            entry["corners_where_you_carry_more_speed_mid_corner"] = sorted(c for c, edge in edges.items() if edge >= 3.0)
            entry["contacts_with_you_this_race"] = contacts_this_race.get(key, 0)
            entry["pass_attempts_this_race"] = [a[4] for a in racecraft.attempts if a[0] == key]
            entry["history"] = racecraft.rivals.get(key)
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
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race)
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

    def run_tool(self, name, arguments):
        if name == "race_picture":
            return self.picture
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

def check_answer(text, known_numbers, clean=False):
    """(ok, reason). known_numbers: every number the tools returned or he said."""
    if not text or not text.strip():
        return False, "empty"
    lowered = text.lower()
    if len(text.split()) > MAX_WORDS:
        return False, f"too long: keep it to about 35 words"
    if "?" in text:
        return False, "asks a question back"
    if "*" in text or "\n-" in text:
        return False, "markdown would be read aloud"
    if re.search(r"km/h|\bkph\b|\bkmh\b|kilomet|\bmph\b", lowered):
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
        self.last = None            # the last question and answer, for follow-ups
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
        """The last exchange, if it was recent: "but he keeps hitting me" only makes sense after
        the question before it (24 Sep)."""
        if self.last is None or sim_time - self.last["sim_time"] > FOLLOW_UP_WINDOW_S:
            return ""
        return f'Just before this he asked: "{self.last["question"]}" and you answered: "{self.last["answer"]}"'

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
            self.last = {"question": question, "answer": answer, "sim_time": sim_time}
            call = Call(seat="race_engineer", kind="ANSWER_AGENT", sim_time=sim_time,
                        priority=RACE_CONTROL, ttl=ANSWER_TTL_S, conclusion=answer, template=answer,
                        facts={"heard": question, "tools": info.get("tools", []), "rounds": info.get("rounds"),
                               "seconds": info["seconds"], "refused": info.get("refused"),
                               "call": info.get("call"), "override": info.get("override")},
                        asked=True, phrase=False)
            self.results.put({"call": call, "costs": info.get("costs", [])})

    def model_turn(self, messages):
        started = time.perf_counter()
        response = self.connect().chat.completions.create(
            model=MODEL, messages=messages, max_tokens=MAX_TOKENS,
            tools=[{"type": "function", "function": tool} for tool in TOOLS],
            extra_body={"thinking": {"type": "enabled" if self.thinking else "disabled"}})
        usage = response.usage
        cost = self.budget.charge(usage.prompt_tokens, usage.completion_tokens)
        spent = {"tokens_in": usage.prompt_tokens, "tokens_out": usage.completion_tokens,
                 "seconds": round(time.perf_counter() - started, 3), "cost_rs": round(cost, 5)}
        return response.choices[0].message, spent

    def think(self, question, snapshot, earlier=""):
        if not self.budget.allows_llm():
            return "Over the radio budget for this race. Stick to the basics, mate.", {"costs": []}
        system = AGENT_PROMPT + ("\n" + CLEAN_RULE if self.clean else "")
        picture = json.dumps(without_empty(snapshot.picture))
        # the voice goes right next to the question: in the system prompt alone it got lost
        # (1 answer in 4 swore on 24 Sep), the same lesson as the persona's per-line flag
        voice = VOICE_REMINDER_CLEAN if self.clean else VOICE_REMINDER
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": f"{earlier}\n\n{question}\n\n(Race picture right now, from race_picture: {picture})\n\n{voice}"}]
        costs = []
        tools_used = ["race_picture"]
        tool_texts = [picture]
        refused = None
        # tool rounds, then the answer, then at most one rewrite
        for round_number in range(1, MAX_ROUNDS + 3):
            try:
                message, spent = self.model_turn(messages)
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
            ok, reason = snapshot.check_call(call, override)
            if ok:
                ok, reason = check_answer(text, numbers_seen(question, *tool_texts), self.clean)
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
