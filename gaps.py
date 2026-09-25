"""Real gaps: when two cars passed the SAME point on track, like timing loops do.

Why (measured 24 Sep 2026, Le Mans): the game's own gap to the car behind swings with the
track section, 3.48 -> 3.19 -> 4.04 s in 10 s, because it is worked out from distance and
speed. A closing alarm built on that shouts at every braking zone. He asked for "how far back
is 0.6 seconds, and at what speed is someone closing in", so the gap has to be a real one.

How:
  - every car leaves a trail of (distance raced, sim time): mine every frame, the others at
    the scoring rate (about 5 a second), in between is interpolated
  - distance raced = laps done x lap length + distance into this lap
  - gap to a car behind = now - when I passed the point where it is now
  - gap to a car ahead  = now - when it passed the point where I am now
  - closing rate = the slope of that gap over the last CLOSING_WINDOW_S
  - where it catches, scored on the 24 Sep tapes before choosing:
      * a 10 s closing rate: 5 of 8 alarms came true, the corner right 2 times, the time
        wildly off (said 4 s, took 114 s). At Le Mans the gap breathes +-0.5 s inside a lap
        (the tow on the straights, the braking zones), and a 10 s rate measures the breathing
      * so: the gap now against the gap at this same point one lap ago = the real closing per
        lap; the gap up the road = last lap's gap at each point minus that closing. That keeps
        the shape of the lap (where the chaser gains, where it loses), and the first point
        where the prediction reaches ON_YOU_S is where it will be on him
"""
import bisect

POINT_EVERY_M = 5.0            # a trail point every 5 m is ~0.07 s at 250 km/h: plenty
KEEP_LAPS = 2.2                # trails older than this are dropped
CLOSING_WINDOW_S = 10.0
MIN_CLOSING_SAMPLES = 10
ON_YOU_S = 0.3                 # "on you": close enough to attack at the next braking zone
LOOK_AHEAD_LAPS = 2
PROFILE_STEP_M = 50.0          # the gap to each car is kept every 50 m of track


class Trail:
    """(distance raced, sim time) of one car, oldest first."""

    def __init__(self):
        self.distance = []
        self.time = []

    def add(self, distance, time):
        if self.distance and distance < self.distance[-1] + POINT_EVERY_M:
            if distance < self.distance[-1] - 1000.0:
                self.distance = []        # a reset (pits, a teleport, a new session)
                self.time = []
            else:
                return
        self.distance.append(distance)
        self.time.append(time)

    def time_at(self, distance):
        """When this car was at that distance, or None if the trail does not cover it."""
        if not self.distance or distance < self.distance[0] or distance > self.distance[-1]:
            return None
        i = bisect.bisect_left(self.distance, distance)
        if self.distance[i] == distance or i == 0:
            return self.time[i]
        d0, d1 = self.distance[i - 1], self.distance[i]
        t0, t1 = self.time[i - 1], self.time[i]
        return t0 + (t1 - t0) * (distance - d0) / (d1 - d0)

    def distance_at(self, time):
        """Where this car was at that time, or None if the trail does not cover it."""
        if not self.time or time < self.time[0] or time > self.time[-1]:
            return None
        i = bisect.bisect_left(self.time, time)
        if self.time[i] == time or i == 0:
            return self.distance[i]
        t0, t1 = self.time[i - 1], self.time[i]
        d0, d1 = self.distance[i - 1], self.distance[i]
        return d0 + (d1 - d0) * (time - t0) / (t1 - t0)

    def speed(self, over_s=5.0):
        """Metres a second over the last few seconds, or None."""
        if len(self.time) < 2:
            return None
        i = bisect.bisect_left(self.time, self.time[-1] - over_s)
        i = min(i, len(self.time) - 2)
        seconds = self.time[-1] - self.time[i]
        if seconds <= 0:
            return None
        return (self.distance[-1] - self.distance[i]) / seconds

    def trim(self, keep_from):
        cut = bisect.bisect_left(self.distance, keep_from)
        if cut > 0:
            del self.distance[:cut]
            del self.time[:cut]


class TrackClock:
    def __init__(self):
        self.lap_length = None         # the longest lap distance seen, from any car
        self.mine = Trail()
        self.theirs = {}               # car id -> Trail
        self.history = {}              # car id -> [(sim time, gap)] for the closing rate
        self.profile = {}              # car id -> {50 m step of distance raced: gap there}
        self.my_laps = None            # laps I have completed, counted by my own lap wraps
        self.last_my_lap_dist = None

    # ---- feeding it ------------------------------------------------------------------------
    def see_race(self, race, now):
        """Every scoring snapshot: the other cars' positions."""
        if race is None or race.me is None:
            return
        for opponent in race.opponents:
            if self.lap_length is None or opponent.lap_dist > self.lap_length:
                self.lap_length = opponent.lap_dist
        if self.my_laps is None:
            self.my_laps = race.me.laps
        if self.lap_length is None:
            return
        for opponent in race.opponents:
            if opponent.in_pits:
                continue
            trail = self.theirs.setdefault(opponent.id, Trail())
            trail.add(opponent.laps * self.lap_length + opponent.lap_dist, now)

    def see_me(self, lap_dist, now):
        """Every frame: my own position."""
        if self.my_laps is None or self.lap_length is None:
            self.last_my_lap_dist = lap_dist
            return
        if self.last_my_lap_dist is not None and lap_dist < self.last_my_lap_dist - self.lap_length / 2:
            self.my_laps += 1               # I crossed the line
        self.last_my_lap_dist = lap_dist
        if lap_dist > self.lap_length:
            self.lap_length = lap_dist
        distance = self.my_laps * self.lap_length + lap_dist
        self.mine.add(distance, now)
        keep_from = distance - KEEP_LAPS * self.lap_length
        self.mine.trim(keep_from)
        for trail in self.theirs.values():
            trail.trim(keep_from - self.lap_length)

    def my_distance(self):
        if not self.mine.distance:
            return None
        return self.mine.distance[-1]

    # ---- gaps ------------------------------------------------------------------------------
    def gap_behind(self, car_id, now):
        """Seconds between me and a car behind, at the point where it is now."""
        trail = self.theirs.get(car_id)
        if trail is None or not trail.distance:
            return None
        when_i_was_there = self.mine.time_at(trail.distance[-1])
        if when_i_was_there is None:
            return None
        return trail.time[-1] - when_i_was_there

    def gap_ahead(self, car_id, now):
        """Seconds between a car ahead and me, at the point where I am now."""
        trail = self.theirs.get(car_id)
        me = self.my_distance()
        if trail is None or me is None:
            return None
        when_it_was_here = trail.time_at(me)
        if when_it_was_here is None:
            return None
        return self.mine.time[-1] - when_it_was_here

    def gap_between(self, front_id, back_id):
        """Seconds between two other cars, at the point where the back one is now."""
        front = self.theirs.get(front_id)
        back = self.theirs.get(back_id)
        if front is None or back is None or not back.distance:
            return None
        when_front_was_there = front.time_at(back.distance[-1])
        if when_front_was_there is None:
            return None
        return back.time[-1] - when_front_was_there

    def remember_gap(self, car_id, gap, now, point=None):
        """point: the distance raced where this gap was measured (the chaser's position)."""
        if point is not None:
            self.profile.setdefault(car_id, {})[int(point // PROFILE_STEP_M)] = gap
        samples = self.history.setdefault(car_id, [])
        samples.append((now, gap))
        while samples and now - samples[0][0] > CLOSING_WINDOW_S:
            samples.pop(0)

    def closing_rate(self, car_id):
        """Seconds of gap lost per second (positive = the gap is shrinking), fitted over the
        last CLOSING_WINDOW_S, or None without enough samples."""
        samples = self.history.get(car_id, [])
        if len(samples) < MIN_CLOSING_SAMPLES or samples[-1][0] - samples[0][0] < CLOSING_WINDOW_S / 2:
            return None
        count = len(samples)
        mean_t = sum(t for t, _ in samples) / count
        mean_g = sum(g for _, g in samples) / count
        spread = sum((t - mean_t) ** 2 for t, _ in samples)
        if spread == 0:
            return None
        slope = sum((t - mean_t) * (g - mean_g) for t, g in samples) / spread
        return -slope

    # ---- where it catches ------------------------------------------------------------------
    def segment_time(self, trail, start, end):
        """A car's time from one point to another on the last lap it drove through them."""
        if self.lap_length is None or not trail.distance:
            return None
        laps_back = 1
        while end - laps_back * self.lap_length > trail.distance[-1]:
            laps_back += 1
        a = trail.time_at(start - laps_back * self.lap_length)
        b = trail.time_at(end - laps_back * self.lap_length)
        if a is None or b is None:
            return None
        return b - a

    def catch_point(self, car_id, point, gap, corners=None):
        """(distance raced where it gets within ON_YOU_S, closing per lap), within one lap;
        or None (not closing, or not one full lap of this pair yet). point: the distance
        raced where gap was measured."""
        if self.lap_length is None:
            return None
        profile = self.profile.get(car_id)
        if not profile:
            return None
        step = int(point // PROFILE_STEP_M)
        lap_steps = int(self.lap_length // PROFILE_STEP_M)
        last_lap_here = profile.get(step - lap_steps)
        if last_lap_here is None:
            return None
        per_lap = last_lap_here - gap                  # how much it closed in one lap, here
        if per_lap <= 0:
            return None
        for ahead in range(1, lap_steps):
            last_lap_there = profile.get(step + ahead - lap_steps)
            if last_lap_there is None:
                continue
            if last_lap_there - per_lap <= ON_YOU_S + 1e-6:
                catch_at = (step + ahead) * PROFILE_STEP_M
                return catch_at, per_lap
        return None

    def corner_at_or_after(self, distance, corners):
        """The corner a point of track belongs to, or the next one after it."""
        in_lap = distance % self.lap_length
        ordered = sorted(corners, key=lambda c: c["start"])
        for corner in ordered:
            if corner["start"] <= in_lap <= corner.get("end", corner["start"]):
                return corner["name"]
        for corner in ordered:
            if corner["start"] >= in_lap:
                return corner["name"]
        return ordered[0]["name"] if ordered else None
