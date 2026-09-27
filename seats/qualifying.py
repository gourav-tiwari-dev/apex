"""The qualifying engineer (25 Sep 2026, his mark: "Apex isn't aware about qualifying").

Every other seat is built for racing, so qualifying was silent. What a real engineer gives in
qualifying: the lap against his best and where it puts him (class position, gap to pole), and
the clock (one more lap or not). Lap times are the game's (so a deleted lap does not count).

No traffic or clean-air calls: in LMU qualifying he is alone on track (his fact, 25 Sep). The
other drivers are only on the timing sheet, so a "car ahead" there is a ghost.
"""

from radio import Call, ENGINEER
from game.constants import GREEN_FLAG, QUALIFYING_SESSIONS, SESSION_OVER

TTL_S = 12.0


def lap_text(seconds):
    minutes = int(seconds // 60)
    rest = round(seconds - minutes * 60, 1)
    if rest >= 60.0:
        minutes, rest = minutes + 1, round(rest - 60.0, 1)
    return f"{minutes}:{rest:04.1f}"


def call(kind, words, now, facts):
    return Call(
        seat="race_engineer",
        kind=kind,
        sim_time=now,
        priority=ENGINEER,
        ttl=TTL_S,
        conclusion=words,
        template=words,
        facts=facts,
    )


class QualifyingEngineer:
    def __init__(self):
        self.my_best = None
        self.last_lap_said = False

    def update(self, moment):
        race = moment.race
        if (
            race is None
            or race.me is None
            or moment.session_type not in QUALIFYING_SESSIONS
        ):
            return []
        # after the clock runs out the lap he is on still counts, and is often the one
        if race.session.game_phase not in (GREEN_FLAG, SESSION_OVER):
            return []
        me, now = race.me, moment.now
        calls = []
        if moment.lap_wrapped:
            if me.last_lap > 0 and not me.in_pits:
                calls += self.lap_done(race, me.last_lap, now)
            calls += self.clock(race, now)
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
            rivals = [
                o.best_lap
                for o in race.opponents
                if o.car_class == me.car_class and o.best_lap > 0
            ]
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
        rivals = [
            o.best_lap
            for o in race.opponents
            if o.car_class == me.car_class and o.best_lap > 0
        ]
        lap = self.my_best or (min(rivals) if rivals else None)
        if lap is None or left <= 0 or self.last_lap_said or me.in_pits:
            return []
        if left < lap:
            self.last_lap_said = True
            return [
                call(
                    "QUALI_CLOCK",
                    "Clock runs out on this lap. It's the last one, make it count.",
                    now,
                    {"time_left_s": round(left)},
                )
            ]
        if left < 2 * lap:
            self.last_lap_said = True
            return [
                call(
                    "QUALI_CLOCK",
                    "Time for one more flying lap after this one.",
                    now,
                    {"time_left_s": round(left)},
                )
            ]
        return []
