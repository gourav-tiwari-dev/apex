"""Racecraft coach: fights, closing cars, passes made and lost, and keeping your head.

This is Gourav's weakness seat (23 Sep 2026): in close racing he gets hasty, commits too
early and makes contact, which costs safety rating. The persona is "aggressive but TIMED":
it never says back off, it says WHERE the move works.

v3 rebuild (24 Sep 2026, after he called the old one "absolute dog shit"):
  - real gaps, measured where both cars passed the same point (gaps.py), not the game's
    estimate that swings with every braking zone
  - a CLOSING ALARM when a car behind closes fast, with the corner where it will be on you,
    from both cars' own times between the corners on their last lap
  - REPUTATION: how many incidents that car has had today, and whether it has hit him
  - a fight is a real fight only after FIGHT_CONFIRM_S within FIGHT_GAP_S
  - the PASS LIFECYCLE he asked for: pass made -> "stick it" while the other car is in his
    tow -> CLEAR once he has held it through the next braking zone -> earned praise, hype
    first, then how he did it. Taken back before that: "go again". Gifts (a car that
    pitted, crashed or retired) are called, never praised
  - DEFEND HELD: a car that sat on his gearbox and fell away gets praise too
  - FIGHT COST: a fight costing him a second a lap with another car coming = decide

What real racecraft says (Driver61, drivingfast.net, Coach Dave, Fanatec's defending guide):
the pass is set up a corner early, exits beat dives, attack where the other car is weak,
defend only where it can get you, one move, and a fight slows both cars down.
"""

import statistics

from radio import Call, RACECRAFT, ENGINEER, SPOTTER
from seats.spotter import (
    side_and_overlap,
    sides_taken,
    CAR_LENGTH_M,
    LANE_MIN_M,
    LANE_MAX_M,
)
from seats.performance import tenths_words
from race_state import same_lap, identity, same_class_neighbours, said_place
from gaps import TrackClock, ON_YOU_S

FIGHT_GAP_S = 1.0  # a same-class car within a second ahead is a fight
DEFEND_GAP_S = 0.8  # and this close behind
FIGHT_CONFIRM_S = 8.0  # ...for this long: a car brushing past is not a fight
EDGE_WORTH_USING_KMH = 3.0  # min-speed advantage that makes a corner yours
GAIN_WORTH_USING_S = 0.15
INCIDENT_KMH = 40.0  # slower than this on track: he's spun or crashed
INCIDENT_QUIET_S = (
    20.0  # ...or, on the road, seconds I gain through it (race model, 25 Sep)
)
CLOSE_GAP_S = 0.4  # this close into a corner where he is faster = about to lunge
WARN_BEFORE_M = 300.0  # "not here" must come before the braking zone, not in it
ATTEMPT_WINDOW_S = 6.0  # how long a pass attempt has to resolve
PLAN_TTL_S = 20.0
# replaying the 23 Sep race with the radio fixed gave 21 plans in 27 minutes: one a minute is
# as much as a driver can use
PLAN_GAP_S = 60.0
# live 24 Sep: "lost the place" was raised 30 times in 6 minutes of lap 1, the start shuffle
LOST_PLACE_GAP_S = 60.0
RACE_SESSIONS = range(10, 14)
GREEN = 5
RESET_TTL_S = 15.0

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
GIFT_IMPACT_S = 5.0  # a car that hit something this recently was not beaten, it crashed
INCIDENT_MERGE_S = (
    5.0  # hits closer than this are one incident (LMU logs several per crash)
)
PRAISE_TTL_S = 8.0

# defending that held: on the gearbox this long, then gone
PRESSURE_GAP_S = 0.6
PRESSURE_FOR_S = 60.0
FELL_AWAY_GAP_S = 1.5
PRESSURE_LAPSE_S = (
    30.0  # off the gearbox this long without falling away: the fight faded, no praise
)

# the price of a fight
FIGHT_COST_S = 1.0  # this much slower than his best, a lap in a fight
THIRD_CAR_GAP_S = 3.0

REPUTATION_INCIDENTS = 2  # this many incidents today = a car to give room to

BRILLIANT = [
    ("WHAT A FUCKING MOVE! Get in there!", "WHAT A MOVE! Get in there!"),
    ("Mega, mate. Absolutely fucking mega.", "Mega, mate. Absolutely mega."),
    ("Oh, get in there! Fucking lovely!", "Oh, get in there! Lovely!"),
]
SOLID = [
    ("Simply lovely, mate.", "Simply lovely, mate."),
    ("Lovely. That's how you fucking do it.", "Lovely. That's how you do it."),
    ("Good job. Clean as you like.", "Good job. Clean as you like."),
]
MOVE_WORDS = {
    "late_brake": "Late on the brakes.",
    "switchback": "Switchback!",
    "double": "Two for one!",
    "corner": "Brave through there.",
    "exit": "Better exit did it.",
    "tow": "Great tow.",
}
BRILLIANT_MOVES = ("late_brake", "switchback", "double", "corner")


def median_or_none(values):
    if not values:
        return None
    return statistics.median(values)


class Reputation:
    """What this race says about each car: incidents (any impact, walls too) and hits on him."""

    def __init__(self):
        self.incidents = {}  # car id -> count this race
        self.last_impact = {}  # car id -> the game's last impact time for that car
        self.hits_on_me = {}  # identity -> contacts with him this race

    def see_race(self, race):
        for opponent in race.opponents:
            impact = opponent.last_impact_time
            if impact is None or impact <= 0:
                continue
            last = self.last_impact.get(opponent.id)
            if last is None:
                self.incidents.setdefault(
                    opponent.id, 0
                )  # an impact from before we watched
            elif impact > last + INCIDENT_MERGE_S:
                self.incidents[opponent.id] = self.incidents.get(opponent.id, 0) + 1
            if last is None or impact > last:
                self.last_impact[opponent.id] = impact

    def hit_recently(self, car_id, now):
        impact = self.last_impact.get(car_id)
        return (
            impact is not None
            and 0 <= now - impact <= GIFT_IMPACT_S
            and self.incidents.get(car_id, 0) > 0
        )

    def words(self, car):
        hits = self.hits_on_me.get(identity(car), 0)
        if hits == 1:
            return "It's already hit you once."
        if hits > 1:
            return f"It's hit you {hits} times."
        incidents = self.incidents.get(car.id, 0)
        if incidents >= REPUTATION_INCIDENTS:
            return f"That car's had {incidents} incidents today."
        return None


class Racecraft:
    def __init__(self, performance, rivals=None, clean=False):
        self.performance = performance  # shares my corner speeds and theirs
        self.rivals = rivals or {}  # steam_id -> team-memory dossier line
        self.clean = clean
        self.clock = TrackClock()
        self.model = None  # the race model, when the live loop shares it
        self.quiet_until = None  # silent after a spin, until this time
        self.corners = []
        self.gap_points = {}  # "ahead" / "behind" -> where the latest gap was measured
        self.reputation = Reputation()
        self.plans_said = set()  # (steam_id, "attack" / "defend", has data)
        self.warned = set()  # (steam_id, corner, lap)
        self.ahead = None
        self.gap_ahead = None
        self.behind = None
        self.gap_behind = None
        self.fight_since = {}  # car id -> when the fight began
        self.last_place = None
        self.last_plan_time = None
        self.last_lost_place_time = None
        self.alarmed = set()  # car ids already alarmed on this approach
        self.closing_called = set()  # car ids already called as "closing on it"
        self.last_order = None  # car id -> True if it was ahead of me
        self.cars = {}  # car id -> the latest Opponent
        self.open_passes = {}  # car id -> the pass I made on it, not yet held
        self.passed_me_at = {}  # car id -> when it last passed me
        self.last_earned_pass_at = None
        self.pressure_since = {}  # car id -> when it got on my gearbox
        self.pressure_last = {}  # car id -> the last time it was on my gearbox
        self.held_said = set()
        self.praise_turn = 0
        self.last_corner = None
        self.last_corner_exit = None  # when I last came out of a corner
        self.flips = {}  # car id -> (who passed whom, when, the move) not yet confirmed
        self.fight_cost_said = set()
        self.my_best_lap = None
        self.offs = []  # sim times of my recent off-tracks
        self.attempts = []  # finished: (steam_id, driver, corner, lap, outcome)
        self.open_attempt = None  # [steam_id, driver, corner, lap, started, place_then]

    # ---- where each of us is faster --------------------------------------------------------
    def edges_against(self, steam_id):
        """corner -> my typical min speed minus his, where both are measured."""
        his_speeds = {}
        for row in self.performance.opponents.rows:
            if row.who == steam_id:
                his_speeds.setdefault(row.corner, []).append(row.min_speed)
        edges = {}
        for corner, my_speeds in self.performance.my_speeds.items():
            mine = median_or_none(my_speeds)
            his = median_or_none(his_speeds.get(corner, []))
            if mine is not None and his is not None:
                edges[corner] = round(mine - his, 1)
        return edges

    def strong_corner_against(self, car):
        # the race model's road times first: seconds I gain through each corner (25 Sep)
        if self.model is not None and self.corners:
            gains = self.model.corner_gains("me", car.id, self.corners)
            best = max(gains, key=gains.get) if gains else None
            if best is not None and gains[best] >= GAIN_WORTH_USING_S:
                return best
            if gains:
                return None
        edges = self.edges_against(identity(car))
        best = None
        for corner, edge in edges.items():
            if edge >= EDGE_WORTH_USING_KMH and (best is None or edge > edges[best]):
                best = corner
        return best

    def corner_after(self, corner, corners):
        names = [c["name"] for c in sorted(corners, key=lambda c: c["start"])]
        if corner not in names:
            return None
        return names[(names.index(corner) + 1) % len(names)]

    def next_corner(self, lap_dist, corners):
        """(name, metres to its braking zone) of the next corner ahead of me."""
        best = None
        for corner in corners:
            ahead = corner["start"] - lap_dist
            if ahead <= 0 and self.clock.lap_length:
                ahead += self.clock.lap_length
            if ahead > 0 and (best is None or ahead < best[1]):
                best = (corner["name"], ahead)
        return best

    def history_with(self, steam_id):
        return self.rivals.get(steam_id)

    def pick(self, pair):
        return pair[1] if self.clean else pair[0]

    # ---- the plans ------------------------------------------------------------------------
    def attack_plan(self, opponent, gap, corners, now):
        edges = self.edges_against(identity(opponent))
        # the gap is for the log, not the line: a plan is where, not how far
        facts = {"driver": opponent.driver}
        history = self.history_with(identity(opponent))
        if history:
            facts["history"] = history
        best = None
        for corner, edge in edges.items():
            if edge >= EDGE_WORTH_USING_KMH and (best is None or edge > edges[best]):
                best = corner
        if best is None:
            facts["stay_within_s"] = 1
            conclusion = "Stay within 1 second of the car ahead and wait for the mistake. No lunges."
            template = "Car ahead. Stay close. Wait for the mistake."
        else:
            pass_at = self.corner_after(best, corners)
            facts.update({"strong_corner": best, "pass_corner": pass_at})
            conclusion = (
                f"Faster out of {best}: pass the car ahead into {pass_at}, not before."
            )
            template = f"You're faster out of {best}. Pass into {pass_at}. Not before."
        reputation = self.reputation.words(opponent)
        if reputation is not None:
            # a reckless car: pass it where it cannot turn in on you
            facts["reputation"] = reputation
            template += f" {reputation} Pass it on the exit, not the brakes."
        if history:
            conclusion += f" History: {history}."
        return Call(
            seat="racecraft",
            kind="ATTACK_PLAN",
            sim_time=now,
            priority=RACECRAFT,
            ttl=PLAN_TTL_S,
            conclusion=conclusion,
            facts=facts,
            template=template,
            evidence={"rival": identity(opponent)},
        )

    def defend_plan(self, opponent, gap, corners, now):
        edges = self.edges_against(identity(opponent))
        facts = {"driver": opponent.driver}
        danger = None
        for corner, edge in edges.items():
            if -edge >= EDGE_WORTH_USING_KMH and (
                danger is None or edge < edges[danger]
            ):
                danger = corner
        if danger is None:
            conclusion = "The car behind has nothing on you. Clean lines, no weaving."
            template = "Car behind. Nothing on you. Clean lines."
        else:
            cover = self.corner_after(danger, corners)
            facts.update({"his_corner": danger, "cover_corner": cover})
            conclusion = f"The car behind is quicker out of {danger}: cover the inside into {cover}."
            template = f"Car behind's quicker out of {danger}. Cover the inside into {cover}. Everywhere else, your line."
        reputation = self.reputation.words(opponent)
        if reputation is not None:
            facts["reputation"] = reputation
            template += f" {reputation} Leave it room."
        return Call(
            seat="racecraft",
            kind="DEFEND_PLAN",
            sim_time=now,
            priority=RACECRAFT,
            ttl=PLAN_TTL_S,
            conclusion=conclusion,
            facts=facts,
            template=template,
            evidence={"rival": identity(opponent)},
        )

    def plan_allowed(self, now):
        return self.last_plan_time is None or now - self.last_plan_time >= PLAN_GAP_S

    def reset(self, kind, conclusion, facts, template, now):
        return Call(
            seat="racecraft",
            kind=kind,
            sim_time=now,
            priority=ENGINEER,
            ttl=RESET_TTL_S,
            conclusion=conclusion,
            facts=facts,
            template=template,
        )

    def instant(
        self,
        kind,
        text,
        now,
        facts=None,
        seat="racecraft",
        priority=RACECRAFT,
        ttl=PRAISE_TTL_S,
        voice="engineer",
    ):
        return Call(
            seat=seat,
            kind=kind,
            sim_time=now,
            priority=priority,
            ttl=ttl,
            conclusion=text,
            template=text,
            facts=facts or {},
            immediate=True,
            voice=voice,
        )

    # ---- gaps ------------------------------------------------------------------------------
    def real_gap(self, car, side, now, game_gap):
        """The same-point gap when both trails cover it, else the game's own estimate."""
        if car is None:
            return None
        if side == "ahead":
            gap = self.clock.gap_ahead(car.id, now)
        else:
            gap = self.clock.gap_behind(car.id, now)
        if gap is None:
            self.gap_points[side] = (
                None  # the game's estimate: no same-point gap to build on
            )
            return game_gap
        if side == "ahead":
            point = self.clock.my_distance()
        else:
            trail = self.clock.theirs.get(car.id)
            point = trail.distance[-1] if trail is not None and trail.distance else None
        self.clock.remember_gap(car.id, gap, now, point)
        self.gap_points[side] = point
        return round(gap, 2)

    def in_fight(self, car, gap, limit, now):
        if car is None or gap is None or gap > limit:
            if car is not None:
                self.fight_since.pop(car.id, None)
            return False
        since = self.fight_since.setdefault(car.id, now)
        return now - since >= FIGHT_CONFIRM_S

    # ---- every frame -----------------------------------------------------------------------
    def share(self, model):
        """Read the race model's clock (the one the whole team uses) instead of keeping my own."""
        self.model = model
        self.clock = model.clock

    def update(self, moment):
        race = moment.race
        now = moment.now
        if moment.new_race and race is not None and race.me is not None:
            if self.model is None:
                self.clock.see_race(race, now)
            self.reputation.see_race(race)
        if self.model is None:
            self.clock.see_me(moment.frame.lap_dist, now)
        if race is None or race.me is None:
            return []
        # qualifying places change all the time as others set laps: "you lost a place"
        # fired 7 times in quali on 23 Sep. Fights and composure are for races.
        if moment.session_type is not None and moment.session_type not in RACE_SESSIONS:
            return []
        if race.me.in_pits or race.session.game_phase != GREEN:
            return []
        # after a spin, or crawling: no racecraft (live 25 Sep, spun at Indianapolis, it kept saying
        # "mega defending" and "stay in the tow")
        if (
            any(event.kind == "SPIN" for event in moment.events)
            or moment.frame.speed_kmh < INCIDENT_KMH
        ):
            self.quiet_until = now + INCIDENT_QUIET_S
        if self.quiet_until is not None and now < self.quiet_until:
            return []
        me = race.me
        corners = moment.corners or []
        self.corners = corners
        calls = []

        for event in moment.events:
            if event.kind == "CONTACT":
                who = event.other_car
                if who is not None:
                    self.reputation.hits_on_me[who] = (
                        self.reputation.hits_on_me.get(who, 0) + 1
                    )
                calls.append(
                    self.reset(
                        "CONTACT_RESET",
                        f"Contact at {event.corner}. Calm him down. No blame, no driving instruction. Next lap, reset.",
                        {"corner": event.corner},
                        "Reset. Next lap.",
                        now,
                    )
                )
                if self.open_attempt is not None:
                    self.finish_attempt("contact")
                for pass_ in self.open_passes.values():
                    pass_["contact"] = True  # a pass with contact is not praised
            if event.kind in ("OFF_TRACK", "SPIN"):
                self.offs = [t for t in self.offs if now - t < 240.0] + [now]
                if len(self.offs) == 2:
                    calls.append(
                        self.reset(
                            "TWO_OFFS",
                            "Two offs in a short time. Calm him down: two tidy laps, nothing else.",
                            {},
                            "Two offs. Two tidy laps now.",
                            now,
                        )
                    )

        # a braking zone survived = a corner left after the one the pass was made in
        if self.last_corner is not None and moment.corner != self.last_corner:
            self.last_corner_exit = now
            for pass_ in self.open_passes.values():
                if pass_["corner"] is None or pass_["left_pass_corner"]:
                    pass_["braking_zones_held"] += 1
                pass_["left_pass_corner"] = True
        self.last_corner = moment.corner

        if moment.new_race:
            self.cars = {opponent.id: opponent for opponent in race.opponents}
            self.ahead, game_gap_ahead, self.behind, game_gap_behind = (
                same_class_neighbours(race, self.model)
            )
            self.gap_ahead = self.real_gap(self.ahead, "ahead", now, game_gap_ahead)
            self.gap_behind = self.real_gap(self.behind, "behind", now, game_gap_behind)
            calls += self.plans(corners, now)
            calls += self.closing_calls(corners, now)
            calls += self.watch_places(race, moment, corners, now)
            calls += self.follow_passes(moment, corners, now)
            calls += self.defending_held(now)
            self.last_place = me.place
            self.resolve_attempt(me, now)
        if moment.lap_wrapped:
            calls += self.fight_cost(race, now)

        warning = self.not_here_warning(moment, corners, now)
        if warning is not None:
            calls.append(warning)
        self.watch_for_attempt(moment, now)
        return calls

    def plans(self, corners, now):
        calls = []
        # one plan per rival, plus one upgrade once there is a real corner to use: at the
        # start there is no data yet, and "stay close, no lunges" is the right call then
        if self.in_fight(self.ahead, self.gap_ahead, FIGHT_GAP_S, now):
            plan = self.attack_plan(self.ahead, self.gap_ahead, corners, now)
            key = (identity(self.ahead), "attack", "strong_corner" in plan.facts)
            if key not in self.plans_said and self.plan_allowed(now):
                self.last_plan_time = now
                self.plans_said.add(key)
                calls.append(plan)
        if self.in_fight(self.behind, self.gap_behind, DEFEND_GAP_S, now):
            plan = self.defend_plan(self.behind, self.gap_behind, corners, now)
            key = (identity(self.behind), "defend", "his_corner" in plan.facts)
            if key not in self.plans_said and self.plan_allowed(now):
                self.last_plan_time = now
                self.plans_said.add(key)
                calls.append(plan)
        return calls

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
            car = self.behind if side == "behind" else self.ahead
            gap = self.gap_behind if side == "behind" else self.gap_ahead
            said = self.alarmed if side == "behind" else self.closing_called
            if car is None or gap is None:
                continue
            if gap > ALARM_REARM_GAP_S:
                said.discard(car.id)
            if car.id in said or not ALARM_MIN_GAP_S <= gap <= ALARM_MAX_GAP_S:
                continue
            point = self.gap_points.get(side)
            if point is None:
                continue  # the game's gap: the rate is fitted on same-point gaps only
            if car.in_pits or car.pit_state != 0:
                continue  # a car pitting is not a car closing (PITS_AHEAD says it)
            # a car that was already on him (or just passed) and is dropping back is not closing: the
            # 10 s rate still leans on the older, closer gaps. Replays of 23 and 25 Sep (27 Sep):
            # "Closing fast on the car ahead" 2 s after that car passed him, and at 0.19 -> 0.51 s
            closest = self.clock.closest_lately(car.id, now)
            if closest is not None and closest < ALARM_MIN_GAP_S:
                continue
            # closing is judged on the lap once there is a lap of both trails: at Le Mans the gap
            # breathes +-0.5 s inside a lap, and a 10 s rate measures the breathing. Replays of the 7
            # race tapes (27 Sep): 10 of 24 closing calls never came within 0.3 s in a lap (one on a
            # car 2.3 s a lap slower); on the lap pace 5 of 22, with the same 9 of 10 arrivals warned.
            # Crew Chief likewise trends the gap over sectors, and iRacedeck against one lap ago
            rate = self.clock.closing_rate(car.id, now)
            quicker = self.clock.pace_vs_me(
                car.id
            )  # s a lap that car is quicker than me
            if quicker is not None:
                per_lap = quicker if side == "behind" else -quicker
                if per_lap < ALARM_MIN_PACE_S:
                    continue
            elif rate is None or rate < ALARM_MIN_RATE:
                continue
            said.add(car.id)
            facts = {"gap_s": gap}
            if quicker is not None:
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
            if side == "behind":
                words = f"Car behind, {tenths_words(gap)}, closing fast."
                reputation = self.reputation.words(car)
                if reputation is not None:
                    words += f" {reputation}"
                calls.append(
                    self.instant(
                        "CLOSING_ALARM",
                        words,
                        now,
                        facts,
                        seat="spotter",
                        priority=SPOTTER,
                        ttl=ALARM_TTL_S,
                        voice="spotter",
                    )
                )
            else:
                words = (
                    f"Closing fast on the car ahead. {tenths_words(gap).capitalize()}."
                )
                calls.append(self.instant("CLOSING_ON", words, now, facts))
        return calls

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
        calls = []
        order = self.order_around_me(race)
        if self.last_order is not None:
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
        self.last_order = order
        left, right = sides_taken(moment)
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
        gap = self.clock.gap_ahead(car.id, now)
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
        calls = []
        held_moves = []
        stick_now = False
        for car_id in list(self.open_passes):
            pass_ = self.open_passes[car_id]
            gap = self.clock.gap_behind(car_id, now)
            if gap is None and self.behind is not None and self.behind.id == car_id:
                gap = self.gap_behind
            if (
                not pass_["stick_said"]
                and gap is not None
                and gap <= TOW_S
                and moment.corner is None
                and not stick_now
            ):
                nearest = self.next_corner(moment.frame.lap_dist, corners)
                if nearest is not None and nearest[1] >= STICK_WARN_M:
                    pass_["stick_said"] = True
                    stick_now = True  # two cars passed at once: one "stick it"
                    calls.append(
                        self.instant(
                            "STICK_IT",
                            f"Stick it. They're in your tow. Cover the inside into {nearest[0]}.",
                            now,
                            {"corner": nearest[0]},
                        )
                    )
            # live 25 Sep: praise came for a car still 0.1-0.4 s behind for 45 s ("I did not make
            # the overtake completely") and for a place being swapped back ("wrong call"). A pass
            # is done when the car is at least half a second back after a braking zone, or clear.
            held = gap is not None and (
                (pass_["braking_zones_held"] >= 1 and gap >= PASS_DONE_GAP_S)
                or gap >= CLEAR_GAP_S
            )
            if not held:
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

    def praise(self, move, now):
        pool = BRILLIANT if move in BRILLIANT_MOVES else SOLID
        hype = self.pick(pool[self.praise_turn % len(pool)])
        self.praise_turn += 1
        # no "Clear." first (his call, 25 Sep): it doubled the spotter's "Clear.", and the cloned
        # voice said it as "Pit." in 12 of 12 takes
        words = f"{hype} {MOVE_WORDS[move]}"
        if self.ahead is not None and self.gap_ahead is not None:
            words += f" Next one, {tenths_words(self.gap_ahead)}."
        return self.instant(
            "PASS_PRAISE", words, now, {"move": move, "gap_ahead_s": self.gap_ahead}
        )

    def defending_held(self, now):
        """A car on his gearbox for PRESSURE_FOR_S that then fell away: the defence held. Live 27 Sep
        it came 70 s late: the car was within 0.6 s for ~30 s, sat 0.9-1.5 s back for 70 s without
        attacking, and the drift counted as pressure. Only time really on the gearbox counts now."""
        car, gap = self.behind, self.gap_behind
        if car is None or gap is None:
            return []
        if gap <= PRESSURE_GAP_S:
            self.pressure_since.setdefault(car.id, now)
            self.pressure_last[car.id] = now
            return []
        since = self.pressure_since.get(car.id)
        if since is None:
            return []
        last = self.pressure_last[car.id]
        if now - last > PRESSURE_LAPSE_S:
            del self.pressure_since[car.id]  # it drifted back without a fight
            del self.pressure_last[car.id]
            return []
        if gap < FELL_AWAY_GAP_S:
            return []
        del self.pressure_since[car.id]
        del self.pressure_last[car.id]
        if last - since < PRESSURE_FOR_S or car.id in self.held_said:
            return []
        self.held_said.add(car.id)
        return [
            self.instant(
                "DEFEND_HELD",
                self.pick(
                    (
                        "Mega defending, mate. They've got fucking nothing.",
                        "Mega defending, mate. They've got nothing.",
                    )
                ),
                now,
            )
        ]

    def fight_cost(self, race, now):
        """A fight costing a second a lap while another car closes in behind: decide."""
        me = race.me
        if me.best_lap > 0 and (
            self.my_best_lap is None or me.best_lap < self.my_best_lap
        ):
            self.my_best_lap = me.best_lap
        if self.ahead is None or self.my_best_lap is None or me.last_lap <= 0:
            return []
        since = self.fight_since.get(self.ahead.id)
        if (
            since is None
            or now - since < me.last_lap * 0.9
            or self.ahead.id in self.fight_cost_said
        ):
            return []
        lost = me.last_lap - self.my_best_lap
        if lost < FIGHT_COST_S:
            return []
        coming = None
        for opponent in race.opponents:
            if opponent.car_class != me.car_class or opponent.place != me.place + 1:
                continue
            gap = self.clock.gap_behind(opponent.id, now)
            rate = self.clock.closing_rate(opponent.id, now)
            if (
                gap is not None
                and gap <= THIRD_CAR_GAP_S
                and rate is not None
                and rate > 0
            ):
                coming = opponent
        if coming is None:
            return []
        self.fight_cost_said.add(self.ahead.id)
        strong = self.strong_corner_against(self.ahead)
        decide = "Commit or settle."
        if strong is not None:
            decide = f"Go at {strong} this lap or settle in."
        words = f"This fight's costing you {tenths_words(lost)} a lap. Car behind is coming. {decide}"
        return [self.instant("FIGHT_COST", words, now, {"lost_s": round(lost, 1)})]

    # ---- the hasty lunge -------------------------------------------------------------------
    def not_here_warning(self, moment, corners, now):
        """Closing fast on the car ahead into a corner where he is at least as quick: the
        hasty lunge. Warned BEFORE the braking zone, from the voice bank."""
        if (
            self.ahead is None
            or self.gap_ahead is None
            or self.gap_ahead > CLOSE_GAP_S
            or not corners
        ):
            return None
        distance = moment.frame.lap_dist
        upcoming = None
        for corner in sorted(corners, key=lambda c: c["start"]):
            if 0 < corner["start"] - distance <= WARN_BEFORE_M:
                upcoming = corner["name"]
                break
        if upcoming is None:
            return None
        edge = self.edges_against(identity(self.ahead)).get(upcoming)
        if edge is None or edge >= EDGE_WORTH_USING_KMH:
            return None  # no data, or this IS his corner to attack
        key = (upcoming, moment.lap_count)  # once per corner per lap, whoever is ahead
        if key in self.warned:
            return None
        self.warned.add(key)
        return Call(
            seat="racecraft",
            kind="NOT_HERE",
            sim_time=now,
            priority=SPOTTER,
            ttl=2.0,
            conclusion="Not here. Wait for it.",
            template="Not here. Wait for it.",
            urgent=True,
            facts={"corner": upcoming},
        )

    # ---- pass attempts, for team memory ----------------------------------------------------
    def watch_for_attempt(self, moment, now):
        """An attempt = alongside the car ahead, on the brakes, in a corner."""
        if self.open_attempt is not None or self.ahead is None or moment.corner is None:
            return
        frame = moment.frame
        if (
            moment.near is None
            or frame.pos is None
            or frame.ori is None
            or frame.brake < 0.2
        ):
            return
        for car in moment.near.cars:
            if car.id != self.ahead.id:
                continue
            lateral, longitudinal = side_and_overlap(frame.pos, frame.ori, car)
            if (
                abs(longitudinal) < CAR_LENGTH_M
                and LANE_MIN_M <= abs(lateral) <= LANE_MAX_M
            ):
                self.open_attempt = [
                    identity(self.ahead),
                    self.ahead.driver,
                    moment.corner,
                    moment.lap_count,
                    now,
                    self.last_place,
                ]

    def resolve_attempt(self, me, now):
        if self.open_attempt is None:
            return
        place_then = self.open_attempt[5]
        if place_then is not None and me.place < place_then:
            self.finish_attempt("pass")
        elif now - self.open_attempt[4] > ATTEMPT_WINDOW_S:
            self.finish_attempt("no_pass")

    def finish_attempt(self, outcome):
        steam_id, driver, corner, lap, started, place_then = self.open_attempt
        self.attempts.append((steam_id, driver, corner, lap, outcome))
        self.open_attempt = None
