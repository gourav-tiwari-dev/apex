"""Passes made and lost: racecraft's PASS LIFECYCLE (his ask, 24 Sep 2026).

A car that changed sides of him is a pass once that has held PASS_CONFIRM_S with
nobody alongside. A pass he made: "stick it" while the other car is in his tow,
then praise once it is done (held through a braking zone, or clear), naming how it
was made (late on the brakes, a switchback, the exit, the tow...). Taken back
before that: "go again". A gift (a car that pitted, crashed or retired) is called,
never praised. A pass on him: the switchback while he is still in its tow, else one
calm line."""

from game.race_snapshot import identity
from race.facts import same_lap, said_place
from radio.calls import ENGINEER
from radio.words import tenths_words
from seats.praise import BRILLIANT, BRILLIANT_MOVES, MOVE_WORDS, SOLID
from seats.spotter import sides_taken

# live 24 Sep: "lost the place" was raised 30 times in 6 minutes of lap 1, the start shuffle
LOST_PLACE_GAP_S = 60.0


# the pass lifecycle
# MEASURED on his tapes (5 races, 23-24 Sep, Le Mans; small sample, 2-4 passes a band): at the
# end of each straight, within 0.5 s of the car ahead he gains +1.1 to +3.8 km/h over clean air,
# from 0.5 to 1.0 s about nothing. A weak tow, as LMU players say, and only inside 0.5 s.
TOW_S = 0.5
CLEAR_GAP_S = 1.0  # out of the tow and out of reach
PASS_DONE_GAP_S = 0.5  # after a braking zone, this far back = the pass is done
STICK_WARN_M = 200.0  # "cover the inside" needs this much road before the corner
SWITCHBACK_WINDOW_S = 15.0  # passed back within this = a switchback
# replaying 24 Sep: side by side for a lap, the order flipped back and forth and gave
# "stick it / they're back / stick it". A pass counts once it holds this long, nobody alongside
PASS_CONFIRM_S = 1.0
EXIT_RECENT_S = 6.0  # a pass this soon after leaving a corner was won on the exit
DOUBLE_PASS_S = 3.0  # two passes this close together = two for one


def pass_done(pass_, gap):
    """A pass is done when the car is at least PASS_DONE_GAP_S back after a braking zone, or
    clear. Live 25 Sep: praise came for a car still 0.1-0.4 s behind for 45 s ("I did not make
    the overtake completely") and for a place being swapped back ("wrong call")."""
    if gap is None:
        return False
    if pass_["braking_zones_held"] >= 1 and gap >= PASS_DONE_GAP_S:
        return True
    return gap >= CLEAR_GAP_S


class PassLifecycle:
    """Racecraft's passes, kept in their own file. Racecraft is built from this class
    (class Racecraft(PassLifecycle, ...)), so `self` here is the Racecraft: its state
    for passes (last_order, flips, open_passes, passed_me_at, last_earned_pass_at,
    last_lost_place_time, praise_lines) is set up with the rest in Racecraft.__init__."""

    # ---- passes made, passes lost ----------------------------------------------------------
    def order_around_me(self, race):
        """car id -> True if ahead of me: same class, same lap (a lapped car is not a place)."""
        me = race.me
        order = {}
        for opponent in race.opponents:
            if opponent.car_class != me.car_class or not same_lap(
                me, opponent, self.model
            ):
                continue
            if abs(opponent.place - me.place) <= 3:
                order[opponent.id] = opponent.place < me.place
        return order

    def watch_places(self, race, moment, corners, now):
        """Places won and lost: a car that changed sides of him is a pass once that has held
        PASS_CONFIRM_S with nobody alongside."""
        order = self.order_around_me(race)
        if self.last_order is not None:
            self.note_flips(order, moment, now)
        self.last_order = order
        left, right = sides_taken(moment)
        calls = []
        for car_id in list(self.flips):
            direction, since, move = self.flips[car_id]
            if now - since < PASS_CONFIRM_S or left or right or car_id not in self.cars:
                continue
            del self.flips[car_id]
            car = self.cars[car_id]
            if direction == "i_passed":
                calls += self.i_passed(car, race, moment, now, move)
            else:
                calls += self.passed_me(car, moment, corners, now)
        return calls

    def note_flips(self, order, moment, now):
        """Every car that changed sides of him since the last snapshot, with how a pass of mine
        was made; one that flipped straight back is still side by side and is forgotten."""
        for car_id, was_ahead in self.last_order.items():
            if car_id not in order or was_ahead == order[car_id]:
                continue
            if car_id in self.flips:
                del self.flips[car_id]  # flipped straight back: still side by side
            elif was_ahead:
                self.flips[car_id] = (
                    "i_passed",
                    now,
                    self.move_of(moment, car_id, now),
                )
            else:
                self.flips[car_id] = ("passed_me", now, None)

    def move_of(self, moment, car_id, now):
        """How the pass was made, read at the moment the order flipped."""
        passed_me = self.passed_me_at.get(car_id)
        if passed_me is not None and now - passed_me <= SWITCHBACK_WINDOW_S:
            return "switchback"
        if (
            self.last_earned_pass_at is not None
            and now - self.last_earned_pass_at <= DOUBLE_PASS_S
        ):
            return "double"
        if moment.frame.brake > 0.2:
            return "late_brake"
        if moment.corner is not None:
            return "corner"
        if (
            self.last_corner_exit is not None
            and now - self.last_corner_exit <= EXIT_RECENT_S
        ):
            return "exit"  # just out of a corner and already past: the exit won it
        return "tow"

    def touched(self, car, moment):
        for event in moment.events:
            if event.kind == "CONTACT" and event.other_car == identity(car):
                return True
        return False

    def i_passed(self, car, race, moment, now, move):
        gift = None
        if car.in_pits or car.pit_state != 0:
            gift = "Car ahead's pitting."
        elif car.finish_status not in (0, 1):
            gift = "Car ahead's out."
        elif self.reputation.hit_recently(car.id, now) and not self.touched(
            car, moment
        ):
            gift = "Car ahead's in trouble."
        if gift is not None:
            return [
                self.instant(
                    "PLACE_GIFT",
                    f"{said_place(race)}. {gift}",
                    now,
                    {"place": race.me.place},
                    seat="race_engineer",
                    priority=ENGINEER,
                )
            ]
        self.last_earned_pass_at = now
        self.open_passes[car.id] = {
            "move": move,
            "at": now,
            "corner": moment.corner,
            "left_pass_corner": False,
            "braking_zones_held": 0,
            "stick_said": False,
            "contact": False,
        }
        self.passed_me_at.pop(car.id, None)
        return []

    def passed_me(self, car, moment, corners, now):
        self.passed_me_at[car.id] = now
        if car.id in self.open_passes:
            del self.open_passes[car.id]  # taken back before it was held
            strong = self.strong_corner_against(car)
            words = "They're back past. Go again."
            if strong is not None:
                words += f" You're quicker out of {strong}."
            return [self.instant("PASS_RETAKEN", words, now, {"strong_corner": strong})]
        # lap 1 is the start shuffle; after it, one composure call a minute at most
        settled = moment.lap_count >= 2 and (
            self.last_lost_place_time is None
            or now - self.last_lost_place_time >= LOST_PLACE_GAP_S
        )
        if not settled:
            return []
        self.last_lost_place_time = now
        nearest = self.next_corner(moment.frame.lap_dist, corners)
        gap = self.clock.gap_ahead(car.id)
        if gap is not None and gap <= TOW_S and nearest is not None:
            # still in its tow: the switchback, right now
            return [
                self.instant(
                    "PASSED",
                    f"Stay in the tow. Get it back into {nearest[0]}.",
                    now,
                    {"corner": nearest[0]},
                )
            ]
        return [
            self.reset(
                "PASSED",
                "Lost the place. Calm him down: stay within 1 second, the plan still works, no lunge to get it straight back.",
                {"stay_within_s": 1},
                "Lost it. Stay close. No lunge.",
                now,
            )
        ]

    def follow_passes(self, moment, corners, now):
        """The passes not yet held: "stick it" while the car is in his tow, then praise once
        the pass is done (one line for two cars at once), none for a pass with contact."""
        calls = []
        held_moves = []
        stick_now = False
        for car_id in list(self.open_passes):
            pass_ = self.open_passes[car_id]
            gap = self.gap_to_passed(car_id)
            if not stick_now:
                stick = self.stick_it(pass_, gap, moment, corners, now)
                if stick is not None:
                    stick_now = True  # two cars passed at once: one "stick it"
                    calls.append(stick)
            if not pass_done(pass_, gap):
                continue
            del self.open_passes[car_id]
            # the pressure right after my pass is the pass being held, praised as the pass: a
            # defence starts counting from here (live 27 Sep: "mega defending" for the same fight)
            self.pressure_since.pop(car_id, None)
            self.pressure_last.pop(car_id, None)
            if pass_["contact"]:
                continue  # contact: the reset covers it, no praise
            held_moves.append(pass_["move"])
        if len(held_moves) > 1:
            calls.append(
                self.praise("double", now)
            )  # two cars at once: one line, not two
        elif held_moves:
            calls.append(self.praise(held_moves[0], now))
        return calls

    def gap_to_passed(self, car_id):
        """How far back a car I passed is: its same-point gap, else the car-behind gap when it
        is the car behind."""
        gap = self.clock.gap_behind(car_id)
        if gap is None and self.behind is not None and self.behind.id == car_id:
            gap = self.gap_behind
        return gap

    def stick_it(self, pass_, gap, moment, corners, now):
        """Says "stick it" once per pass, while the car is in his tow and there is road enough
        before the next corner to cover the inside; else None."""
        in_tow = gap is not None and gap <= TOW_S
        if pass_["stick_said"] or not in_tow or moment.corner is not None:
            return None
        nearest = self.next_corner(moment.frame.lap_dist, corners)
        if nearest is None or nearest[1] < STICK_WARN_M:
            return None
        pass_["stick_said"] = True
        return self.instant(
            "STICK_IT",
            f"Stick it. They're in your tow. Cover the inside into {nearest[0]}.",
            now,
            {"corner": nearest[0]},
        )

    def praise(self, move, now):
        pool = BRILLIANT if move in BRILLIANT_MOVES else SOLID
        hype = self.praise_lines.next("praise", pool)
        # no "Clear." first (his call, 25 Sep): it doubled the spotter's "Clear."
        words = f"{hype} {MOVE_WORDS[move]}"
        if self.ahead is not None and self.gap_ahead is not None:
            words += f" Next one, {tenths_words(self.gap_ahead)}."
        return self.instant(
            "PASS_PRAISE", words, now, {"move": move, "gap_ahead_s": self.gap_ahead}
        )
