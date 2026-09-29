"""Facts the seats read off a race snapshot.

Is a car on his lap (same_lap), who is ahead and behind him in his class, is it a multiclass
race, his place as the radio says it ("P18 in class"), how long the track is, and how many laps
are left: the leader timed to the line on the road, so the flag lands where the game puts it."""

import math


def same_lap(me, opponent, model=None):
    """Is this car racing on my lap? From the road when the race model has both cars: within half a
    lap of me. Never from the game's laps-behind-leader: in his online warning lobby (25 Sep) it
    said cars were 1-2 laps behind while everyone was on lap 0, every car was filtered out, and
    racecraft and battle awareness were silent the whole race. Without the model: a lapped car is
    one with two or more fewer laps done (at the line the counts are one apart for a moment)."""
    if model is not None and model.lap_length:
        mine, theirs = model.distance("me"), model.distance(opponent.id)
        if mine is not None and theirs is not None:
            return abs(mine - theirs) < model.lap_length / 2
    return abs(opponent.laps - me.laps) < 2


def same_class_neighbours(race, model=None):
    """The same-class cars just ahead of and behind me in the race, and the time gaps to them.
    Only cars on my lap: a lapped car is not a fight. With the race model the gaps are its
    same-point road gaps (one number everywhere, 25 Sep); the game's own gap is the fallback."""
    me = race.me
    ahead, behind = nearest_in_class(race, model)
    gap_ahead = None
    gap_behind = None
    if ahead is not None:
        gap_ahead = model.gap(ahead.id, "me") if model is not None else None
        if gap_ahead is None:
            gap_ahead = round(me.time_behind_leader - ahead.time_behind_leader, 2)
    if behind is not None:
        gap_behind = model.gap("me", behind.id) if model is not None else None
        if gap_behind is None:
            gap_behind = round(behind.time_behind_leader - me.time_behind_leader, 2)
    return ahead, gap_ahead, behind, gap_behind


def nearest_in_class(race, model=None):
    """The cars of my class on my lap just ahead of and just behind me in the race:
    (ahead, behind), either None."""
    me = race.me
    ahead = None
    behind = None
    for opponent in race.opponents:
        if opponent.car_class != me.car_class or not same_lap(me, opponent, model):
            continue
        if opponent.place < me.place and (
            ahead is None or opponent.place > ahead.place
        ):
            ahead = opponent
        if opponent.place > me.place and (
            behind is None or opponent.place < behind.place
        ):
            behind = opponent
    return ahead, behind


def multiclass(race):
    """True when other classes share the track: then the place he races for is his place in class."""
    for opponent in race.opponents:
        if opponent.car_class != race.me.car_class:
            return True
    return False


def class_place(race, place, car_class):
    """A car's place among its own class: one more than the cars of that class ahead of it overall."""
    ahead = 0
    if race.me.car_class == car_class and race.me.place < place:
        ahead += 1
    for opponent in race.opponents:
        if opponent.car_class == car_class and opponent.place < place:
            ahead += 1
    return ahead + 1


def said_place(race):
    """His place as the radio says it: "P7 in class" in a multiclass race, "P7" otherwise. Replay of
    the 58-car race (27 Sep): "Settled. P54, up seven." and "P43." were overall places among
    Hypercars and LMP2s, when among the GT3s he races he was P18 and P7."""
    if multiclass(race):
        return f"P{class_place(race, race.me.place, race.me.car_class)} in class"
    return f"P{race.me.place}"


def track_length(race, model=None):
    """The lap in metres: the session's own figure. Live 25 Sep: the farthest car's distance
    stood in for it, and on lap 1 that is ~1.9 km of a 13.6 km lap, so a 20-minute race at Le Mans
    looked 9.9 laps long and the fuel call said "short by 3.8 laps".
    Without the session's figure (tapes before 25 Sep), the longest lap distance seen all race
    (the race model's clock) beats the farthest car now: on 24 Sep the farthest car was the
    leader himself, 1.1 km from the line, so he looked to be on the line (26 Sep)."""
    length = getattr(race.session, "lap_length", None)
    if length and length > 1000:
        return length
    farthest_now = max([o.lap_dist for o in race.opponents] + [0.0])
    seen = model.lap_length if model is not None else None
    if seen and seen > farthest_now:
        return seen
    return farthest_now


def rolling_lap(model, key):
    """The car's last 13.6 km of road in seconds (race model), or None. Wherever it starts, so
    no standing start, no pit stop line, no -1 from an invalid lap."""
    if model is None:
        return None
    clock = getattr(model, "clock", None)
    if clock is not None and not getattr(clock, "fixed_length", True):
        return None  # a guessed, growing track length warps the trail (old tapes)
    try:
        lap = model.road_lap(key)
    except Exception:
        return None
    return lap if lap and lap > 30 else None


def road_time_to_line(model, key):
    """Seconds the car took last lap from where it is now to the line (race model), or None."""
    if model is None:
        return None
    clock = getattr(model, "clock", None)
    if clock is not None and not getattr(clock, "fixed_length", True):
        return None  # a guessed, growing track length warps the trail (old tapes)
    try:
        seconds = model.road_time_to_line(key)
    except Exception:
        return None
    return seconds


def time_to_line(leader, leader_lap, lap_length, model, key):
    """Seconds until the leader is over the line. From the leader's own last lap from this point
    when the race model has it: Le Mans ends in the slow Ford chicanes, 5% of the distance and
    ~10% of the time. Live 27 Sep, 684 m from the line: 12 s by distance, 25 s on the road, and
    "laps to go" was a lap out for minutes at a time (and the fuel with it)."""
    done = (
        (leader.lap_dist / lap_length)
        if lap_length > 1000 and getattr(leader, "lap_dist", None) is not None
        else 0.0
    )
    by_distance = (1.0 - min(max(done, 0.0), 1.0)) * leader_lap
    on_road = road_time_to_line(model, key)
    if on_road is not None and abs(on_road - by_distance) < 0.5 * leader_lap:
        return on_road
    return by_distance


def laps_to_go(race, lap_time, model=None):
    """Laps still to drive, this one included, counted at the line. A lap race: max laps minus
    laps done. A timed race: the flag drops when the overall LEADER first crosses the line after
    the clock runs out, and I finish that same lap (minus any laps I am down).
    Live 25 Sep: a "5-lap" race became 6 because the leader was lapping faster than him; Apex
    counted with HIS lap time and said "last lap" on lap 5. Now it counts with the leader's
    own lap time and where the leader is on the lap. lap_time (mine) is only the fallback."""
    me = race.me
    session = race.session
    if 0 < session.max_laps < 1000:
        return max(0, session.max_laps - me.laps)
    leader = (
        me if me.place == 1 else next((o for o in race.opponents if o.place == 1), None)
    )
    if leader is None:
        # the leader is not in the data: my own lap time and my gap to the leader
        if lap_time is None or lap_time <= 0:
            return None
        return math.ceil(
            (session.time_remaining + max(0.0, me.time_behind_leader)) / lap_time
        )
    leader_lap = leader_lap_time(race, leader, lap_time, model)
    if leader_lap is None:
        return None
    lap_length = track_length(race, model)
    to_line = time_to_line(
        leader, leader_lap, lap_length, model, "me" if leader is me else leader.id
    )
    more = (
        0
        if session.time_remaining <= to_line
        else math.ceil((session.time_remaining - to_line) / leader_lap)
    )
    leader_finishes_on = leader.laps + 1 + more
    if getattr(leader, "finish_status", 0) == 1:
        # the leader has taken the flag: the race is those laps (live 25 Sep: on his last lap
        # Apex still counted one more and said "box this lap for fuel")
        leader_finishes_on = leader.laps
    return max(0, leader_finishes_on - me.laps_behind_leader - me.laps)


def leader_lap_time(race, leader, lap_time, model):
    """The leader's pace, or None: its rolling lap on the road first, then the laps the game
    posted for it, my own lap time last."""
    me = race.me
    # the rolling lap on the road first. Live 25 Sep, lap 2: the game posted -1 for the leader's
    # laps and his own lap 1 (4:23, a standing start) made the race a lap short. Only once the
    # leader's last lap of road is all racing: 5% into lap 2, so the window starts after the
    # launch (live 25 Sep, lap 1: the window still held formation-lap road and fuel said "fine,
    # push" at 0.3 laps spare; at 15% the lap-1 line fell back to his 4:23 standing start)
    length = track_length(race, model)
    clean = leader.laps >= 2 or (
        leader.laps == 1 and length > 1000 and leader.lap_dist > 0.05 * length
    )
    rolling = rolling_lap(model, "me" if leader is me else leader.id) if clean else None
    posted = []
    for lap in (leader.last_lap, leader.best_lap):
        if lap and lap > 0:
            posted.append(lap)
    if (
        rolling is not None
        and posted
        and abs(rolling - min(posted)) > 0.2 * min(posted)
    ):
        rolling = (
            None  # a rolling lap 20% off anything the game posted is a broken trail
        )
    candidates = (
        (rolling, lap_time, me.last_lap, me.best_lap)
        if leader is me
        else (rolling, leader.last_lap, leader.best_lap, lap_time)
    )
    return next((t for t in candidates if t is not None and t > 0), None)


def leader_margin(race, model=None):
    """Seconds between the clock running out and the leader's next time over the line
    (> 0: the clock runs out first, so the leader's current lap is the last). None when it
    cannot be known. Live 25 Sep it was ~2 s: the leader pushed, crossed with time left, and a
    "5-lap" race ran 6."""
    session = race.session
    if 0 < session.max_laps < 1000 or session.time_remaining <= 0:
        return None
    leader = next((o for o in race.opponents if o.place == 1), None)
    if leader is None:
        return None
    leader_lap = next(
        (t for t in (leader.last_lap, leader.best_lap) if t and t > 0), None
    )
    lap_length = track_length(race, model)
    if leader_lap is None or lap_length < 1000:
        return None
    to_line = time_to_line(leader, leader_lap, lap_length, model, leader.id)
    return round(to_line - session.time_remaining, 1)
