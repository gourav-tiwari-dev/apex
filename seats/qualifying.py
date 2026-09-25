"""The qualifying engineer (25 Sep 2026, his mark: "Apex isn't aware about qualifying").

Every other seat is built for racing, so qualifying was silent. What a real engineer gives in
qualifying: the lap against his best and where it puts him (class position, gap to pole), a
warning when traffic up the road will spoil the flying lap, and the clock (one more lap or not).
Lap times are the game's (so a deleted lap does not count as a best); gaps are the race model's.
"""
from radio import Call, ENGINEER

QUALI_SESSIONS = range(5, 9)
GREEN = 5
FLAG_OUT = 8                   # clock ran out: the lap he is on still counts, and is often the one
TRAFFIC_S = 3.0                # a car this close ahead on the road will cost the lap
TRAFFIC_EVERY_S = 30.0
TTL_S = 12.0


def lap_text(seconds):
    minutes = int(seconds // 60)
    rest = round(seconds - minutes * 60, 1)
    if rest >= 60.0:
        minutes, rest = minutes + 1, round(rest - 60.0, 1)
    return f"{minutes}:{rest:04.1f}"


def call(kind, words, now, facts):
    return Call(seat="race_engineer", kind=kind, sim_time=now, priority=ENGINEER, ttl=TTL_S,
                conclusion=words, template=words, facts=facts)


class QualifyingEngineer:
    def __init__(self):
        self.my_best = None
        self.traffic_said_at = None
        self.last_lap_said = False
        self.out_lap = False        # left the pits since the line: the lap to make space on

    def update(self, moment):
        race = moment.race
        if race is None or race.me is None or moment.session_type not in QUALI_SESSIONS:
            return []
        if race.session.game_phase not in (GREEN, FLAG_OUT):
            return []
        me, now = race.me, moment.now
        calls = []
        if me.in_pits:
            self.out_lap = True
        if moment.lap_wrapped:
            if me.last_lap > 0 and not me.in_pits:
                calls += self.lap_done(race, me.last_lap, now)
            calls += self.clock(race, now)
            self.out_lap = me.in_pits
        # traffic only on the out lap, where he can still make space; on the flying lap the
        # engineer keeps quiet (replay 25 Sep: "make a gap" 11 s before the line was useless)
        if self.out_lap and moment.new_race and moment.model is not None and not me.in_pits:
            calls += self.traffic(moment.model, now)
        return calls

    def lap_done(self, race, lap, now):
        me = race.me
        if not 30 <= lap <= 900:
            return []
        # the game's best counts only laps that stand; a best still unset means none has yet
        best = me.best_lap if me.best_lap > 0 else None
        self.my_best = best
        words = f"{lap_text(lap)}."
        facts = {"lap_s": round(lap, 3)}
        if best is None:
            words += " Doesn't count."
        elif abs(lap - best) < 0.05:
            words += " Best lap."
        else:
            words += f" {round(lap - best, 1)} off your best."
            facts["off_best_s"] = round(lap - best, 1)
        if best is not None:
            rivals = [o.best_lap for o in race.opponents if o.car_class == me.car_class and o.best_lap > 0]
            position = 1 + sum(1 for t in rivals if t < best)
            words += f" P{position} in class"
            facts["class_position"] = position
            if position > 1:
                off = round(best - min(rivals), 1)
                words += f", {off} off pole."
                facts["off_pole_s"] = off
            else:
                words += ", on pole."
        return [call("QUALI_LAP", words, now, facts)]

    def clock(self, race, now):
        """At the line: is there time for another lap after this one? A lap started before the
        clock runs out still counts. Before he has a time, the class's best lap stands in."""
        left = race.session.time_remaining
        me = race.me
        rivals = [o.best_lap for o in race.opponents if o.car_class == me.car_class and o.best_lap > 0]
        lap = self.my_best or (min(rivals) if rivals else None)
        if lap is None or left <= 0 or self.last_lap_said or me.in_pits:
            return []
        if left < lap:
            self.last_lap_said = True
            return [call("QUALI_CLOCK", "Clock runs out on this lap. It's the last one, make it count.", now,
                         {"time_left_s": round(left)})]
        if left < 2 * lap:
            self.last_lap_said = True
            return [call("QUALI_CLOCK", "Time for one more flying lap after this one.", now, {"time_left_s": round(left)})]
        return []

    def traffic(self, model, now):
        if self.traffic_said_at is not None and now - self.traffic_said_at < TRAFFIC_EVERY_S:
            return []
        order = model.order()
        if "me" not in order or order.index("me") == 0:
            return []
        ahead = order[order.index("me") - 1]
        gap = model.gap(ahead, "me")
        if gap is None or not 0 < gap <= TRAFFIC_S:
            return []
        self.traffic_said_at = now
        words = f"Traffic {gap:.1f} ahead. Make a gap for the lap."
        return [call("QUALI_TRAFFIC", words, now, {"gap_s": round(gap, 1)})]
