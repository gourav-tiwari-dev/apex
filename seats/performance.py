"""Performance engineer: where the time is going, corner by corner, while you drive.

Live cues (v1's detector lines, now radio calls): lock-ups, off-tracks, spins, coasting,
and a rear that snaps on the brakes.

Corner feedback: after each lap, the one corner where you were furthest below your own best
minimum speed this session, if it is more than noise. And once per corner, how the fastest
car in your class carries speed through it, measured from its own telemetry in this race.

Earned praise, at most twice a race and only for something measured (Gourav's July
"micro-win" idea): a new personal best lap.
"""
import statistics

from radio import Call, ENGINEER, PERFORMANCE, MEMORY
from track_map import corner_at

# the kinds v1 spoke; HARD_BRAKING and CORNER_ENTRY are recorded, never said
SPOKEN_KINDS = {"SPIN", "OFF_TRACK", "LOCKUP", "THROTTLE_LIFT"}
INCIDENTS = {"SPIN", "OFF_TRACK", "LOCKUP"}
STALE_AFTER_S = 6.0      # v1's STALE_THRESHOLD: advice about a corner 6 s ago is useless

# his min-speed spread per corner on the 11-lap tape was 1-5 km/h: less than this is noise
LOSS_WORTH_SAYING_KMH = 4.0
FASTEST_GAP_WORTH_SAYING_KMH = 4.0
LAPS_FOR_A_REFERENCE = 2     # one lap of another car is not a reference
MAX_PRAISE_PER_SESSION = 2
CORNER_CALL_TTL_S = 15.0
QUALIFYING = range(5, 9)


def call_from_event(event, event_id):
    if event.kind not in SPOKEN_KINDS:
        return None
    if event.kind in INCIDENTS:
        priority = ENGINEER
    else:
        priority = PERFORMANCE
    return Call(
        seat="performance",
        kind=event.kind,
        sim_time=event.sim_time,
        priority=priority,
        ttl=STALE_AFTER_S,
        conclusion=event.conclusion,
        facts={"corner": event.corner, "speed_kmh": round(event.speed_kmh)},
        template=event.conclusion,
        evidence={"event_id": event_id},
    )


class OpponentCorners:
    """Every same-class car's minimum speed through each corner, from the race snapshots.
    Snapshots come 5 times a second, so a minimum is only good to a km/h or two."""

    def __init__(self):
        self.inside = {}      # opponent id -> [corner, lap, lowest speed so far]
        self.rows = []        # finished corners: (steam_id, driver, car_class, corner, lap, min_speed)

    def update(self, race, corners, my_class):
        for opponent in race.opponents:
            if opponent.speed_kmh is None or opponent.car_class != my_class:
                continue
            corner = corner_at(corners, opponent.lap_dist)
            current = self.inside.get(opponent.id)
            if current is not None and current[0] != corner:
                self.rows.append((str(opponent.steam_id), opponent.driver, opponent.car_class,
                                  current[0], current[1], round(current[2], 1)))
                current = None
                del self.inside[opponent.id]
            if corner is None:
                continue
            if current is None:
                self.inside[opponent.id] = [corner, opponent.laps, opponent.speed_kmh]
            elif opponent.speed_kmh < current[2]:
                current[2] = opponent.speed_kmh

    def fastest_through(self, corner):
        """(driver, typical min speed) of the car that carries the most speed through it."""
        speeds_by_driver = {}
        for steam_id, driver, car_class, row_corner, lap, speed in self.rows:
            if row_corner == corner:
                speeds_by_driver.setdefault(driver, []).append(speed)
        fastest = None
        for driver, speeds in speeds_by_driver.items():
            if len(speeds) < LAPS_FOR_A_REFERENCE:
                continue
            typical = statistics.median(speeds)
            if fastest is None or typical > fastest[1]:
                fastest = (driver, round(typical, 1))
        return fastest


class PerformanceEngineer:
    def __init__(self):
        self.best_min_speed = {}     # corner -> my best minimum speed this session
        self.my_speeds = {}          # corner -> every minimum speed I did this session
        self.this_lap = []           # (loss, corner, speed, best) of corners finished this lap
        self.fastest_said = set()    # corners already compared to the fastest car
        self.best_lap_seen = None
        self.praise_given = 0
        self.opponents = OpponentCorners()

    def update(self, moment):
        calls = []
        race = moment.race
        now = moment.now

        if race is not None and race.me is not None and moment.new_race and moment.corners:
            self.opponents.update(race, moment.corners, race.me.car_class)

        stat = moment.corner_stat
        if stat is not None and stat.min_speed is not None and stat.lap_count >= 1:
            self.corner_finished(stat, now, calls)

        if moment.lap_wrapped:
            calls.extend(self.lap_finished(moment, now))
        return calls

    def corner_finished(self, stat, now, calls):
        corner = stat.corner
        speed = round(stat.min_speed, 1)
        best = self.best_min_speed.get(corner)
        if best is not None:
            self.this_lap.append((round(best - speed, 1), corner, speed, best))
        if best is None or speed > best:
            self.best_min_speed[corner] = speed
        self.my_speeds.setdefault(corner, []).append(speed)

        # how the fastest car in my class takes this corner, once per corner per session
        if corner in self.fastest_said or len(self.my_speeds[corner]) < LAPS_FOR_A_REFERENCE:
            return
        fastest = self.opponents.fastest_through(corner)
        if fastest is None:
            return
        driver, their_speed = fastest
        mine = round(statistics.median(self.my_speeds[corner]), 1)
        gap = round(their_speed - mine, 1)
        if gap < FASTEST_GAP_WORTH_SAYING_KMH:
            return
        self.fastest_said.add(corner)
        calls.append(Call(
            seat="performance", kind="FASTEST_CAR", sim_time=now, priority=PERFORMANCE,
            ttl=CORNER_CALL_TTL_S,
            conclusion=f"{driver}, the fastest car in your class, carries {their_speed} km/h through {corner}. You carry {mine}. That corner is where the time is.",
            facts={"corner": corner, "driver": driver, "their_kmh": their_speed, "your_kmh": mine, "gap_kmh": gap},
            template=f"{corner}: the fastest car carries {gap} more. That's the corner."))

    def lap_finished(self, moment, now):
        calls = []
        worst = None
        for loss, corner, speed, best in self.this_lap:
            if loss >= LOSS_WORTH_SAYING_KMH and (worst is None or loss > worst[0]):
                worst = (loss, corner, speed, best)
        self.this_lap = []
        if worst is not None:
            loss, corner, speed, best = worst
            calls.append(Call(
                seat="performance", kind="CORNER_LOSS", sim_time=now, priority=PERFORMANCE,
                ttl=CORNER_CALL_TTL_S,
                conclusion=f"{corner} this lap: {speed} km/h at the slowest point, your best today is {best}. Carry more speed through the middle.",
                facts={"corner": corner, "speed_kmh": speed, "best_kmh": best, "loss_kmh": loss},
                template=f"{corner}: {loss} down on your best."))

        race = moment.race
        if race is not None and race.me is not None:
            calls.extend(self.lap_time_calls(race.me, moment, now))
        return calls

    def lap_time_calls(self, me, moment, now):
        calls = []
        best = me.best_lap if me.best_lap > 0 else None
        if best is not None and self.best_lap_seen is not None and best < self.best_lap_seen \
                and self.praise_given < MAX_PRAISE_PER_SESSION:
            gain = round(self.best_lap_seen - best, 2)
            self.praise_given += 1
            calls.append(Call(
                seat="performance", kind="PRAISE", sim_time=now, priority=MEMORY,
                ttl=CORNER_CALL_TTL_S,
                conclusion=f"New personal best, {gain} seconds quicker. Tell him that is his lap, earned.",
                facts={"gain_s": gain},
                template="New best lap. That's your lap."))
        if best is not None:
            self.best_lap_seen = best

        # qualifying (E15): every lap, how far from the best
        if moment.session_type in QUALIFYING and me.last_lap > 0 and best is not None:
            minutes = int(me.last_lap // 60)
            seconds = round(me.last_lap - minutes * 60, 1)
            delta = round(me.last_lap - best, 1)
            calls.append(Call(
                seat="performance", kind="QUALI_LAP", sim_time=now, priority=PERFORMANCE,
                ttl=CORNER_CALL_TTL_S,
                conclusion=f"Lap {minutes}:{seconds:04.1f}, {delta} off your best.",
                facts={"minutes": minutes, "seconds": seconds, "delta_s": delta},
                template=f"{minutes}:{seconds:04.1f}. {delta} off your best."))
        return calls
