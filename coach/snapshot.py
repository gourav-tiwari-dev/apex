"""The race held still for the coach: what it sees when he asks, and its tools.

A Snapshot is taken the moment he asks a question (the race, his lap, the seats as they are),
so every tool the coach calls answers about the same instant. run_tool carries out the tools
listed in coach/prompt.py: the race picture, the cars around him, the fight maths, the corners,
fuel, the car, his habits, the database."""

import race_tools
from coach.answer_checks import about_the_fight
from coach.fight_maths import (
    CONTACT_LET_BY_QUICKER_S,
    NOT_A_FIGHT_S,
    catch_words,
    my_pace,
    pace_pair,
    pace_words,
    pass_odds,
    race_maths,
    recent_lap,
    road_words,
    team_call,
    trend_words,
)
from race_state import identity, laps_to_go, same_class_neighbours, tyre_averages
from seats.strategist import HOT_TYRE_C, fine_margin
from words import lap_text


OTHER_CLASS_NEAR_M = 400  # an other-class car this close behind is about to arrive


class Snapshot:
    """Built by the race loop the moment he asks. Plain dicts only, so the agent's thread
    reads a still picture while the race moves on."""

    def __init__(
        self,
        race,
        lap,
        lap_dist,
        corners,
        engineer,
        strategist,
        performance,
        racecraft,
        governor,
        habits,
        contacts_this_race,
        db_path=None,
        session_id=None,
        model=None,
    ):
        self.model = model  # the race model: the one picture (25 Sep)
        self.quicker_by = {}  # "ahead" / "behind" -> s a lap that car is quicker than me
        self.corners_map = corners or []
        self.picture = self.race_picture(race, lap, engineer, governor, racecraft)
        self.lap = lap
        self.race = race
        self.db_path = db_path
        self.session_id = session_id
        self.actions = []  # reminders he asked for; the race loop carries them out
        self.standings = race_tools.standings(race)
        self.session = race_tools.session_info(race, self.picture.get("laps_to_go"))
        self.laps = race_tools.lap_history(list(getattr(strategist, "lap_records", [])))
        self.drivers = self.every_driver(
            race, performance, racecraft, contacts_this_race, engineer
        )
        self.habits = habits
        self.corners = self.every_corner(performance)
        self.car_state = self.car(race, strategist)
        self.car_state.update(race_tools.full_car(race, strategist))
        self.ahead_of_me = self.track_ahead(lap_dist, corners, race, racecraft)
        self.picture["other_class_cars_near"] = self.other_class_near(
            race, lap_dist, corners
        )
        # fights exist in races only: in qualifying the other cars are ghosts on the timing sheet, and
        # a "car within a second" made every answer need a DEFEND/ATTACK line (live 25 Sep: "qualifying
        # is fucked up" twice got "No clean answer on that one")
        self.orders = None  # his standing orders (set by the agent before it thinks)
        racing = self.picture.get("session") == "race"
        self.team_calls = {
            side: self.picture[side]["team_call"]
            for side in ("ahead", "behind")
            if racing and self.picture.get(side) and self.picture[side].get("team_call")
        }
        self.contacts_in_fight = 0
        self.history_in_fight = ""
        for side in self.team_calls:
            entry = self.drivers.get(side, {})
            self.contacts_in_fight += entry.get("contacts_with_you_this_race", 0) or 0
            self.history_in_fight += " " + (entry.get("history") or "")

    def other_class_near(self, race, lap_dist, corners):
        """Cars of another class just behind him on the road: the ones about to lap him."""
        lengths = (
            [c["end"] for c in corners or []]
            + [o.lap_dist for o in race.opponents]
            + [lap_dist]
        )
        lap_length = max(lengths) if lengths else 0
        near = []
        for opponent in race.opponents:
            if (
                opponent.car_class == race.me.car_class
                or opponent.in_pits
                or lap_length <= 0
            ):
                continue
            behind_m = (lap_dist - opponent.lap_dist) % lap_length
            if behind_m <= OTHER_CLASS_NEAR_M:
                near.append(
                    {
                        "driver": opponent.driver,
                        "class": opponent.car_class,
                        "metres_behind": round(behind_m),
                    }
                )
        return near

    def override_evidence(self):
        """The override reasons the data actually supports right now."""
        supported = set()
        if self.car_state.get("damage"):
            supported.add("damage")
        if self.contacts_in_fight > 0 or "contact" in self.history_in_fight.lower():
            supported.add("contact")
        if self.picture.get("other_class_cars_near"):
            supported.add("class")
        if any(t > HOT_TYRE_C for t in self.car_state.get("tyre_temps_c", [])):
            supported.add("tyres")
        return supported

    def check_orders(self, call, override):
        """(ok, reason): his standing orders bind the coach. He said fight: no LET BY unless the
        car is damaged or the tyres are gone, and then it is said as his decision."""
        orders = self.orders
        if orders is None:
            return True, "ok"
        if (
            call == "LET BY"
            and orders.get("fight") == "fight"
            and override not in ("damage", "tyres")
        ):
            return False, (
                "he gave a standing order: nobody gets past without a fight. Do not tell him to "
                "let it by; help him fight it cleanly (CALL: DEFEND)"
            )
        return True, "ok"

    def check_call(self, call, override, question=None):
        """(ok, reason): in a fight the call must be the team's, or an override the data backs.
        A question that is not about the cars around him needs no CALL line, fight or not."""
        if not self.team_calls:
            return True, "ok"
        team_words = {text.split(":")[0] for text in self.team_calls.values()}
        if call is None and question is not None and not about_the_fight(question):
            return True, "ok"
        if call is None:
            return (
                False,
                "start with the CALL line (CALL: DEFEND / LET BY / ATTACK / FOLLOW), then the answer",
            )
        if call in team_words and override is None:
            return True, "ok"
        # live 25 Sep: "that Mercedes has hit you once and it's 1.9 s a lap SLOWER, let it go".
        # Never give a place to a slower car; contact alone is not enough - the car must also be
        # quicker, or have hit him at least twice (his rule: no place without a fight unless it is
        # genuinely fast, or his situation is bad).
        if call == "LET BY":
            quicker = self.quicker_by.get("behind")
            if quicker is not None and quicker < 0:
                return False, (
                    f"the car behind is {-quicker:.1f} s a lap SLOWER: never let a slower car by. "
                    "Defend one line, or follow the team call"
                )
            if (
                override == "contact"
                and self.contacts_in_fight < 2
                and (quicker is None or quicker < CONTACT_LET_BY_QUICKER_S)
            ):
                return False, (
                    "one contact is not a reason to give the place to a car that is not clearly quicker: "
                    "defend, give it room, one line"
                )
        supported = self.override_evidence()
        if override in supported:
            return True, "ok"
        if not supported:
            return False, (
                f"the team call is {' / '.join(sorted(team_words))} and the data supports no "
                "override (no damage, no contact history, no other class near, tyres fine): follow it"
            )
        return False, (
            f"the team call is {' / '.join(sorted(team_words))}; an override needs a reason the "
            f"data supports, and it supports only: {', '.join(sorted(supported))}"
        )

    def race_picture(self, race, lap, engineer, governor, racecraft=None):
        me = race.me
        session = race.session
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race, self.model)
        picture = {
            "session": {10: "race", 11: "race", 12: "race", 13: "race"}.get(
                session.session, "practice or qualifying"
            ),
            "lap": lap,
            "place": me.place,
            "laps_to_go": engineer.to_go_at_line
            if engineer.to_go_at_line is not None
            else laps_to_go(race, me.last_lap if me.last_lap > 0 else None, self.model),
            "my_last_lap": lap_text(me.last_lap),
            "my_best_lap": lap_text(me.best_lap),
            "time_left_s": round(session.time_remaining)
            if session.time_remaining > 0
            else None,
            "quiet_until_lap": governor.quiet_until_lap,
        }
        for side, car, gap in (
            ("ahead", ahead, gap_ahead),
            ("behind", behind, gap_behind),
        ):
            if car is None or gap is None:
                picture[side] = None
                continue
            entry = {"driver": car.driver, "gap_s": round(gap, 1)}
            before = engineer.gaps_at_line.get(side)
            if self.model is not None:
                # one number for one thing (25 Sep): the race model's road trend, not the
                # line-to-line game gap, which said "steady" while the road said "growing 0.6"
                front, back = (car.id, "me") if side == "ahead" else ("me", car.id)
                entry["gap_trend"] = self.trend_words(front, back)
            elif before is not None and before[1] is not None:
                entry["gap_trend"] = trend_words(side, before[1] - gap)
            theirs, source = recent_lap(car)
            mine = my_pace(me, engineer)
            if theirs is not None:
                entry["their_lap"] = f"{lap_text(theirs)} ({source})"
            theirs, mine, measured, how = pace_pair(
                car, theirs, source, mine, engineer, racecraft, self.model
            )
            if theirs is not None and mine is not None:
                self.quicker_by[side] = round(
                    mine - theirs, 2
                )  # + = that car is quicker than me
            if theirs is not None and mine is not None:
                entry["their_pace"] = f"{pace_words(theirs, mine)} ({how})"
                entry["race_maths"] = race_maths(
                    side, round(gap, 1), theirs, mine, picture["laps_to_go"]
                )
            else:
                entry["their_pace"] = (
                    "not known yet: no lap measured on the road or posted. Say so, never guess it."
                )
            call = team_call(
                side, round(gap, 1), theirs, mine, picture["laps_to_go"], measured
            )
            if (
                side == "behind"
                and theirs is not None
                and mine is not None
                and gap < 1.5
            ):
                words, share = pass_odds(mine - theirs)
                entry["pass_odds_from_his_races"] = (
                    f"in fights on his tapes, cars {words} got past within a lap "
                    f"{round(share * 100)}% of the time"
                )
            if call is not None:
                entry["team_call"] = call
            elif side == "behind":
                # 25 Sep bank run: a car 1.7 s back, 0.7 s a lap quicker, got "don't fight it,
                # let it go" five times. The team's rule for when it arrives is decided now.
                entry["team_call_when_it_reaches_you"] = team_call(
                    side, NOT_A_FIGHT_S / 2, theirs, mine, picture["laps_to_go"]
                )
            if gap >= NOT_A_FIGHT_S:
                entry["fight"] = (
                    f"not a fight yet: {round(gap, 1)} s is more than {NOT_A_FIGHT_S:g} s"
                )
            else:
                entry["fight"] = (
                    f"IN A FIGHT NOW: {round(gap, 1)} s, within {NOT_A_FIGHT_S:g} s"
                )
            picture[side] = entry
        if self.model is not None:
            self.picture_laps_to_go = picture.get("laps_to_go")
            picture["field_around_you"] = self.field(race)
            picture["battles_near_you"] = self.battles_near(race)
            pitted = [
                f"P{self.model.car(key).place}"
                for _, key in self.model.pitting_near()
                if self.model.car(key) is not None
            ]
            if pitted:
                picture["just_pitted_near_you"] = pitted
        return picture

    def trend_words(self, front, back):
        t = self.model.trend(front, back)
        if t is None:
            return "not measured yet"
        amount = abs(t["closing_per_lap"])
        sure = "sure, 2 laps" if t["sure"] else "1 lap only, NOT sure"
        if amount < 0.1:
            return f"gap steady ({sure})"
        who = (
            "the car behind is catching"
            if t["closing_per_lap"] > 0
            else "the gap is growing"
        )
        return f"{who} {amount:.1f} s a lap ({sure})"

    def field(self, race):
        """The same-class cars 3 places either side, as the race model sees them on the road."""
        me = race.me
        rows = []
        for o in sorted(race.opponents, key=lambda o: o.place):
            if o.car_class != me.car_class or abs(o.place - me.place) > 3 or o.in_pits:
                continue
            ahead = o.place < me.place
            gap = self.model.gap(o.id, "me") if ahead else self.model.gap("me", o.id)
            row = {
                "place": o.place,
                "side": "ahead" if ahead else "behind",
                "gap_s": gap,
                "car": o.car_model or o.car_name,
            }
            to_go = self.picture_laps_to_go
            if ahead:
                row["trend"] = self.trend_words(o.id, "me").replace(
                    "the car behind is", "you are"
                )
                catch = self.model.catch("me", o.id)
                if catch is not None:
                    row["you_catch_it"] = catch_words(catch[1], to_go)
            else:
                row["trend"] = self.trend_words("me", o.id)
                catch = self.model.catch(o.id, "me")
                if catch is not None:
                    row["it_catches_you"] = catch_words(catch[1], to_go)
            rows.append(row)
        return rows

    def battles_near(self, race):
        places = {o.id: o.place for o in race.opponents}
        places["me"] = race.me.place
        out = []
        for front, back, gap in self.model.battles():
            if (
                front in places
                and back in places
                and min(
                    abs(places[front] - race.me.place),
                    abs(places[back] - race.me.place),
                )
                <= 4
            ):
                who = [("you" if k == "me" else f"P{places[k]}") for k in (front, back)]
                out.append(f"{who[0]} and {who[1]}, {gap} s apart")
        return out[:5]

    def every_driver(self, race, performance, racecraft, contacts_this_race, engineer):
        me = race.me
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race, self.model)
        drivers = {}
        for opponent in race.opponents:
            if opponent.car_class != me.car_class:
                continue
            entry = {
                "driver": opponent.driver,
                "place": opponent.place,
                "car": opponent.car_model or opponent.car_name,
                "last_lap": lap_text(opponent.last_lap),
                "best_lap": lap_text(opponent.best_lap),
                "in_pits": opponent.in_pits,
                "where": road_words(me, opponent),
            }
            theirs, source = recent_lap(opponent)
            mine = my_pace(me, engineer)
            theirs, mine, _, how = pace_pair(
                opponent, theirs, source, mine, engineer, racecraft, self.model
            )
            if theirs is not None and mine is not None:
                entry["their_pace"] = f"{pace_words(theirs, mine)} ({how})"
            else:
                entry["their_pace"] = "not known: no lap time posted yet"
            key = identity(opponent)
            edges = racecraft.edges_against(key)
            # named for what they measure: speed carried through the middle, NOT braking (24 Sep:
            # "corners_they_are_quicker" came back as "quicker in every braking zone")
            if self.model is None or not self.corners_map:
                entry["corners_where_they_carry_more_speed_mid_corner"] = sorted(
                    c for c, edge in edges.items() if edge <= -3.0
                )
                entry["corners_where_you_carry_more_speed_mid_corner"] = sorted(
                    c for c, edge in edges.items() if edge >= 3.0
                )
            entry["contacts_with_you_this_race"] = contacts_this_race.get(key, 0)
            entry["pass_attempts_this_race"] = [
                a[4] for a in racecraft.attempts if a[0] == key
            ]
            entry["history"] = racecraft.rivals.get(key)
            if self.model is not None and self.corners_map:
                # seconds THEY gain on you through each corner, from the road (race model)
                gains = self.model.corner_gains(opponent.id, "me", self.corners_map)
                entry["corners_where_they_gain_time_s"] = {
                    c: g for c, g in gains.items() if g >= 0.1
                }
                entry["corners_where_you_gain_time_s"] = {
                    c: -g for c, g in gains.items() if g <= -0.1
                }
            drivers[opponent.driver.lower()] = entry
            if opponent is ahead:
                drivers["ahead"] = entry
            if opponent is behind:
                drivers["behind"] = entry
        return drivers

    def every_corner(self, performance):
        corners = {}
        rival = {item[1]: item for item in performance.rival_gaps()}
        for corner, passes in performance.my_passes.items():
            timed = [p.time_s for p in passes if p.time_s is not None]
            entry = {"corner": corner, "laps_measured": len(timed)}
            if timed:
                entry["my_best_s"] = round(min(timed), 2)
                entry["my_last_s"] = round(timed[-1], 2)
            measured = performance.balance.corner_balance(corner)
            if measured is not None:
                from balance import describe

                entry["balance"] = describe(measured)
            if corner in rival:
                gap, _, fastest, (change, lengths) = rival[corner]
                from seats.performance import advice_text

                entry["fastest_car"] = fastest["driver"]
                entry["fastest_gains_s"] = gap
                entry["what_to_change"] = advice_text(change, lengths)
            corners[corner.lower()] = entry
        return corners

    def car(self, race, strategist):
        me = race.me
        state = {
            "fuel": strategist.fuel_now,
            "tyre_temps_c": tyre_averages(me),
            "damage": sum(me.dents) > 0,
            "tyres_overheating": any(t > HOT_TYRE_C for t in tyre_averages(me))
            or bool(me.overheating),
            "track_limit_steps": me.track_limit_steps,
            "penalty_at_steps": race.session.limit_steps_per_penalty,
            "penalties": me.penalties,
        }
        return state

    def track_ahead(self, lap_dist, corners, race, racecraft):
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race, self.model)
        if not corners:
            return []
        ordered = sorted(corners, key=lambda c: c["start"])
        lap_length = max(c["end"] for c in ordered)
        upcoming = []
        for corner in ordered:
            distance = corner["start"] - lap_dist
            if distance < 0:
                distance += lap_length
            upcoming.append((distance, corner["name"]))
        upcoming.sort()
        result = []
        for distance, name in upcoming[:4]:
            entry = {"corner": name, "metres_away": round(distance)}
            for side, car in (("car_ahead", ahead), ("car_behind", behind)):
                if car is None:
                    continue
                edge = racecraft.edges_against(identity(car)).get(name)
                if edge is not None and abs(edge) >= 3.0:
                    entry[side] = (
                        "you are quicker here" if edge > 0 else "they are quicker here"
                    )
            result.append(entry)
        return result

    def driver_names(self):
        return [key for key in self.drivers if key not in ("ahead", "behind")]

    def strategy(self):
        """The plan, in order, from code's numbers: the model explains it, never re-derives it."""
        plan = []
        fuel = self.car_state.get("fuel_at_the_flag")
        if isinstance(fuel, dict):
            spare, what = fuel["spare_laps"], fuel["limit"]
            if spare < 0:
                plan.append(
                    f"SAVE {what}: {-spare} laps short at the flag. Lift and coast before the longest "
                    "braking zones until it is back above zero."
                )
            elif spare < fine_margin(
                fuel.get("laps_left") or 99.0
            ):  # the strategist's own line
                plan.append(f"{what} is tight: {spare} laps spare. No wasted laps.")
            else:
                plan.append(f"{what} is no limit: {spare} laps spare. Push.")
        else:
            plan.append(
                "fuel to the flag: not known yet (needs 2 laps measured at the line)"
            )
        for side in ("ahead", "behind"):
            car = self.picture.get(side)
            if not car:
                continue
            maths = car.get("race_maths", {})
            reach = maths.get("at_this_pace", "")
            if car.get("team_call"):
                plan.append(f"car {side}, {car['gap_s']} s: {car['team_call']}")
            elif (
                side == "ahead" and "before the flag" in reach and "reach them" in reach
            ):
                plan.append(f"PUSH: the car ahead, {car['gap_s']} s up, {reach}.")
            elif (
                side == "behind" and "before the flag" in reach and "reach you" in reach
            ):
                plan.append(
                    f"DEFEND LATER: the car behind, {car['gap_s']} s back, {reach}. "
                    f"Keep it behind with a {maths.get('to_keep_them_behind', 'quicker')} lap."
                )
            else:
                plan.append(
                    f"car {side}, {car['gap_s']} s: {reach or car.get('their_pace', 'pace not known')}"
                )
        if self.car_state.get("tyres_overheating"):
            plan.append(
                f"TYRES are cooking (over {HOT_TYRE_C} C): smoother, less sliding, or the pace goes."
            )
        losing = sorted(
            (entry for entry in self.corners.values() if entry.get("fastest_gains_s")),
            key=lambda entry: -entry["fastest_gains_s"],
        )[:2]
        where = [
            f"{entry['corner']}: the fastest car gains {entry['fastest_gains_s']} s. {entry.get('what_to_change', '')}".strip()
            for entry in losing
        ]
        return {
            "laps_to_go": self.picture.get("laps_to_go"),
            "place": self.picture.get("place"),
            "plan_in_order": plan,
            "where_the_time_is": where or ["not measured yet"],
            "his_habits": (self.habits or [])[:2],
        }

    def remind(self, arguments):
        try:
            lap = int(arguments.get("lap"))
        except (TypeError, ValueError):
            return {"error": "lap must be a lap number"}
        what = str(arguments.get("what", "")).strip()
        if lap <= self.lap or not what:
            return {
                "error": f"the reminder needs a lap after this one (this is lap {self.lap}) and words"
            }
        to_go = self.picture.get("laps_to_go")
        if to_go and lap > self.lap + to_go - 1:
            # 25 Sep bank run: "reminder set for lap 10" in a race that ends on lap 6
            return {
                "error": f"the race ends on lap {self.lap + to_go - 1}: there is no lap {lap}"
            }
        self.actions.append({"remind_lap": lap, "what": what[:80]})
        return {"ok": f"reminder set for lap {lap}: {what[:80]}"}

    def run_tool(self, name, arguments):
        if name == "race_picture":
            return self.picture
        if name == "standings":
            return self.standings
        if name == "session":
            return self.session
        if name == "lap_history":
            return self.laps
        if name == "strategy":
            return self.strategy()
        if name == "race_events":
            events = race_tools.race_events(self.db_path, self.session_id)
            # the other car in a contact, as its place now (never a name): "who hit me?"
            from race_state import identity

            places = {identity(o): f"P{o.place}" for o in self.race.opponents}
            for contact in events.get("contacts", []):
                if contact.get("other_car"):
                    contact["other_car"] = places.get(
                        contact["other_car"], "a car no longer in the race"
                    )
            return events
        if name == "setup":
            return race_tools.setup_advice(self.db_path, self.session_id, self.race)
        if name == "knowledge":
            return race_tools.knowledge(arguments.get("topic", ""))
        if name == "calculator":
            return race_tools.calculate(arguments.get("expression", ""))
        if name == "remind_me":
            return self.remind(arguments)
        if name == "database":
            return race_tools.query_db(self.db_path, arguments.get("sql", ""))
        if name == "my_habits":
            return self.habits or ["no measured habits yet"]
        if name == "car":
            return self.car_state
        if name == "track_ahead":
            return self.ahead_of_me
        if name == "driver":
            who = str(arguments.get("who", "")).lower().strip()
            if who in self.drivers:
                return self.drivers[who]
            for key, entry in self.drivers.items():
                if who and (who in key or key in who):
                    return entry
            return {
                "error": f"no driver '{who}' in your class; ask for 'ahead', 'behind' or a name from race_picture"
            }
        if name == "corner":
            wanted = str(arguments.get("name", "")).lower().strip()
            for key, entry in self.corners.items():
                if wanted and (wanted in key or key in wanted):
                    return entry
            # a misheard name ("four chickens" for Ford Chicanes, 24 Sep) is still that corner
            import difflib

            close = difflib.get_close_matches(
                wanted, list(self.corners), n=1, cutoff=0.55
            )
            if close:
                return dict(self.corners[close[0]], heard_as=wanted)
            return {
                "error": f"no data for corner '{wanted}'",
                "known": sorted(self.corners),
            }
        return {"error": f"no tool {name}"}
