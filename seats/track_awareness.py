"""Track awareness: what is happening on the road AHEAD and in the mirrors, beyond the car
beside him.

His ask (24 Sep 2026): "track awareness is very poor, it can't detect three wide cars up front
(don't fight them, let them fight and take advantage), swarm detection, fights going on up
ahead so the cars ahead are slowing each other down". Research on what real spotters and
Crew Chief call (its FlagsMonitor, MulticlassWarnings, Opponents and Timings modules):
a car gone off or stopped ahead, an incident in a corner, faster classes fighting behind,
slower cars fighting ahead, being held up.

Two kinds of call, two voices:
  HAZARDS       the spotter, standard voice, the moment they are seen: three wide ahead,
                a stopped or crawling car ahead, a faster class closing, faster cars
                fighting behind
  OPPORTUNITIES Max: a battle or a train of cars ahead, with what to do about it

Names are never said: positions ("P7 and P8") and classes ("Hypercar") only.
"""

import collections
import math
import statistics

from driving.track_map import corner_at
from race.facts import same_lap

from race.gaps import TrackClock
from radio.calls import Call, SPOTTER, RACECRAFT
from radio.words import tenths_words
from game.constants import GREEN_FLAG, RACE_SESSIONS


LOOK_AHEAD_M = 600.0  # hazards this far up the road are worth a call (~8 s at 270 km/h)
THREE_WIDE_LOOK_M = 400.0
ALONGSIDE_M = 6.0  # two cars this close in lap distance are side by side
# and three abreast needs the outside cars this far apart across the track (two car widths). Replay
# of 24 Sep: 4 of 22 "three wide" were 2.8-3.9 m across, two alongside and one tucked in behind (27 Sep)
THREE_WIDE_ACROSS_M = 4.0
SLOW_SHARE = 0.5  # a car at half the NORMAL speed for where it is is a hazard
STOPPED_KMH = 20.0
TELEPORT_M = (
    300.0  # a car that moved this far between two snapshots did not drive there
)
NORMAL_BIN_M = 50.0  # normal speed is learned for every 50 m of track
NORMAL_SAMPLES = 40  # the latest passes kept per 50 m
NORMAL_MIN_SAMPLES = 8  # fewer passes than this: only a stopped car is called
HAZARD_TTL_S = 8.0  # 3 s let all 3 LMP2 warnings expire unspoken live on 25 Sep
HAZARD_REARM_S = 20.0  # the same hazard is not called again for this long

BATTLE_GAP_S = 0.5  # two cars this close for FIGHT_FOR_S are fighting
FIGHT_FOR_S = 10.0  # a battle or train must hold together this long before it is called
TRAIN_GAP_S = 1.0  # three or more cars each within this = a train
TRAIN_MAX_CARS = 5  # past this it is "5-plus cars": nobody counts a whole field
ALREADY_IN_IT_S = 1.0  # closer than this he is part of the fight: racecraft coaches it
OPPORTUNITY_REACH_S = 3.0  # he is this close to the back of it
OPPORTUNITY_TTL_S = 15.0
OPPORTUNITY_REARM_S = 120.0
GROUP_REARM_S = (
    300.0  # the same group (by its last car) is not called again for this long
)

# A faster class is called about 15 s before it will be on him, from how fast the gap is really
# shrinking. Replay of the 58-car race (27 Sep): "LMP2 behind, 2.5 seconds" came 39-50 s before each
# LMP2 got to him (at 2.5 s it closed ~0.05 s a second), so it sounded like nothing came. His mark:
# "LMP do not detected". Their posted laps were no use: those LMP2s had slow last laps (trouble)
FASTER_CLASS_WATCH_S = 3.0  # a faster class this close behind is watched
FASTER_CLASS_ARRIVES_S = 15.0  # called when it will be on him within this
RIGHT_BEHIND_S = 0.8  # this close it is called whatever it is doing
CLOSING_HISTORY_S = (
    20.0  # the gap's shrink rate is read over this long, at least 8 s: an LMP2
)
# gains on the straights, not in the corners, and 8 s on a straight
# said "15 s" to cars 32 and 51 s away (58-car race, 27 Sep)
MIN_CLOSING_HISTORY_S = 8.0
MIN_CLOSING = 0.01  # seconds of gap a second: slower than this is not closing
# LMU's classes, fastest first (WEC 2024-25: Hypercar, LMP2, LMGT3)
CLASS_SPEED = {
    "hypercar": 3,
    "lmh": 3,
    "lmdh": 3,
    "lmp2": 2,
    "lmp3": 1,
    "gte": 1,
    "lmgt3": 0,
    "gt3": 0,
}


def class_rank(car_class):
    lowered = (car_class or "").lower().replace(" ", "")
    for name, rank in CLASS_SPEED.items():
        if name in lowered:
            return rank
    return 0


def spoken_class(car_class):
    lowered = (car_class or "").lower()
    if "hyper" in lowered or "lmh" in lowered or "lmdh" in lowered:
        return "Hypercar"
    if "lmp2" in lowered:
        return "LMP2"
    return "Faster car"


class NormalSpeed:
    """The speed cars normally do at each 50 m of this track: the median of the latest passes
    of every car. A car is 'slow' only against THIS. The first version compared it with MY
    speed, and on the 24 Sep replay every car braking for Mulsanne Corner, Arnage or
    Indianapolis was "Slow car ahead" (23 calls in 15 minutes)."""

    def __init__(self):
        self.passes = {}  # 50 m bin -> latest speeds seen there
        self.last_bin = {}  # car id -> the bin its last sample went into

    def see(self, car_id, lap_dist, speed_kmh):
        # one sample per car per pass, so a car parked in a bin cannot become its "normal"
        where = int(lap_dist // NORMAL_BIN_M)
        if self.last_bin.get(car_id) == where:
            return
        self.last_bin[car_id] = where
        self.passes.setdefault(where, collections.deque(maxlen=NORMAL_SAMPLES)).append(
            speed_kmh
        )

    def at(self, lap_dist):
        speeds = self.passes.get(int(lap_dist // NORMAL_BIN_M))
        if speeds is None or len(speeds) < NORMAL_MIN_SAMPLES:
            return None
        return statistics.median(speeds)


class TrackAwareness:
    def __init__(self):
        self.lap_length = None
        self.said_at = {}  # hazard / opportunity key -> when it was last called
        self.class_gaps = {}  # faster-class car id -> [(time, gap behind me)], the last 8 s
        self.positions = {}  # car id -> (x, z) at the last snapshot, for their direction
        self.normal = NormalSpeed()
        self.model = None
        self.clock = TrackClock()  # same-point gaps between the cars ahead
        self.group_since = (
            None  # (id of the car ahead, since when its group has held together)
        )
        self.last_group_call = None
        self.last_seen_at = {}  # car id -> its lap distance at the last snapshot
        self.parked = (
            set()
        )  # cars that jumped to where they stand: in the garage, not on track

    def fresh(self, key, now, rearm):
        last = self.said_at.get(key)
        if last is not None and now - last < rearm:
            return False
        self.said_at[key] = now
        return True

    def ahead_of_me(self, car, my_lap_dist):
        """Metres up the road from me to that car, 0..lap."""
        distance = car.lap_dist - my_lap_dist
        if distance < 0:
            distance += self.lap_length
        return distance

    def hazard(self, kind, text, now, facts):
        return Call(
            seat="spotter",
            kind=kind,
            sim_time=now,
            priority=SPOTTER,
            ttl=HAZARD_TTL_S,
            conclusion=text,
            template=text,
            facts=facts,
            immediate=True,
            voice="spotter",
        )

    def opportunity(self, kind, text, now, facts):
        # not immediate: a group up the road is coaching, so it waits its turn in the talk
        # budget. As immediate it skipped the budget and TRAIN_AHEAD went out 57 times.
        return Call(
            seat="racecraft",
            kind=kind,
            sim_time=now,
            priority=RACECRAFT,
            ttl=OPPORTUNITY_TTL_S,
            conclusion=text,
            template=text,
            facts=facts,
        )

    def update(self, moment):
        """Hazards on the road ahead (slow or stopped cars, three wide), faster classes behind,
        and fights up the road he can close on."""
        race = moment.race
        self.see(moment)
        if race is None or race.me is None or not moment.new_race:
            return []
        self.learn_lap_length(race, moment.frame.lap_dist)
        if not self.watching(race, moment):
            return []
        now = moment.now
        corners = moment.corners or []
        calls = []
        calls += self.slow_or_stopped_ahead(race, moment, corners, now)
        calls += self.three_wide_ahead(race, moment, now)
        calls += self.faster_class_behind(race, moment.frame.lap_dist, now)
        calls += self.fights_ahead(race, now)
        return calls

    def see(self, moment):
        """Read the team's clock (or feed my own), watch for parked cars, and learn the normal
        speed at every point of the track from the cars driving it."""
        race = moment.race
        shared = moment.model is not None
        self.model = moment.model
        if shared:
            self.clock = moment.model.clock  # the one clock the whole team reads
        if race is not None and race.me is not None and moment.new_race:
            if not shared:
                self.clock.see_race(race, moment.now)
            for car in race.opponents:
                self.watch_parked(car)
                if (
                    not car.in_pits
                    and car.speed_kmh is not None
                    and car.id not in self.parked
                ):
                    self.normal.see(car.id, car.lap_dist, car.speed_kmh)
        if not shared:
            self.clock.see_me(moment.frame.lap_dist, moment.now)

    def learn_lap_length(self, race, my_lap_dist):
        """The lap's length: the farthest any car (or he) has been from the line."""
        for opponent in race.opponents:
            if self.lap_length is None or opponent.lap_dist > self.lap_length:
                self.lap_length = opponent.lap_dist
        if my_lap_dist > (self.lap_length or 0):
            self.lap_length = my_lap_dist

    def watching(self, race, moment):
        """Only under green, out of the pits, in a race, once the lap length is known."""
        if (
            race.session.game_phase != GREEN_FLAG
            or race.me.in_pits
            or not self.lap_length
        ):
            return False
        if moment.session_type is not None and moment.session_type not in RACE_SESSIONS:
            return False
        return True

    def watch_parked(self, car):
        """A car that jumped hundreds of metres in one snapshot did not drive there: "return to
        garage". Live 27 Sep: a car crashed at 2,612 m, reappeared at 89 m among the garages 2 s
        later, stopped and not flagged in the pits, and was called "Car stopped ahead, before Dunlop
        Chicane" with nothing on the track. Parked until it drives off again."""
        before = self.last_seen_at.get(car.id)
        self.last_seen_at[car.id] = car.lap_dist
        if before is None or not self.lap_length:
            return
        moved = abs(car.lap_dist - before)
        moved = min(
            moved, abs(self.lap_length - moved)
        )  # over the line is a short move
        if moved > TELEPORT_M:
            self.parked.add(car.id)
        elif (
            car.id in self.parked
            and car.speed_kmh is not None
            and car.speed_kmh >= STOPPED_KMH
        ):
            self.parked.discard(car.id)

    # ---- hazards ---------------------------------------------------------------------------
    def slow_or_stopped_ahead(self, race, moment, corners, now):
        my_speed = moment.frame.speed_kmh
        if my_speed < 60:
            return []
        worst = self.slowest_ahead(race, moment.frame.lap_dist)
        if worst is None:
            return []
        car, ahead = worst
        where = self.place_on_track(car.lap_dist, corners, corner_at)
        stopped = car.speed_kmh < STOPPED_KMH
        # one call per car: "slow car" then "car stopped" 0.6 s later was the same car (23 Sep)
        key = ("hazard", car.id)
        if not self.fresh(key, now, HAZARD_REARM_S):
            return []
        # and one per place: two cars of one crash were two calls 0.2 s apart (replay of 23 Sep,
        # 1248.0 / 1248.2 before Mulsanne Chicane 2, 27 Sep)
        if where and not self.fresh(("hazard_at", where), now, HAZARD_REARM_S):
            return []
        what = "Car stopped" if stopped else "Slow car"
        text = f"{what} ahead, {where}." if where else f"{what} ahead."
        return [
            self.hazard(
                "CAR_STOPPED_AHEAD" if stopped else "SLOW_CAR_AHEAD",
                text,
                now,
                {"metres": round(ahead), "corner": where},
            )
        ]

    def slowest_ahead(self, race, my_lap_dist):
        """The slowest car on the road just ahead that is stopped, or far under the normal speed
        there: (car, metres ahead), or None."""
        worst = None
        for car in race.opponents:
            if car.in_pits or car.speed_kmh is None or car.id in self.parked:
                continue
            ahead = self.ahead_of_me(car, my_lap_dist)
            if not 30 < ahead <= LOOK_AHEAD_M:
                continue
            if car.speed_kmh >= STOPPED_KMH:
                normal = self.normal.at(car.lap_dist)
                if normal is None or car.speed_kmh > normal * SLOW_SHARE:
                    continue
            if worst is None or car.speed_kmh < worst[0].speed_kmh:
                worst = (car, ahead)
        return worst

    def place_on_track(self, lap_dist, corners, corner_at):
        """ "T1 Rettifilo" inside a corner, "before Arnage" on the straight leading to one."""
        if not corners:
            return None
        inside = corner_at(corners, lap_dist)
        if inside:
            return inside
        following = None
        for corner in corners:
            ahead = corner["start"] - lap_dist
            if ahead < 0:
                ahead += self.lap_length
            if following is None or ahead < following[1]:
                following = (corner["name"], ahead)
        return f"before {following[0]}"

    def three_wide_ahead(self, race, moment, now):
        before = self.remember_positions(race)
        cars = self.cars_just_ahead(race, moment.frame.lap_dist)
        for i in range(len(cars) - 2):
            first, third = cars[i], cars[i + 2]
            if third[0] - first[0] > ALONGSIDE_M:
                continue
            across = self.across_the_track([c for _, c in cars[i : i + 3]], before)
            if across is not None and across < THREE_WIDE_ACROSS_M:
                continue  # two alongside and one behind
            # once in 20 s, not once per trio: replay of 24 Sep, 22 calls in one race (18 really
            # three wide), 5 of them in 12 s as one pack jostled into new trios (27 Sep)
            if self.fresh(("three_wide",), now, HAZARD_REARM_S):
                return [
                    self.hazard(
                        "THREE_WIDE_AHEAD",
                        "Three wide ahead. Stay out of it, let them fight.",
                        now,
                        {"metres": round(first[0])},
                    )
                ]
            return []
        return []

    def remember_positions(self, race):
        """Every car's position now, kept for the next snapshot; returns the ones from before."""
        before = self.positions
        self.positions = {}
        for car in race.opponents:
            if car.x is not None and car.z is not None:
                self.positions[car.id] = (car.x, car.z)
        return before

    def cars_just_ahead(self, race, my_lap_dist):
        """(metres ahead, car) for cars 20 m to THREE_WIDE_LOOK_M up the road, nearest first."""
        cars = []
        for car in race.opponents:
            if car.in_pits:
                continue
            ahead = self.ahead_of_me(car, my_lap_dist)
            if 20 < ahead <= THREE_WIDE_LOOK_M:
                cars.append((ahead, car))
        cars.sort(key=lambda item: item[0])
        return cars

    def across_the_track(self, trio, before):
        """Metres between the outside cars of three, across the lead car's direction of travel (from
        its last position). None when there are no positions: the lap-distance rule stands alone."""
        lead = trio[0]
        was = before.get(lead.id)
        if was is None or lead.x is None:
            return None
        dx = lead.x - was[0]
        dz = lead.z - was[1]
        moved = math.hypot(dx, dz)
        if moved < 0.5:
            return None
        offsets = []
        for car in trio:
            if car.x is None or car.z is None:
                return None
            offsets.append(((car.x - lead.x) * dz - (car.z - lead.z) * dx) / moved)
        return max(offsets) - min(offsets)

    def faster_class_behind(self, race, my_lap_dist, now):
        coming, watched = self.faster_cars_behind(race, my_lap_dist, now)
        if not coming:
            return []
        coming.sort(key=lambda item: item[0])
        gap, arrives, car = coming[0]
        partner = self.fight_partner(car, gap, watched)
        if partner is not None:
            key = ("faster_fight", tuple(sorted((car.id, partner.id))))
            if self.fresh(key, now, OPPORTUNITY_REARM_S):
                text = f"Two {spoken_class(car.car_class)}s fighting behind. Stay predictable, hold your line."
                return [
                    self.hazard(
                        "FASTER_FIGHT_BEHIND", text, now, {"gap_s": round(gap, 1)}
                    )
                ]
            return []
        if not self.fresh(("faster", car.id), now, OPPORTUNITY_REARM_S):
            return []
        if arrives is None:
            text = f"{spoken_class(car.car_class)} right behind you. Hold your line, let it by on the exit."
        else:
            text = (
                f"{spoken_class(car.car_class)} behind, closing. On you in about {round(arrives)} seconds. "
                "Hold your line, let it by on the exit."
            )
        facts = {"gap_s": round(gap, 1)}
        if arrives is not None:
            facts["arrives_in_s"] = round(arrives)
        return [self.hazard("FASTER_CLASS_BEHIND", text, now, facts)]

    def faster_cars_behind(self, race, my_lap_dist, now):
        """Faster-class cars behind him on the road: (coming, watched). coming = (gap, arrives,
        car) for the ones on him soon or stuck right behind; watched = every one in the watch."""
        mine = class_rank(race.me.car_class)
        coming = []
        watched = []  # every faster car within the watch, closing or not
        for car in race.opponents:
            if car.in_pits or class_rank(car.car_class) <= mine:
                continue
            metres = self.metres_behind_on_road(car, my_lap_dist)
            if metres is None:
                continue
            gap = self.road_gap_behind(metres)
            if gap is None or gap > FASTER_CLASS_WATCH_S:
                continue
            arrives = self.arrives_in(car, gap, now)
            watched.append((gap, car))
            soon = arrives is not None and arrives <= FASTER_CLASS_ARRIVES_S
            # right there, not getting by
            stuck_behind = arrives is None and gap <= RIGHT_BEHIND_S
            if soon or stuck_behind:
                coming.append((gap, arrives, car))
        return coming, watched

    def fight_partner(self, car, gap, watched):
        """A fight: another faster car right with the first, whether or not its own arrival is
        known yet."""
        for other_gap, other in watched:
            if other.id != car.id and abs(other_gap - gap) <= BATTLE_GAP_S:
                return other
        return None

    def arrives_in(self, car, gap, now):
        """Seconds until that car is on him: the gap over how fast it is shrinking, measured on the
        road over the last 20 s (at least 8). None before that, or when it is not closing. Not from
        the speed difference: an LMP2 at 300 on the straight while he brakes at 150 read as "on you
        in 3 seconds" at a 3 s gap, five false calls on the 58-car race (27 Sep)."""
        history = self.class_gaps.setdefault(car.id, [])
        history.append((now, gap))
        while history and now - history[0][0] > CLOSING_HISTORY_S:
            history.pop(0)
        first_time, first_gap = history[0]
        if now - first_time < MIN_CLOSING_HISTORY_S:
            return None
        closing = (first_gap - gap) / (now - first_time)
        if closing < MIN_CLOSING:
            return None
        return gap / closing

    def road_gap_behind(self, metres):
        """Seconds since I was where that car is now, on the road. A faster class is usually a lap
        up, so its race distance never matches mine and the race gap was None all the way in
        (replay of the 58-car race, 27 Sep): the road is what counts. None until my trail has it."""
        mine = self.clock.my_distance()
        if mine is None or not self.clock.mine.time:
            return None
        when_i_was_there = self.clock.mine.time_at(mine - metres)
        if when_i_was_there is None:
            return None
        return self.clock.mine.time[-1] - when_i_was_there

    def metres_behind_on_road(self, car, my_lap_dist):
        """Metres behind me on the road, for cars within a kilometre behind and moving."""
        if car.speed_kmh is None or car.speed_kmh < 30:
            return None
        behind = my_lap_dist - car.lap_dist
        if behind < 0:
            behind += self.lap_length
        if behind > 1000.0:
            return None
        return behind

    def fights_ahead(self, race, now):
        """Same-class cars ahead, close to each other, and him 1-3 s behind them: a battle (2)
        or a train (3+). They are slowing each other down: close up and take them.

        Gaps are same-point gaps (gaps.py), not the game's gap, which swung 3.5 -> 4.0 s in
        10 s on 24 Sep. The group must hold together for FIGHT_FOR_S, and it is named by the
        car directly ahead of him, so cars joining or leaving the front of a train do not make
        it "new" (the first version keyed on every car in it and called it 57 times)."""
        rivals = self.rivals_ahead(race)
        if not rivals or rivals[0].place != race.me.place - 1:
            self.group_since = None
            return []
        nearest = rivals[0]
        reach = self.clock.gap_ahead(nearest.id)
        if reach is None or not ALREADY_IN_IT_S < reach <= OPPORTUNITY_REACH_S:
            self.group_since = None
            return []
        chain, gaps = self.chain_ahead(nearest, rivals)
        is_battle = len(chain) == 2 and gaps[0] <= BATTLE_GAP_S
        if len(chain) < 3 and not is_battle:
            self.group_since = None
            return []
        if not self.group_worth_a_call(nearest, now):
            return []
        self.last_group_call = now
        if is_battle:
            text = (
                f"P{chain[1].place} and P{chain[0].place} are fighting, {tenths_words(reach)} up the road. "
                f"They're slowing each other down. Close up and let them fight."
            )
            return [
                self.opportunity("BATTLE_AHEAD", text, now, {"gap_s": round(reach, 1)})
            ]
        if len(chain) == TRAIN_MAX_CARS:  # it may go on further up: no end position
            who = f"at least {len(chain)} cars from P{chain[0].place} up"
        else:
            who = f"{len(chain)} cars, P{chain[-1].place} to P{chain[0].place}"
        text = (
            f"Train ahead, {who}, {tenths_words(reach)} up the road. "
            f"They're holding each other up. Close in and pick them off."
        )
        return [
            self.opportunity(
                "TRAIN_AHEAD", text, now, {"cars": len(chain), "gap_s": round(reach, 1)}
            )
        ]

    def rivals_ahead(self, race):
        """His class, on his lap, out of the pits, ahead of him: nearest first."""
        me = race.me
        rivals = []
        for car in race.opponents:
            if car.car_class != me.car_class or car.in_pits or car.place >= me.place:
                continue
            if same_lap(me, car, self.model):
                rivals.append(car)
        rivals.sort(key=lambda car: car.place, reverse=True)  # nearest first
        return rivals

    def chain_ahead(self, nearest, rivals):
        """The cars in a row from the one directly ahead of him, each within TRAIN_GAP_S of the
        next: (chain, gaps between them)."""
        chain = [nearest]
        gaps = []
        for car in rivals[1:]:
            if car.place != chain[-1].place - 1 or len(chain) == TRAIN_MAX_CARS:
                break
            gap = self.clock.gap_between(car.id, chain[-1].id)
            if gap is None or gap > TRAIN_GAP_S:
                break
            chain.append(car)
            gaps.append(gap)
        return chain, gaps

    def group_worth_a_call(self, nearest, now):
        """The group has held for FIGHT_FOR_S, no group call was made lately, and not this one."""
        if self.group_since is None or self.group_since[0] != nearest.id:
            self.group_since = (nearest.id, now)
        if now - self.group_since[1] < FIGHT_FOR_S:
            return False
        if (
            self.last_group_call is not None
            and now - self.last_group_call < OPPORTUNITY_REARM_S
        ):
            return False
        return self.fresh(("group", nearest.id), now, GROUP_REARM_S)
