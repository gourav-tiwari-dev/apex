"""The race model: ONE picture of the race that every seat and the coach read (25 Sep 2026).

Why: until now every seat kept its own partial picture - racecraft its own gap clock, track
awareness another, the strategist its own lap count - and they contradicted each other live
("17 s a lap quicker" from a start lap, "last lap" a lap early, "no need to pit" with 0.6 laps of
energy). A real pit wall works from one race state and forecasts from it (Pitwall, arXiv
2607.06495: one canonical race state, a live pace filter per car, forecasts that are scored).

What the field study of his four race tapes (tools/field_study.py) decided:
  - the same-point road trail matches the game's lap times to a median 0.036 s: it is the truth
  - between cars racing each other the gap moves ~1.2 s a lap for reasons that are not pace, so
    ONE lap proves little. The pace between two cars is the road trend over the last one or two
    laps (median of 8 stretches a lap), and it comes with how sure it is: with 2 laps of history
    the direction (who is catching) was right ~80% of the time on his tapes
  - a per-car "model pace" against a class reference predicted next laps WORSE than the last lap,
    so pace is always between two cars, on the road, never from a reference

It holds one TrackClock (every car's trail, mine included), the battles between cars and the
pit stops. Seats and the coach only read it.
"""

import math
import statistics

from gaps import TrackClock

STRETCHES = 8  # a lap of road trend = the median of 8 stretches
SURE_LAPS = 2  # two laps of trend: the direction was right ~80% on his tapes
BATTLE_S = 1.0  # same-point gap under this: a fight
BATTLE_FOR_S = 8.0  # ... held this long: a battle (racecraft's 8 s confirmation)
# field study (25 Sep): 20 of 21 sure catch forecasts came true, but the time was off by a
# median 64%, and within 1.5x the forecast 95% of the time for forecasts over a minute: say
# WHETHER and an upper bound, never "in 1.6 laps"
CATCH_UPPER = 1.5
ME = "me"


class RaceModel:
    def __init__(self, lap_length=None):
        self.clock = TrackClock(lap_length)
        self.race = None
        self.now = None
        self.close_since = {}  # (front key, back key) -> since when within BATTLE_S
        self.pit_events = []  # (time, car key, "in" / "out")
        self.was_in_pits = {}

    # ---- feeding it (the live loop, once per snapshot / frame, BEFORE any seat) ----------------
    @property
    def lap_length(self):
        return self.clock.lap_length

    def see_race(self, race, now):
        if race is None or race.me is None:
            return
        self.race, self.now = race, now
        self.clock.see_race(race, now)
        if not self.lap_length:
            return
        for o in race.opponents:
            was = self.was_in_pits.get(o.id)
            if was is not None and was != o.in_pits:
                self.pit_events.append((now, o.id, "in" if o.in_pits else "out"))
            self.was_in_pits[o.id] = o.in_pits
        self.pit_events = [e for e in self.pit_events if now - e[0] < 120]
        self.battles_update(now)

    def see_me(self, lap_dist, now):
        self.clock.see_me(lap_dist, now)

    # ---- where things are ---------------------------------------------------------------------
    def trail(self, key):
        return self.clock.mine if key == ME else self.clock.theirs.get(key)

    def distance(self, key):
        trail = self.trail(key)
        return trail.distance[-1] if trail is not None and trail.distance else None

    def car(self, key):
        if self.race is None:
            return None
        if key == ME:
            return self.race.me
        return next((o for o in self.race.opponents if o.id == key), None)

    # ---- between two cars ---------------------------------------------------------------------
    def gap(self, front, back):
        """Same-point gap: seconds after the front car the back car reached where it is now."""
        f, b = self.trail(front), self.trail(back)
        if f is None or b is None or not b.distance:
            return None
        when_front = f.time_at(b.distance[-1])
        if when_front is None:
            return None
        return round(b.time[-1] - when_front, 2)

    def gap_at(self, front, back, distance):
        f, b = self.trail(front), self.trail(back)
        if f is None or b is None:
            return None
        tf, tb = f.time_at(distance), b.time_at(distance)
        if tf is None or tb is None:
            return None
        return tb - tf

    def trend(self, front, back):
        """How the gap between them is moving, measured on the road over the last one or two laps:
        {"closing_per_lap": + = back car catching, "laps": history used, "sure": bool} or None."""
        d = self.distance(back)
        if d is None or not self.lap_length:
            return None
        L = self.lap_length
        for laps in (SURE_LAPS, 1):
            changes = []
            for i in range(STRETCHES * laps):
                end = d - i * L / STRETCHES
                a, b = (
                    self.gap_at(front, back, end),
                    self.gap_at(front, back, end - L / STRETCHES),
                )
                if a is None or b is None:
                    break
                changes.append(b - a)
            if len(changes) == STRETCHES * laps:
                per_lap = round(statistics.median(changes) * STRETCHES, 2)
                return {
                    "closing_per_lap": per_lap,
                    "laps": laps,
                    "sure": laps >= SURE_LAPS,
                }
        return None

    def quicker(self, a, b):
        """Seconds a lap car a is quicker than car b on the road (negative = slower), with the
        trend's certainty: (seconds, sure) or None. Works whichever of the two is ahead."""
        da, db = self.distance(a), self.distance(b)
        if da is None or db is None:
            return None
        if da >= db:
            t = self.trend(a, b)  # a ahead: b catching means b quicker
            return None if t is None else (-t["closing_per_lap"], t["sure"])
        t = self.trend(b, a)
        return None if t is None else (t["closing_per_lap"], t["sure"])

    def road_lap(self, key):
        """The time the car took over its last lap of road, whatever the game posted."""
        trail = self.trail(key)
        if trail is None or not trail.distance or not self.lap_length:
            return None
        start = trail.time_at(trail.distance[-1] - self.lap_length)
        return None if start is None else round(trail.time[-1] - start, 2)

    def road_time_to_line(self, key):
        """Seconds the car took last lap from where it is now to the line: the rest of its lap with
        the lap's own shape, where the distance alone would spread the time evenly."""
        trail = self.trail(key)
        if trail is None or not trail.distance or not self.lap_length:
            return None
        here = trail.distance[-1]
        line = math.ceil(here / self.lap_length) * self.lap_length
        then = trail.time_at(here - self.lap_length)
        crossed = trail.time_at(line - self.lap_length)
        if then is None or crossed is None:
            return None
        return round(crossed - then, 2)

    def catch(self, chaser, target, until=0.3):
        """Seconds until the chaser is within `until` of the target at the current road trend,
        only when the trend is sure and closing; (seconds, laps) or None."""
        g = self.gap(target, chaser)
        t = self.trend(target, chaser)
        lap = self.road_lap(chaser)
        if (
            g is None
            or t is None
            or not t["sure"]
            or t["closing_per_lap"] <= 0.05
            or not lap
        ):
            return None
        laps = max(g - until, 0.0) / t["closing_per_lap"]
        return round(laps * lap, 0), round(laps, 1)

    def corner_time(self, key, corner, passes=2):
        """Median seconds the car took through the corner over its last `passes` passes."""
        trail = self.trail(key)
        if trail is None or not trail.distance or not self.lap_length:
            return None
        L = self.lap_length
        start, end = corner["start"], corner["end"]
        if end < start:
            end += L
        lap = int((trail.distance[-1] - end) // L)
        times = []
        for n in range(lap, lap - passes, -1):
            a, b = trail.time_at(n * L + start), trail.time_at(n * L + end)
            if a is not None and b is not None and b > a:
                times.append(b - a)
        return statistics.median(times) if times else None

    def corner_gains(self, a, b, corners):
        """corner name -> seconds car a GAINS on car b through it (+) or loses, from the road:
        exact times through each corner, not a speed proxy (racecraft compared minimum speeds,
        which miss braking and exits, where passes are set up)."""
        gains = {}
        for corner in corners or []:
            ta, tb = self.corner_time(a, corner), self.corner_time(b, corner)
            if ta is not None and tb is not None:
                gains[corner["name"]] = round(tb - ta, 2)
        return gains

    # ---- the field ----------------------------------------------------------------------------
    def order(self, car_class=None):
        """Keys on the road, furthest raced first."""
        keys = [ME] + [o.id for o in self.race.opponents] if self.race else []
        rows = []
        for key in keys:
            car = self.car(key)
            d = self.distance(key)
            if car is None or d is None or car.in_pits:
                continue
            if car_class is not None and car.car_class != car_class:
                continue
            rows.append((d, key))
        return [key for _, key in sorted(rows, reverse=True)]

    def battles_update(self, now):
        keys = self.order()
        seen = set()
        for front, back in zip(keys, keys[1:]):
            g = self.gap(front, back)
            if g is not None and 0 <= g <= BATTLE_S:
                self.close_since.setdefault((front, back), now)
                seen.add((front, back))
        for pair in list(self.close_since):
            if pair not in seen:
                del self.close_since[pair]

    def battles(self, now=None):
        """Pairs within a second of each other for BATTLE_FOR_S or longer: [(front, back, gap)]."""
        now = self.now if now is None else now
        return [
            (f, b, self.gap(f, b))
            for (f, b), since in self.close_since.items()
            if now - since >= BATTLE_FOR_S
        ]

    def pitting_near(self, key=ME, within_places=3):
        """Cars that entered the pit lane in the last two minutes and were near him in the order."""
        me = self.car(key)
        if me is None:
            return []
        near = []
        for when, car_key, what in self.pit_events:
            car = self.car(car_key)
            if (
                what == "in"
                and car is not None
                and car.car_class == me.car_class
                and abs(car.place - me.place) <= within_places
            ):
                near.append((when, car_key))
        return near
