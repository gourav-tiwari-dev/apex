"""Racecraft coach: WHERE to pass, where not to, and keeping your head in a fight.

This is Gourav's weakness seat (23 Sep 2026): in close racing he gets hasty, commits too
early and makes contact, which costs safety rating. The persona is "aggressive but TIMED":
it never says back off, it says WHERE the move works.

The idea every plan rests on, from real racecraft: a faster exit from corner N wins the
pass into the braking zone of the corner after it. So the plan is built from where you are
faster than him (your min speeds this session vs his, from his own telemetry):
    "You're faster out of Ascari. Pass into Parabolica. Not before."

It also counts every pass attempt and how it ended (pass, no pass, contact), so team
memory can measure the hasty-commit habit instead of guessing it.
"""
import statistics

from radio import Call, RACECRAFT, ENGINEER, SPOTTER
from seats.spotter import side_and_overlap, CAR_LENGTH_M, LANE_MIN_M, LANE_MAX_M
from race_state import identity

FIGHT_GAP_S = 1.0             # a same-class car within a second ahead is a fight
DEFEND_GAP_S = 0.8            # and this close behind
EDGE_WORTH_USING_KMH = 3.0    # min-speed advantage that makes a corner yours
CLOSE_GAP_S = 0.4             # this close into a corner where he is faster = about to lunge
WARN_BEFORE_M = 300.0         # "not here" must come before the braking zone, not in it
ATTEMPT_WINDOW_S = 6.0        # how long a pass attempt has to resolve
PLAN_TTL_S = 20.0
# replaying the 23 Sep race with the radio fixed gave 21 plans in 27 minutes: one a minute is
# as much as a driver can use
PLAN_GAP_S = 60.0
RACE_SESSIONS = range(10, 14)
RESET_TTL_S = 15.0


def median_or_none(values):
    if not values:
        return None
    return statistics.median(values)


class Racecraft:
    def __init__(self, performance, rivals=None):
        self.performance = performance     # shares my corner speeds and theirs
        self.rivals = rivals or {}         # steam_id -> team-memory dossier line
        self.plans_said = set()            # (steam_id, "attack" / "defend")
        self.warned = set()                # (steam_id, corner, lap)
        self.ahead = None
        self.gap_ahead = None
        self.behind = None
        self.gap_behind = None
        self.last_place = None
        self.last_plan_time = None
        self.offs = []                     # sim times of my recent off-tracks
        self.attempts = []                 # finished: (steam_id, driver, corner, lap, outcome)
        self.open_attempt = None           # [steam_id, driver, corner, lap, started, place_then]

    # ---- who is around me, same class only -------------------------------------------------
    def neighbours(self, race):
        me = race.me
        ahead = None
        behind = None
        for opponent in race.opponents:
            if opponent.car_class != me.car_class or opponent.laps_behind_leader != me.laps_behind_leader:
                continue
            if opponent.place < me.place and (ahead is None or opponent.place > ahead.place):
                ahead = opponent
            if opponent.place > me.place and (behind is None or opponent.place < behind.place):
                behind = opponent
        gap_ahead = None
        gap_behind = None
        if ahead is not None:
            gap_ahead = round(me.time_behind_leader - ahead.time_behind_leader, 2)
        if behind is not None:
            gap_behind = round(behind.time_behind_leader - me.time_behind_leader, 2)
        return ahead, gap_ahead, behind, gap_behind

    # ---- where each of us is faster --------------------------------------------------------
    def edges_against(self, steam_id):
        """corner -> my typical min speed minus his, where both are measured."""
        his_speeds = {}
        for row_steam_id, driver, car_class, corner, lap, speed in self.performance.opponents.rows:
            if row_steam_id == steam_id:
                his_speeds.setdefault(corner, []).append(speed)
        edges = {}
        for corner, my_speeds in self.performance.my_speeds.items():
            mine = median_or_none(my_speeds)
            his = median_or_none(his_speeds.get(corner, []))
            if mine is not None and his is not None:
                edges[corner] = round(mine - his, 1)
        return edges

    def corner_after(self, corner, corners):
        names = [c["name"] for c in sorted(corners, key=lambda c: c["start"])]
        if corner not in names:
            return None
        return names[(names.index(corner) + 1) % len(names)]

    def history_with(self, steam_id):
        return self.rivals.get(steam_id)

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
            conclusion = f"Stay within 1 second of {opponent.driver} and wait for the mistake. No lunges."
            template = f"{opponent.driver} ahead. Stay close. Wait for the mistake."
        else:
            pass_at = self.corner_after(best, corners)
            facts.update({"strong_corner": best, "edge_kmh": edges[best], "pass_corner": pass_at})
            conclusion = f"Faster out of {best}: pass {opponent.driver} into {pass_at}, not before."
            template = f"You're faster out of {best}. Pass into {pass_at}. Not before."
        if history:
            conclusion += f" History: {history}."
        return Call(seat="racecraft", kind="ATTACK_PLAN", sim_time=now, priority=RACECRAFT,
                    ttl=PLAN_TTL_S, conclusion=conclusion, facts=facts, template=template,
                    evidence={"rival": identity(opponent)})

    def defend_plan(self, opponent, gap, corners, now):
        edges = self.edges_against(identity(opponent))
        facts = {"driver": opponent.driver}
        danger = None
        for corner, edge in edges.items():
            if -edge >= EDGE_WORTH_USING_KMH and (danger is None or edge < edges[danger]):
                danger = corner
        if danger is None:
            conclusion = f"{opponent.driver} behind has nothing on you. Clean lines, no weaving."
            template = f"{opponent.driver} behind. Nothing on you. Clean lines."
        else:
            cover = self.corner_after(danger, corners)
            facts.update({"his_corner": danger, "his_edge_kmh": -edges[danger], "cover_corner": cover})
            conclusion = f"{opponent.driver} is quicker out of {danger}: cover the inside into {cover}."
            template = f"{opponent.driver}'s quicker out of {danger}. Cover the inside into {cover}."
        return Call(seat="racecraft", kind="DEFEND_PLAN", sim_time=now, priority=RACECRAFT,
                    ttl=PLAN_TTL_S, conclusion=conclusion, facts=facts, template=template,
                    evidence={"rival": identity(opponent)})

    def plan_allowed(self, now):
        return self.last_plan_time is None or now - self.last_plan_time >= PLAN_GAP_S

    def reset(self, kind, conclusion, facts, template, now):
        return Call(seat="racecraft", kind=kind, sim_time=now, priority=ENGINEER, ttl=RESET_TTL_S,
                    conclusion=conclusion, facts=facts, template=template)

    # ---- every frame -----------------------------------------------------------------------
    def update(self, moment):
        race = moment.race
        if race is None or race.me is None:
            return []
        # qualifying places change all the time as others set laps: "you lost a place"
        # fired 7 times in quali on 23 Sep. Fights and composure are for races.
        if moment.session_type is not None and moment.session_type not in RACE_SESSIONS:
            return []
        if race.me.in_pits:
            return []
        me = race.me
        now = moment.now
        corners = moment.corners or []
        calls = []

        if moment.new_race:
            self.ahead, self.gap_ahead, self.behind, self.gap_behind = self.neighbours(race)
            # one plan per rival, plus one upgrade once there is a real corner to use: at the
            # start there is no data yet, and "stay close, no lunges" is the right call then
            if self.ahead is not None and self.gap_ahead is not None and self.gap_ahead <= FIGHT_GAP_S:
                plan = self.attack_plan(self.ahead, self.gap_ahead, corners, now)
                key = (identity(self.ahead), "attack", "strong_corner" in plan.facts)
                if key not in self.plans_said and self.plan_allowed(now):
                    self.last_plan_time = now
                    self.plans_said.add(key)
                    calls.append(plan)
            if self.behind is not None and self.gap_behind is not None and self.gap_behind <= DEFEND_GAP_S:
                plan = self.defend_plan(self.behind, self.gap_behind, corners, now)
                key = (identity(self.behind), "defend", "his_corner" in plan.facts)
                if key not in self.plans_said and self.plan_allowed(now):
                    self.last_plan_time = now
                    self.plans_said.add(key)
                    calls.append(plan)
            # composure when a place is lost (E5)
            if self.last_place is not None and me.place > self.last_place:
                calls.append(self.reset("PASSED", "Lost the place. Calm him down: stay within 1 second, the plan still works, no lunge to get it straight back.",
                                        {"stay_within_s": 1}, "Lost it. Stay close. No lunge.", now))
            self.last_place = me.place
            self.resolve_attempt(me, now)

        for event in moment.events:
            if event.kind == "CONTACT":
                calls.append(self.reset("CONTACT_RESET",
                                        f"Contact at {event.corner}. Calm him down. No blame, no driving instruction. Next lap, reset.",
                                        {"corner": event.corner}, "Reset. Next lap.", now))
                if self.open_attempt is not None:
                    self.finish_attempt("contact")
            if event.kind in ("OFF_TRACK", "SPIN"):
                self.offs = [t for t in self.offs if now - t < 240.0] + [now]
                if len(self.offs) == 2:
                    calls.append(self.reset("TWO_OFFS", "Two offs in a short time. Calm him down: two tidy laps, nothing else.",
                                            {}, "Two offs. Two tidy laps now.", now))

        warning = self.not_here_warning(moment, corners, now)
        if warning is not None:
            calls.append(warning)
        self.watch_for_attempt(moment, now)
        return calls

    def not_here_warning(self, moment, corners, now):
        """Closing fast on the car ahead into a corner where he is at least as quick: the
        hasty lunge. Warned BEFORE the braking zone, from the voice bank."""
        if self.ahead is None or self.gap_ahead is None or self.gap_ahead > CLOSE_GAP_S or not corners:
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
            return None                      # no data, or this IS his corner to attack
        key = (identity(self.ahead), upcoming, moment.lap_count)
        if key in self.warned:
            return None
        self.warned.add(key)
        return Call(seat="racecraft", kind="NOT_HERE", sim_time=now, priority=SPOTTER, ttl=2.0,
                    conclusion="Not here. Wait for it.", template="Not here. Wait for it.", urgent=True,
                    facts={"corner": upcoming})

    # ---- pass attempts, for team memory ----------------------------------------------------
    def watch_for_attempt(self, moment, now):
        """An attempt = alongside the car ahead, on the brakes, in a corner."""
        if self.open_attempt is not None or self.ahead is None or moment.corner is None:
            return
        frame = moment.frame
        if moment.near is None or frame.pos is None or frame.ori is None or frame.brake < 0.2:
            return
        for car in moment.near.cars:
            if car.id != self.ahead.id:
                continue
            lateral, longitudinal = side_and_overlap(frame.pos, frame.ori, car)
            if abs(longitudinal) < CAR_LENGTH_M and LANE_MIN_M <= abs(lateral) <= LANE_MAX_M:
                self.open_attempt = [identity(self.ahead), self.ahead.driver, moment.corner,
                                     moment.lap_count, now, self.last_place]

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
