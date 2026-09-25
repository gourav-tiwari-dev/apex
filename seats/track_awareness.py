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
from radio import Call, SPOTTER, RACECRAFT
from seats.performance import tenths_words

GREEN = 5
RACE_SESSIONS = range(10, 14)

LOOK_AHEAD_M = 600.0          # hazards this far up the road are worth a call (~8 s at 270 km/h)
THREE_WIDE_LOOK_M = 400.0
ALONGSIDE_M = 6.0             # two cars this close in lap distance are side by side
SLOW_SHARE = 0.5              # a car at half my speed ahead is a hazard
STOPPED_KMH = 20.0
HAZARD_TTL_S = 3.0
HAZARD_REARM_S = 20.0         # the same hazard is not called again for this long

BATTLE_GAP_S = 0.5            # two cars this close for FIGHT_FOR_S are fighting
FIGHT_FOR_S = 10.0
TRAIN_GAP_S = 1.0             # three or more cars each within this = a train
OPPORTUNITY_REACH_S = 3.0     # he is this close to the back of it
OPPORTUNITY_TTL_S = 15.0
OPPORTUNITY_REARM_S = 120.0

FASTER_CLASS_WARN_S = 2.5     # a faster class this close behind gets one call
# LMU's classes, fastest first (WEC 2024-25: Hypercar, LMP2, LMGT3)
CLASS_SPEED = {"hypercar": 3, "lmh": 3, "lmdh": 3, "lmp2": 2, "lmp3": 1, "gte": 1, "lmgt3": 0, "gt3": 0}


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


class TrackAwareness:
    def __init__(self):
        self.lap_length = None
        self.said_at = {}              # hazard / opportunity key -> when it was last called
        self.close_since = {}          # (car id, car id) -> since when these two have been this close

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
        return Call(seat="spotter", kind=kind, sim_time=now, priority=SPOTTER, ttl=HAZARD_TTL_S,
                    conclusion=text, template=text, facts=facts, immediate=True, voice="spotter")

    def opportunity(self, kind, text, now, facts):
        return Call(seat="racecraft", kind=kind, sim_time=now, priority=RACECRAFT, ttl=OPPORTUNITY_TTL_S,
                    conclusion=text, template=text, facts=facts, immediate=True)

    def update(self, moment):
        race = moment.race
        if race is None or race.me is None or not moment.new_race:
            return []
        for opponent in race.opponents:
            if self.lap_length is None or opponent.lap_dist > self.lap_length:
                self.lap_length = opponent.lap_dist
        if moment.frame.lap_dist > (self.lap_length or 0):
            self.lap_length = moment.frame.lap_dist
        if race.session.game_phase != GREEN or race.me.in_pits or not self.lap_length:
            return []
        if moment.session_type is not None and moment.session_type not in RACE_SESSIONS:
            return []
        now = moment.now
        corners = moment.corners or []
        calls = []
        calls += self.slow_or_stopped_ahead(race, moment, corners, now)
        calls += self.three_wide_ahead(race, moment, now)
        calls += self.faster_class_behind(race, moment.frame.lap_dist, now)
        calls += self.fights_ahead(race, now)
        return calls

    # ---- hazards ---------------------------------------------------------------------------
    def slow_or_stopped_ahead(self, race, moment, corners, now):
        from live_telemetry import corner_at
        my_speed = moment.frame.speed_kmh
        if my_speed < 60:
            return []
        worst = None
        for car in race.opponents:
            if car.in_pits or car.speed_kmh is None:
                continue
            ahead = self.ahead_of_me(car, moment.frame.lap_dist)
            if not 30 < ahead <= LOOK_AHEAD_M:
                continue
            if car.speed_kmh > my_speed * SLOW_SHARE:
                continue
            if worst is None or car.speed_kmh < worst[0].speed_kmh:
                worst = (car, ahead)
        if worst is None:
            return []
        car, ahead = worst
        where = self.place_on_track(car.lap_dist, corners, corner_at)
        stopped = car.speed_kmh < STOPPED_KMH
        key = ("stopped" if stopped else "slow", car.id)
        if not self.fresh(key, now, HAZARD_REARM_S):
            return []
        what = "Car stopped" if stopped else "Slow car"
        text = f"{what} ahead, {where}." if where else f"{what} ahead."
        return [self.hazard("CAR_STOPPED_AHEAD" if stopped else "SLOW_CAR_AHEAD", text, now,
                            {"metres": round(ahead), "corner": where})]

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
        cars = []
        for car in race.opponents:
            if car.in_pits:
                continue
            ahead = self.ahead_of_me(car, moment.frame.lap_dist)
            if 20 < ahead <= THREE_WIDE_LOOK_M:
                cars.append((ahead, car))
        cars.sort(key=lambda item: item[0])
        for i in range(len(cars) - 2):
            first, third = cars[i], cars[i + 2]
            if third[0] - first[0] <= ALONGSIDE_M:
                ids = tuple(sorted(c.id for _, c in cars[i:i + 3]))
                if self.fresh(("three_wide", ids), now, HAZARD_REARM_S):
                    return [self.hazard("THREE_WIDE_AHEAD", "Three wide ahead. Stay out of it, let them fight.",
                                        now, {"metres": round(first[0])})]
                return []
        return []

    def faster_class_behind(self, race, my_lap_dist, now):
        me = race.me
        mine = class_rank(me.car_class)
        closing = []
        for car in race.opponents:
            if car.in_pits or class_rank(car.car_class) <= mine:
                continue
            gap = self.seconds_behind_on_road(car, my_lap_dist)
            if gap is not None and 0 < gap <= FASTER_CLASS_WARN_S:
                closing.append((gap, car))
        if not closing:
            return []
        closing.sort(key=lambda item: item[0])
        gap, car = closing[0]
        if len(closing) >= 2 and closing[1][0] - gap <= BATTLE_GAP_S:
            key = ("faster_fight", tuple(sorted((closing[0][1].id, closing[1][1].id))))
            if self.fresh(key, now, OPPORTUNITY_REARM_S):
                text = f"Two {spoken_class(car.car_class)}s fighting behind. Stay predictable, hold your line."
                return [self.hazard("FASTER_FIGHT_BEHIND", text, now, {"gap_s": round(gap, 1)})]
            return []
        if self.fresh(("faster", car.id), now, OPPORTUNITY_REARM_S):
            text = f"{spoken_class(car.car_class)} behind, {tenths_words(gap)}. Hold your line, let it by on the exit."
            return [self.hazard("FASTER_CLASS_BEHIND", text, now, {"gap_s": round(gap, 1)})]
        return []

    def seconds_behind_on_road(self, car, my_lap_dist):
        """How long until that car reaches where I am now: metres behind me on the road at its
        own speed. Only for cars within a kilometre behind."""
        if car.speed_kmh is None or car.speed_kmh < 30:
            return None
        behind = my_lap_dist - car.lap_dist
        if behind < 0:
            behind += self.lap_length
        if behind > 1000.0:
            return None
        return behind / (car.speed_kmh / 3.6)

    # ---- opportunities ---------------------------------------------------------------------
    def fights_ahead(self, race, now):
        """Same-class cars ahead, close to each other, and him close to them: a battle (2) or a
        train (3+). They are slowing each other down: close up and take them."""
        me = race.me
        ahead = []
        for car in race.opponents:
            if car.car_class != me.car_class or car.laps_behind_leader != me.laps_behind_leader or car.in_pits:
                continue
            if car.place < me.place:
                ahead.append(car)
        ahead.sort(key=lambda car: car.place, reverse=True)       # nearest first
        if not ahead:
            return []
        nearest_gap = me.time_behind_leader - ahead[0].time_behind_leader
        if ahead[0].place != me.place - 1 or not 0 < nearest_gap <= OPPORTUNITY_REACH_S:
            return []
        chain = [ahead[0]]
        for car in ahead[1:]:
            if car.place != chain[-1].place - 1:
                break
            if chain[-1].time_behind_leader - car.time_behind_leader > TRAIN_GAP_S:
                break
            chain.append(car)
        if len(chain) < 2:
            return []
        pair = (chain[0].id, chain[1].id)
        pair_gap = chain[0].time_behind_leader - chain[1].time_behind_leader
        if pair_gap <= BATTLE_GAP_S:
            self.close_since.setdefault(pair, now)
        else:
            self.close_since.pop(pair, None)
        fighting = pair in self.close_since and now - self.close_since[pair] >= FIGHT_FOR_S
        places = f"P{chain[-1].place} to P{chain[0].place}"
        if len(chain) >= 3:
            key = ("train", tuple(car.id for car in chain))
            if self.fresh(key, now, OPPORTUNITY_REARM_S):
                text = (f"Train ahead, {len(chain)} cars, {places}, {tenths_words(nearest_gap)} up the road. "
                        f"They're holding each other up. Close in and pick them off.")
                return [self.opportunity("TRAIN_AHEAD", text, now, {"cars": len(chain), "gap_s": round(nearest_gap, 1)})]
            return []
        if fighting:
            key = ("battle", pair)
            if self.fresh(key, now, OPPORTUNITY_REARM_S):
                text = (f"P{chain[1].place} and P{chain[0].place} are fighting, {tenths_words(nearest_gap)} up the road. "
                        f"They're slowing each other down. Close up and let them fight.")
                return [self.opportunity("BATTLE_AHEAD", text, now, {"gap_s": round(nearest_gap, 1)})]
        return []
