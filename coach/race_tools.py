"""The push-to-talk agent's wider tools (v3 step 5b, 25 Sep 2026): "push-to-talk is the last
resort, it must answer anything". The first agent had 6 narrow tools; a driver asks about
standings, the car, the weather, his laps, what happened, the rules, the plan.

Everything here is read-only. The race-derived tools are computed once, on the main thread,
when he asks (the Snapshot); the database tools open their own read-only connection, so the
race loop's connection is never touched from the agent's thread.

Other drivers are never named: positions, classes and car models only (his rule, 24 Sep).
"""

import ast
import math
import operator
import os
import re
import sqlite3
import statistics
from game.constants import BLUE_FLAG, QUALIFYING_SESSIONS, RACE_SESSIONS, SECTOR_YELLOW
from radio.words import lap_text
from game.constants import WHEEL_NAMES
from game.race_snapshot import NO_TYRE_READING_C, tyre_averages

PHASES = {
    0: "before the session",
    1: "reconnaissance",
    2: "grid walk",
    3: "formation lap",
    4: "countdown",
    5: "green",
    6: "full course yellow or safety car",
    7: "session stopped",
    8: "session over",
}
GRIP = {
    0: "green track",
    1: "low rubber",
    2: "medium rubber",
    3: "high rubber",
    4: "saturated rubber",
}
SESSIONS = {0: "test day", 9: "warmup"}
MAX_SQL_ROWS = 30
SQL_STEPS_LIMIT = 2_000_000  # sqlite VM steps before a query is cut off (~0.1-0.3 s)
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the project folder
KNOWLEDGE_FILE = os.path.join(HERE, "knowledge.md")


def session_name(number):
    if 1 <= number <= 4:
        return "practice"
    if 5 <= number <= 8:
        return "qualifying"
    if 10 <= number <= 13:
        return "race"
    return SESSIONS.get(number, "unknown session")


def wheel_values(values, digits=0):
    if not values or len(values) != 4:
        return None
    return {
        wheel: round(value, digits) if digits else round(value)
        for wheel, value in zip(WHEEL_NAMES, values)
    }


# ---- computed from the race when he asks --------------------------------------------------
def standings(race):
    """Every car, in order: position, class, car, gap. His own row says "you"."""
    me = race.me
    rows = [(me.place, None)] + [(car.place, car) for car in race.opponents]
    rows.sort(key=lambda row: row[0])
    class_places = {}
    table = []
    for place, car in rows:
        car_class = me.car_class if car is None else car.car_class
        class_places[car_class] = class_places.get(car_class, 0) + 1
        who = me if car is None else car
        entry = {
            "place": place,
            "class": car_class,
            "class_place": class_places[car_class],
            "best_lap": lap_text(who.best_lap),
            "last_lap": lap_text(who.last_lap),
            "pitstops": who.pitstops,
        }
        if car is None:
            entry["you"] = True
        else:
            entry["car"] = car.car_model or car.car_name
            if car.in_pits:
                entry["in_pits"] = True
        if who.laps_behind_leader > 0:
            entry["laps_down"] = who.laps_behind_leader
        else:
            entry["gap_to_leader_s"] = round(who.time_behind_leader, 1)
        table.append(entry)
    mine = next(entry for entry in table if entry.get("you"))
    return {
        "your_place_overall": mine["place"],
        "your_place_in_class": mine["class_place"],
        "cars_in_your_class": class_places[me.car_class],
        "cars": table,
    }


def full_car(race, strategist):
    me = race.me
    temps = tyre_averages(me)
    state = {
        "fuel_litres": round(me.fuel, 1),
        "fuel_capacity_litres": round(me.fuel_capacity, 1),
        "fuel_at_the_flag": strategist.fuel_now
        or "not known yet: needs 2 laps measured at the line",
        "virtual_energy_percent": round(me.virtual_energy * 100)
        if me.virtual_energy > 0
        else None,
        "battery_percent": round(me.battery * 100) if me.battery > 0 else None,
        "tyre_compound": me.compound or None,
        "tyre_temps_by_wheel_c": wheel_values(temps) if len(temps) == 4 else None,
        "tyre_temps_inner_centre_outer_c": {
            wheel: [round(t) for t in z]
            for wheel, z in zip(WHEEL_NAMES, me.tyre_temps)
            if z and min(z) > NO_TYRE_READING_C
        }
        or None,
        "tyre_pressures_kpa": wheel_values(me.tyre_pressures),
        "tyre_wear_percent_left": wheel_values([w * 100 for w in me.tyre_wear])
        if me.tyre_wear
        else None,
        "brake_temps_c": wheel_values(me.brake_temps),
        "damage": sum(me.dents) > 0,
        "damage_zones_hit": sum(1 for d in me.dents if d) or None,
        "parts_detached": me.detached or None,
        "engine_overheating": me.overheating or None,
        "brake_bias_rear_percent": round(me.brake_bias_rear * 100, 1)
        if me.brake_bias_rear
        else None,
        "traction_control": me.tc,
        "abs": me.abs,
        "motor_map": me.motor_map,
        "anti_roll_bar_front": me.arb_front,
        "anti_roll_bar_rear": me.arb_rear,
        "track_limit_steps": me.track_limit_steps,
        "penalty_at_steps": race.session.limit_steps_per_penalty,
        "penalties": me.penalties,
        "pitstops": me.pitstops,
    }
    return state


def session_info(race, laps_to_go):
    session = race.session
    info = {
        "track": session.track,
        "session": session_name(session.session),
        "phase": PHASES.get(session.game_phase, f"phase {session.game_phase}"),
        "time_left": lap_text(session.time_remaining)
        if session.time_remaining > 0
        else None,
        "laps_to_go": laps_to_go,
        "air_temp_c": round(session.ambient_temp),
        "track_temp_c": round(session.track_temp),
        "raining": "no"
        if session.raining < 0.05
        else f"yes, {round(session.raining * 100)} percent",
        "track_wetness_percent": round(session.wetness * 100),
        "grip": GRIP.get(session.grip_level),
        "fixed_setup": session.fixed_setup,
        "yellow_anywhere": session.yellow_flag_state not in (0, -1)
        or any(f == SECTOR_YELLOW for f in session.sector_flags),
        "his_flag": "blue" if race.me.flag == BLUE_FLAG else "none",
    }
    if session.max_laps and session.max_laps < 10000:
        info["race_laps"] = session.max_laps
    if session.session in QUALIFYING_SESSIONS:
        info["qualifying"] = qualifying_picture(race)
    if session.session in RACE_SESSIONS and race.me.grid > 0:
        info["his_grid_slot"] = race.me.grid
    return info


def qualifying_picture(race):
    """Where qualifying stands for him: his time or none, the class order, the grid slot it
    gives, and whether the clock allows another run. Live 25 Sep he crashed on his push lap,
    said "qualifying is fucked up", and the coach had nothing to answer with."""
    me, session = race.me, race.session
    rivals = sorted(
        o.best_lap
        for o in race.opponents
        if o.car_class == me.car_class and o.best_lap > 0
    )
    mine = me.best_lap if me.best_lap > 0 else None
    picture = {
        "his_best": lap_text(mine) if mine else "no time set",
        "class_cars_with_a_time": len(rivals),
        "class_cars": 1 + sum(1 for o in race.opponents if o.car_class == me.car_class),
        "pole": lap_text(rivals[0]) if rivals else None,
    }
    if mine:
        position = 1 + sum(1 for t in rivals if t < mine)
        picture["his_class_position"] = position
        if rivals and position > 1:
            picture["off_pole_s"] = round(mine - rivals[0], 2)
    else:
        picture["his_class_position"] = (
            f"none: without a time he starts behind every car that set one (class P{len(rivals) + 1} or lower)"
        )
    lap = mine or (rivals[len(rivals) // 2] if rivals else None)
    if lap and session.time_remaining > 0:
        # a lap started before the clock runs out still counts; a run from the pits is an out lap + a flying lap
        picture["time_for_another_run"] = (
            "yes, an out lap and a flying lap fit"
            if session.time_remaining > 2 * lap
            else "only if he is already on track: a lap started before the clock ends still counts"
            if session.time_remaining > 0
            else "no"
        )
    elif session.time_remaining <= 0:
        picture["time_for_another_run"] = "no: the clock has run out"
    if len(rivals) >= 3:
        picture["class_times_spread"] = (
            f"{lap_text(rivals[0])} to {lap_text(rivals[-1])}"
        )
    return picture


def lap_history(records):
    """His laps this session, timed at the line by Apex, with sectors where all three were seen."""
    if not records:
        return {"laps": [], "note": "no full lap timed yet this session"}
    laps = []
    for record in records[-10:]:
        entry = {
            "lap": record["lap"],
            "time": lap_text(record["time_s"]),
            "valid": record["valid"],
        }
        if record.get("sectors_s"):
            entry["sectors_s"] = record["sectors_s"]
        if record.get("fuel_used"):
            entry["fuel_used_litres"] = record["fuel_used"]
        laps.append(entry)
    times = [r["time_s"] for r in records]
    summary = {"laps": laps, "laps_timed": len(records), "best": lap_text(min(times))}
    if len(times) >= 3:
        recent = times[-3:]
        summary["last_3_average"] = lap_text(statistics.mean(recent))
        summary["last_3_spread_s"] = round(max(recent) - min(recent), 2)
    with_sectors = [r["sectors_s"] for r in records if r.get("sectors_s")]
    if with_sectors:
        best = [min(s[i] for s in with_sectors) for i in range(3)]
        summary["best_sectors_s"] = best
        summary["best_possible_lap"] = lap_text(sum(best))
    return summary


# ---- from the database (own read-only connection) -----------------------------------------
def read_only(db_path):
    uri = "file:" + os.path.abspath(db_path).replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=2)


def race_events(db_path, session_id):
    """What happened to him this session: offs, spins, contacts, lock-ups, passes, and the last
    lines the radio said."""
    if not db_path or session_id is None:
        return {"error": "no race log available here"}
    shown = (
        "OFF_TRACK",
        "SPIN",
        "CONTACT",
        "IMPACT",
        "REAR_SNAP",
        "LOCKUP",
        "WHEELSPIN",
        "TRACK_LIMITS",
        "PENALTY",
    )
    conn = read_only(db_path)
    try:
        marks = ",".join("?" * len(shown))
        rows = conn.execute(
            f"SELECT kind, lap_count, corner, other_car FROM events WHERE session_id = ? AND kind IN ({marks}) "
            "ORDER BY sim_time",
            (session_id, *shown),
        ).fetchall()
        counts = {}
        for kind, _, _, _ in rows:
            counts[kind.lower()] = counts.get(kind.lower(), 0) + 1
        # contacts get their own full list: 13 wheelspins pushed both contacts out of the last 12
        # events on the 25 Sep bank run, and "who hit me?" got "can't see who"
        contacts = [
            {"lap": lap, "corner": corner, "other_car": other}
            for kind, lap, corner, other in rows
            if kind == "CONTACT"
        ]
        latest = [
            {"what": kind.lower(), "lap": lap, "corner": corner}
            for kind, lap, corner, other in rows
            if kind not in ("WHEELSPIN", "CONTACT")
        ][-10:]
        passes = conn.execute(
            "SELECT corner, lap_count, outcome FROM pass_attempts WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        said = conn.execute(
            "SELECT kind, line FROM radio_log WHERE session_id = ? AND status = 'spoken' "
            "ORDER BY sim_time DESC LIMIT 8",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "counts_this_session": counts,
        "contacts": contacts,
        "latest_other_events": latest,
        "pass_attempts": [
            {"corner": c, "lap": lap, "outcome": o} for c, lap, o in passes
        ],
        "radio_said_last_newest_first": [line for _, line in said],
    }


def setup_advice(db_path, session_id, race):
    me = race.me
    settings = {
        "brake_bias_rear_percent": round(me.brake_bias_rear * 100, 1)
        if me.brake_bias_rear
        else None,
        "traction_control": me.tc,
        "abs": me.abs,
        "motor_map": me.motor_map,
        "fixed_setup": race.session.fixed_setup,
        "can_change_in_car": "brake bias, TC and ABS (ranked races run fixed setups)",
    }
    if not db_path or session_id is None:
        return {"settings_now": settings, "advice": "no race log available here"}
    from seats.setup_engineer import advice_for

    conn = read_only(db_path)
    try:
        advice = [item["conclusion"] for item in advice_for(conn, session_id)]
    finally:
        conn.close()
    return {
        "settings_now": settings,
        "advice_from_this_session": advice or ["not enough evidence yet for a change"],
    }


def query_db(db_path, sql):
    """Last resort: one read-only SELECT over apex.db (every past session)."""
    if not db_path:
        return {"error": "no database here"}
    text = str(sql or "").strip().rstrip(";")
    if not re.match(r"(?is)^(select|with)\b", text) or ";" in text:
        return {"error": "only one SELECT statement"}
    conn = read_only(db_path)
    steps = {"n": 0}

    def cut_off():
        steps["n"] += 1
        return 1 if steps["n"] > SQL_STEPS_LIMIT // 1000 else 0

    conn.set_progress_handler(cut_off, 1000)
    try:
        cursor = conn.execute(text)
        columns = [d[0] for d in cursor.description or []]
        rows = cursor.fetchmany(MAX_SQL_ROWS)
    except sqlite3.Error as error:
        return {"error": str(error), "tables": SCHEMA_HINT}
    finally:
        conn.close()
    return {
        "columns": columns,
        "rows": [list(row) for row in rows],
        "rows_shown": len(rows),
    }


SCHEMA_HINT = (
    "sessions(id, started_at, track, session_type, final_place, grid, car_class, car_model), "
    "events(session_id, kind, sim_time, lap_count, corner, other_car), "
    "radio_log(session_id, sim_time, seat, kind, status, line), "
    "pass_attempts(session_id, driver, corner, lap_count, outcome), "
    "rivals_seen(session_id, driver, car_class, best_lap, final_place), "
    "corner_stats(session_id, lap_count, corner, brake_onset, min_speed), "
    "profile_facts(kind, track, subject, summary), llm_calls(session_id, seat, cost_rs)"
)


# ---- no data needed -----------------------------------------------------------------------
OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Mod: operator.mod,
}
FUNCTIONS = {
    "min": min,
    "max": max,
    "round": round,
    "abs": abs,
    "ceil": math.ceil,
    "floor": math.floor,
}


def calculate(expression):
    """Arithmetic only, so the model never does sums in its head (24 Sep: its own maths gave
    wrong calls). The result counts as data for the number check."""

    def value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
            return OPERATORS[type(node.op)](value(node.left), value(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in OPERATORS:
            return OPERATORS[type(node.op)](value(node.operand))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FUNCTIONS
        ):
            return FUNCTIONS[node.func.id](*[value(arg) for arg in node.args])
        raise ValueError(
            "only numbers, + - * / ** %, brackets, min max round abs ceil floor"
        )

    try:
        result = value(ast.parse(str(expression), mode="eval").body)
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError) as error:
        return {"error": str(error)}
    return {
        "expression": expression,
        "result": round(result, 3) if isinstance(result, float) else result,
    }


def knowledge_sections():
    with open(KNOWLEDGE_FILE, encoding="utf8") as f:
        text = f.read()
    sections = []
    for block in text.split("\n## ")[1:]:
        title, _, body = block.partition("\n")
        keys = re.search(r"^keys: (.*)$", body, re.M)
        words = {title.lower()} | {
            k.strip() for k in (keys.group(1).split(",") if keys else [])
        }
        sections.append(
            (title.strip(), words, re.sub(r"^keys: .*\n", "", body).strip())
        )
    return sections


def knowledge(topic):
    """The sections of knowledge.md about this topic (rules, flags, penalties, ratings, tyres,
    balance, what Apex can see), best match first."""
    asked = " " + re.sub(r"[^a-z0-9 ]+", " ", str(topic).lower()) + " "
    scored = []
    for title, words, body in knowledge_sections():
        score = sum(
            len(w)
            for w in words
            if " " + w + " " in asked or (len(w) >= 5 and w in asked)
        )
        if score:
            scored.append((score, title, body))
    scored.sort(reverse=True)
    if not scored:
        return {
            "not_found": topic,
            "topics": [title for title, _, _ in knowledge_sections()],
        }
    return {
        "sections": [{"topic": title, "facts": body} for _, title, body in scored[:2]]
    }
