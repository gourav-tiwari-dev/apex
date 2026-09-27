"""The maths behind a fight call: who is quicker and by how much, whether a catch happens
before the flag, the odds of a pass, and the team call (DEFEND, LET BY, ATTACK, FOLLOW)
that the coach states and may override with a reason."""

import math

from race_model import CATCH_UPPER
from words import lap_text


NOT_A_FIGHT_S = 1.0  # further apart than this, nobody is diving at anybody yet


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
PASS_ODDS = [
    (-99.0, "slower than you", 0.45),
    (-0.5, "about your pace", 0.55),
    (0.5, "0.5 to 2 s a lap quicker", 0.81),
    (2.0, "2+ s a lap quicker", 0.86),
]


def pass_odds(quicker):
    """(words, share) for a car behind that is `quicker` s a lap quicker on the road."""
    found = PASS_ODDS[0]
    for row in PASS_ODDS:
        if quicker >= row[0]:
            found = row
    return found[1], found[2]


CONTACT_LET_BY_QUICKER_S = (
    0.5  # contact once + at least this much quicker: a let-by is fair
)


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
    base = mine or engineer.my_lap or 240.0  # only the difference matters to the maths
    if model is not None:
        found = model.quicker(car.id, "me")
        if found is not None:
            quicker, sure = found
            how = (
                "measured on the road over the last 2 laps"
                if sure
                else "on the road over 1 lap only: NOT SURE yet, say so"
            )
            return round(base - quicker, 2), base, sure, how
    quicker = None
    if racecraft is not None:
        quicker = racecraft.clock.pace_vs_me(car.id)
    if quicker is not None:
        return (
            round(base - quicker, 2),
            base,
            True,
            "measured on the road over the last lap",
        )
    if theirs is not None and mine is not None:
        clean = source == "last lap"
        return (
            theirs,
            mine,
            clean,
            "from lap times" if clean else "from their best lap, their last not posted",
        )
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
    per_lap = round(their_lap - my_lap, 2)  # > 0: I am quicker
    if side == "ahead":
        if laps_to_go:
            needed = gap / laps_to_go
            maths["to_catch_by_the_flag"] = (
                f"find {round(needed, 1)} s a lap on them: "
                f"a {lap_text(their_lap - needed)} lap, every lap"
            )
        if per_lap > 0:
            laps = round(gap / per_lap, 1)
            maths["at_this_pace"] = f"you reach them in about {laps} laps: " + (
                "before the flag"
                if laps_to_go and laps <= laps_to_go
                else "not before the flag"
            )
        else:
            maths["at_this_pace"] = "you are not catching them"
    else:
        if per_lap < 0:
            laps = round(gap / -per_lap, 1)
            maths["at_this_pace"] = f"they reach you in about {laps} laps: " + (
                "before the flag"
                if laps_to_go and laps <= laps_to_go
                else "not before the flag"
            )
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
ATTACK_QUICKER_S = 0.2  # this much quicker than the car ahead, in a fight: go


def team_call(side, gap, their_lap, my_lap, laps_to_go, measured=True):
    """DEFEND / LET BY / ATTACK / FOLLOW for a car within a second, or None outside a fight.
    measured: the pace came from the road (or two clean laps); without it, never LET BY."""
    if gap >= NOT_A_FIGHT_S:
        return None
    last_lap = laps_to_go is not None and laps_to_go <= 1
    if side == "behind":
        if last_lap:
            return "DEFEND: last lap, every place counts. One line, no weaving, no moving in the braking zone."
        if (
            measured
            and their_lap is not None
            and my_lap is not None
            and my_lap - their_lap >= LET_BY_QUICKER_S
        ):
            quicker = round(my_lap - their_lap, 1)
            return (
                f"LET BY: {quicker} s a lap quicker, it gets by anyway. Hold a predictable line, "
                "don't cover the inside, never lift in its path, then stay with it."
            )
        return "DEFEND: similar pace, this place is yours to keep. One line into its strong corner, no weaving."
    if (
        their_lap is not None
        and my_lap is not None
        and their_lap - my_lap >= ATTACK_QUICKER_S
    ):
        return (
            "ATTACK: you are quicker. Set it up on the exit of a corner where you carry more speed "
            "and pass into the next braking zone. One clean move, no lunge."
        )
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
