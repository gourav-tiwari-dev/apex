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

from radio.calls import Call, RACECRAFT, ENGINEER, SPOTTER
from seats.spotter import (
    side_and_overlap,
    CAR_LENGTH_M,
    LANE_MIN_M,
    LANE_MAX_M,
)
from radio.words import Rotation, tenths_words
from game.race_snapshot import identity
from race.facts import same_class_neighbours
from race.gaps import TrackClock
from game.constants import GREEN_FLAG, RACE_SESSIONS
from seats.reputation import Reputation
from seats.passes import PassLifecycle
from seats.closing_alarm import ClosingAlarm

FIGHT_GAP_S = 1.0  # a same-class car within a second ahead is a fight
DEFEND_GAP_S = 0.8  # and this close behind
FIGHT_CONFIRM_S = 8.0  # ...for this long: a car brushing past is not a fight
EDGE_WORTH_USING_KMH = 3.0  # min-speed advantage that makes a corner yours
# ...or, on the road, the seconds I gain through it (race model, 25 Sep)
GAIN_WORTH_USING_S = 0.15
INCIDENT_KMH = 40.0  # slower than this on track: he's spun or crashed
INCIDENT_QUIET_S = 20.0  # ...and racecraft stays quiet this long after it
CLOSE_GAP_S = 0.4  # this close into a corner where he is faster = about to lunge
WARN_BEFORE_M = 300.0  # "not here" must come before the braking zone, not in it
ATTEMPT_WINDOW_S = 6.0  # how long a pass attempt has to resolve
PLAN_TTL_S = 20.0
# replaying the 23 Sep race with the radio fixed gave 21 plans in 27 minutes: one a minute is
# as much as a driver can use
PLAN_GAP_S = 60.0
RESET_TTL_S = 15.0
TWO_OFFS_WINDOW_S = 240.0  # two offs this close together: two tidy laps


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
# a lap in a fight: the fight lasted this share of it, so the lap time is the fight's
FIGHT_MOST_OF_LAP = 0.9


def median_or_none(values):
    if not values:
        return None
    return statistics.median(values)


class Racecraft(PassLifecycle, ClosingAlarm):
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
        self.praise_lines = Rotation(clean)  # both pools share one turn
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
    def attack_plan(self, opponent, corners, now):
        edges = self.edges_against(identity(opponent))
        # a plan is where, not how far: no gap in it
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

    def defend_plan(self, opponent, corners, now):
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
            gap = self.clock.gap_ahead(car.id)
        else:
            gap = self.clock.gap_behind(car.id)
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
        """Everything racecraft says this frame: after incidents, then (on each new race
        snapshot) plans, closing cars, places won and lost, passes, and at the line the cost
        of a fight."""
        race = moment.race
        now = moment.now
        self.see(moment)
        if race is None or race.me is None:
            return []
        # qualifying places change all the time as others set laps: "you lost a place"
        # fired 7 times in quali on 23 Sep. Fights and composure are for races.
        if moment.session_type is not None and moment.session_type not in RACE_SESSIONS:
            return []
        if race.me.in_pits or race.session.game_phase != GREEN_FLAG:
            return []
        if self.quiet_after_incident(moment, now):
            return []
        corners = moment.corners or []
        self.corners = corners
        calls = self.after_incidents(moment.events, now)
        self.count_braking_zones(moment, now)
        if moment.new_race:
            calls += self.each_snapshot(race, moment, corners, now)
        if moment.lap_wrapped:
            calls += self.fight_cost(race, now)
        warning = self.not_here_warning(moment, corners, now)
        if warning is not None:
            calls.append(warning)
        self.watch_for_attempt(moment, now)
        return calls

    def see(self, moment):
        """Feed my own clock (only when the race model is not shared) and the reputations."""
        race = moment.race
        now = moment.now
        if moment.new_race and race is not None and race.me is not None:
            if self.model is None:
                self.clock.see_race(race, now)
            self.reputation.see_race(race)
        if self.model is None:
            self.clock.see_me(moment.frame.lap_dist, now)

    def quiet_after_incident(self, moment, now):
        """True while quiet: after a spin, or crawling, no racecraft (live 25 Sep, spun at
        Indianapolis, it kept saying "mega defending" and "stay in the tow")."""
        if (
            any(event.kind == "SPIN" for event in moment.events)
            or moment.frame.speed_kmh < INCIDENT_KMH
        ):
            self.quiet_until = now + INCIDENT_QUIET_S
        return self.quiet_until is not None and now < self.quiet_until

    def after_incidents(self, events, now):
        """Contact: note who hit him, calm him down, and no praise for a pass it touched. Two
        offs close together: two tidy laps."""
        calls = []
        for event in events:
            if event.kind == "CONTACT":
                calls.append(self.after_contact(event, now))
            if event.kind in ("OFF_TRACK", "SPIN"):
                two_offs = self.after_off(now)
                if two_offs is not None:
                    calls.append(two_offs)
        return calls

    def after_contact(self, event, now):
        """Note who hit him and calm him down; the pass attempt ends, and a pass the contact
        touched is not praised."""
        who = event.other_car
        if who is not None:
            self.reputation.hits_on_me[who] = self.reputation.hits_on_me.get(who, 0) + 1
        call = self.reset(
            "CONTACT_RESET",
            f"Contact at {event.corner}. Calm him down. No blame, no driving instruction. Next lap, reset.",
            {"corner": event.corner},
            "Reset. Next lap.",
            now,
        )
        if self.open_attempt is not None:
            self.finish_attempt("contact")
        for pass_ in self.open_passes.values():
            pass_["contact"] = True
        return call

    def after_off(self, now):
        """An off or a spin: when it is the second inside TWO_OFFS_WINDOW_S, two tidy laps."""
        self.offs = [t for t in self.offs if now - t < TWO_OFFS_WINDOW_S] + [now]
        if len(self.offs) != 2:
            return None
        return self.reset(
            "TWO_OFFS",
            "Two offs in a short time. Calm him down: two tidy laps, nothing else.",
            {},
            "Two offs. Two tidy laps now.",
            now,
        )

    def count_braking_zones(self, moment, now):
        # a braking zone survived = a corner left after the one the pass was made in
        if self.last_corner is not None and moment.corner != self.last_corner:
            self.last_corner_exit = now
            for pass_ in self.open_passes.values():
                if pass_["corner"] is None or pass_["left_pass_corner"]:
                    pass_["braking_zones_held"] += 1
                pass_["left_pass_corner"] = True
        self.last_corner = moment.corner

    def each_snapshot(self, race, moment, corners, now):
        """On each new race snapshot: who is around him and how far, then every call that
        needs the whole field."""
        self.cars = {opponent.id: opponent for opponent in race.opponents}
        self.ahead, game_gap_ahead, self.behind, game_gap_behind = (
            same_class_neighbours(race, self.model)
        )
        self.gap_ahead = self.real_gap(self.ahead, "ahead", now, game_gap_ahead)
        self.gap_behind = self.real_gap(self.behind, "behind", now, game_gap_behind)
        calls = []
        calls += self.plans(corners, now)
        calls += self.closing_calls(corners, now)
        calls += self.watch_places(race, moment, corners, now)
        calls += self.follow_passes(moment, corners, now)
        calls += self.defending_held(now)
        self.last_place = race.me.place
        self.resolve_attempt(race.me, now)
        return calls

    def plans(self, corners, now):
        calls = []
        # one plan per rival, plus one upgrade once there is a real corner to use: at the
        # start there is no data yet, and "stay close, no lunges" is the right call then
        if self.in_fight(self.ahead, self.gap_ahead, FIGHT_GAP_S, now):
            plan = self.attack_plan(self.ahead, corners, now)
            key = (identity(self.ahead), "attack", "strong_corner" in plan.facts)
            if key not in self.plans_said and self.plan_allowed(now):
                self.last_plan_time = now
                self.plans_said.add(key)
                calls.append(plan)
        if self.in_fight(self.behind, self.gap_behind, DEFEND_GAP_S, now):
            plan = self.defend_plan(self.behind, corners, now)
            key = (identity(self.behind), "defend", "his_corner" in plan.facts)
            if key not in self.plans_said and self.plan_allowed(now):
                self.last_plan_time = now
                self.plans_said.add(key)
                calls.append(plan)
        return calls

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
            or now - since < me.last_lap * FIGHT_MOST_OF_LAP
            or self.ahead.id in self.fight_cost_said
        ):
            return []
        lost = me.last_lap - self.my_best_lap
        if lost < FIGHT_COST_S:
            return []
        if self.third_car_coming(race, now) is None:
            return []
        self.fight_cost_said.add(self.ahead.id)
        strong = self.strong_corner_against(self.ahead)
        decide = "Commit or settle."
        if strong is not None:
            decide = f"Go at {strong} this lap or settle in."
        words = f"This fight's costing you {tenths_words(lost)} a lap. Car behind is coming. {decide}"
        return [self.instant("FIGHT_COST", words, now, {"lost_s": round(lost, 1)})]

    def third_car_coming(self, race, now):
        """The car one place behind me in my class, when it is within THIRD_CAR_GAP_S and
        closing; else None."""
        me = race.me
        coming = None
        for opponent in race.opponents:
            if opponent.car_class != me.car_class or opponent.place != me.place + 1:
                continue
            gap = self.clock.gap_behind(opponent.id)
            rate = self.clock.closing_rate(opponent.id, now)
            if (
                gap is not None
                and gap <= THIRD_CAR_GAP_S
                and rate is not None
                and rate > 0
            ):
                coming = opponent
        return coming

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
