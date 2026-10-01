"""A car closing fast: the alarm for the car behind (said in the spotter's voice, it
cannot wait), and "closing fast on the car ahead".

Closing is judged on the same-point gap: on the lap pace once there is a lap of both
trails, else on the live rate over the last 10 s. Where it will catch is logged with
every alarm, never said yet: on the 24 Sep tapes the alarm came true 5 times in 8,
the corner only 2."""

from race.gaps import ON_YOU_S
from radio.calls import SPOTTER
from radio.words import tenths_words

# the closing alarm, scored on the 24 Sep tapes (see closing_calls): 10 s rate windows beat 20
# and 30 s; alarms at 1.95 s were noise and at 0.32 s too late, so 1.5 s down to 0.5 s
ALARM_MAX_GAP_S = 1.5
ALARM_MIN_GAP_S = 0.5
ALARM_MIN_RATE = 0.02  # s of gap gone per s, over the last 10 s: 5 of 8 came true
ALARM_MIN_PACE_S = (
    0.5  # s a lap closing on the lap-level pace, once there is a lap of it
)
ALARM_REARM_GAP_S = 2.0
ALARM_TTL_S = 4.0


class ClosingAlarm:
    """Racecraft's closing calls, kept in their own file. Racecraft is built from this
    class (class Racecraft(PassLifecycle, ClosingAlarm)), so `self` here is the
    Racecraft: the cars ahead and behind, their real gaps and where they were measured,
    the track clock, and the cars already called (alarmed, closing_called), all set up
    in Racecraft.__init__."""

    # ---- closing fast ----------------------------------------------------------------------
    def closing_calls(self, corners, now):
        """A car closing fast, from the live closing rate of the same-point gap.

        Where it will catch is NOT said yet. Scored on the 24 Sep tapes: the live rate's
        alarms came true 5 times in 8, but the corner was right 2 times; the lap-apart model
        fired once, came true, wrong corner. Both predictions are logged with every alarm
        (facts) so they can be scored over the next races, and the corner goes on the radio
        once one of them is proven."""
        calls = []
        for side in ("behind", "ahead"):
            call = self.closing_call(side, corners, now)
            if call is not None:
                calls.append(call)
        return calls

    def closing_call(self, side, corners, now):
        """The closing call for the car behind or the car ahead, or None."""
        if side == "behind":
            car, gap, said = self.behind, self.gap_behind, self.alarmed
        else:
            car, gap, said = self.ahead, self.gap_ahead, self.closing_called
        if car is None or gap is None:
            return None
        if gap > ALARM_REARM_GAP_S:
            said.discard(car.id)
        if car.id in said or not ALARM_MIN_GAP_S <= gap <= ALARM_MAX_GAP_S:
            return None
        point = self.gap_points.get(side)
        if point is None:
            return None  # the game's gap: the rate is fitted on same-point gaps only
        closing = self.closing_speed(car, side, now)
        if closing is None:
            return None
        rate, per_lap = closing
        said.add(car.id)
        facts = self.closing_facts(car, gap, point, rate, per_lap, corners)
        if side == "ahead":
            words = f"Closing fast on the car ahead. {tenths_words(gap).capitalize()}."
            return self.instant("CLOSING_ON", words, now, facts)
        words = f"Car behind, {tenths_words(gap)}, closing fast."
        reputation = self.reputation.words(car)
        if reputation is not None:
            words += f" {reputation}"
        return self.instant(
            "CLOSING_ALARM",
            words,
            now,
            facts,
            seat="spotter",
            priority=SPOTTER,
            ttl=ALARM_TTL_S,
            voice="spotter",
        )

    def closing_speed(self, car, side, now):
        """(rate, per_lap) when the car is really closing, else None. rate: seconds of gap gone
        a second, over the last 10 s; per_lap: seconds a lap it closes, once there is a lap of
        both trails."""
        if car.in_pits or car.pit_state != 0:
            return None  # a car pitting is not a car closing (PITS_AHEAD says it)
        # a car that was already on him (or just passed) and is dropping back is not closing:
        # the 10 s rate still leans on the older, closer gaps. Replays of 23 and 25 Sep (27 Sep):
        # "Closing fast on the car ahead" 2 s after that car passed him, and at 0.19 -> 0.51 s
        closest = self.clock.closest_lately(car.id, now)
        if closest is not None and closest < ALARM_MIN_GAP_S:
            return None
        # closing is judged on the lap once there is a lap of both trails: at Le Mans the gap
        # breathes +-0.5 s inside a lap, and a 10 s rate measures the breathing. Replays of the
        # 7 race tapes (27 Sep): 10 of 24 closing calls never came within 0.3 s in a lap (one
        # on a car 2.3 s a lap slower); on the lap pace 5 of 22, with the same 9 of 10 arrivals
        # warned. Crew Chief likewise trends the gap over sectors, and iRacedeck against one lap
        # ago
        rate = self.clock.closing_rate(car.id, now)
        quicker = self.clock.pace_vs_me(car.id)  # s a lap that car is quicker than me
        per_lap = None
        if quicker is not None:
            per_lap = quicker if side == "behind" else -quicker
            if per_lap < ALARM_MIN_PACE_S:
                return None
        elif rate is None or rate < ALARM_MIN_RATE:
            return None
        return rate, per_lap

    def closing_facts(self, car, gap, point, rate, per_lap, corners):
        """What a closing call rests on, logged with it: the lap pace, the live rate, and both
        predictions of where it catches (scored later, never said yet)."""
        facts = {"gap_s": gap}
        if per_lap is not None:
            facts["pace_closing_s_per_lap"] = round(per_lap, 2)
        if rate is not None and rate > 0:
            facts["closing_s_per_s"] = round(rate, 3)
            facts["predicted_catch_s"] = round((gap - ON_YOU_S) / rate, 1)
        found = self.clock.catch_point(car.id, point, gap)
        if found is not None and corners:
            facts["predicted_corner_lap_model"] = self.clock.corner_at_or_after(
                found[0], corners
            )
            facts["closing_per_lap_s"] = round(found[1], 2)
        return facts
