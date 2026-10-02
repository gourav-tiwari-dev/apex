"""The database itself: its tables (SCHEMA), the columns added since (NEW_COLUMNS, so an old
apex.db still opens), and every writer a session uses - the session row, events, laps, corner
stats, radio lines, the model's cost, the result, rivals, opponents' corners, pass attempts."""

import json
import sqlite3

from game.race_snapshot import identity


SCHEMA = """
  CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY,
    started_at    TEXT    NOT NULL,
    tape_path     TEXT,
    event_hash    TEXT,
    replay_speed  REAL
);

  CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL,
    kind          TEXT    NOT NULL,
    sim_time      REAL    NOT NULL,
    speed_kmh     REAL    NOT NULL,
    corner        TEXT,
    conclusion    TEXT,
    lap_dist      REAL    NOT NULL,
    lap_count     INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions (id)
  );

  CREATE TABLE IF NOT EXISTS laps (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL,
    lap_count     INTEGER NOT NULL,
    validity      INTEGER NOT NULL,          -- mCountLapFlag: 0/1/2
    UNIQUE (session_id, lap_count),
    FOREIGN KEY (session_id) REFERENCES sessions (id)
  );

  -- REBUILDABLE CACHE. Everything here is derived from the tape by CornerStats.
  -- Safe to DROP and replay. Never the source of truth.
  CREATE TABLE IF NOT EXISTS corner_stats (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL,
    lap_count     INTEGER NOT NULL,
    corner        TEXT    NOT NULL,
    brake_onset   REAL,                      -- raw lap_dist where brake crossed 0.4; NULL = never braked
    min_speed     REAL,
    slow_zone     REAL,
    coast         REAL,
    UNIQUE (session_id, lap_count, corner),
    FOREIGN KEY (session_id) REFERENCES sessions (id)
  );

  CREATE TABLE IF NOT EXISTS focus_contracts (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL,
    corner        TEXT    NOT NULL,
    focus         TEXT    NOT NULL,
    metric        TEXT    NOT NULL,
    baseline      REAL    NOT NULL,
    target        REAL    NOT NULL,
    min_laps      INTEGER NOT NULL,
    UNIQUE (session_id),
    FOREIGN KEY (session_id) REFERENCES sessions (id)
  );

  -- v2: every call any seat raised, and what happened to it.
  -- status: spoken / stale / expired / no_line / queue_full / voice_failed
  CREATE TABLE IF NOT EXISTS radio_log (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL,
    sim_time      REAL    NOT NULL,          -- when the seat raised it
    seat          TEXT    NOT NULL,
    kind          TEXT    NOT NULL,
    priority      INTEGER NOT NULL,
    urgent        INTEGER NOT NULL,
    status        TEXT    NOT NULL,
    line          TEXT,                      -- the words actually said
    reason        TEXT,                      -- why a line was refused or dropped
    conclusion    TEXT,
    facts         TEXT,                      -- JSON
    evidence      TEXT,                      -- JSON: ids of the rows it rests on
    latency_ms    INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions (id)
  );

  -- v2: every car I raced against, one row per session, for the rival dossier
  CREATE TABLE IF NOT EXISTS rivals_seen (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL,
    steam_id      TEXT,                      -- the stable identity; names can change
    driver        TEXT    NOT NULL,
    car_class     TEXT,
    best_lap      REAL,
    final_place   INTEGER,
    UNIQUE (session_id, driver),
    FOREIGN KEY (session_id) REFERENCES sessions (id)
  );

  -- v2: how every same-class car took each corner, from their telemetry during the race.
  -- The reference for tracks with no reference lap: the fastest car in my own races.
  CREATE TABLE IF NOT EXISTS opponent_corners (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL,
    steam_id      TEXT,
    driver        TEXT,
    car_class     TEXT,
    corner        TEXT    NOT NULL,
    lap_count     INTEGER,
    min_speed     REAL    NOT NULL,          -- from 5 Hz snapshots: good to a km/h or two
    FOREIGN KEY (session_id) REFERENCES sessions (id)
  );

  -- v2: every pass I tried and how it ended - the hasty-commit habit, measured
  CREATE TABLE IF NOT EXISTS pass_attempts (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL,
    steam_id      TEXT,
    driver        TEXT,
    corner        TEXT,
    lap_count     INTEGER,
    outcome       TEXT    NOT NULL,          -- pass / no_pass / contact
    FOREIGN KEY (session_id) REFERENCES sessions (id)
  );

  -- v2 TEAM MEMORY. A cache rebuilt from the drives by team_memory.build_profile().
  -- A fact exists only with evidence: at least one row in profile_evidence (checked in code,
  -- and every evidence row must point at a real event / lap / session - foreign keys).
  CREATE TABLE IF NOT EXISTS profile_facts (
    id            INTEGER PRIMARY KEY,
    kind          TEXT    NOT NULL,          -- corner_habit, lap_one, contact_corner, rival, clean_race
    track         TEXT,
    subject       TEXT    NOT NULL,          -- a corner, a driver, or "me"
    value         REAL,
    occurrences   INTEGER NOT NULL,
    drives        INTEGER NOT NULL,          -- how many separate drives it showed up in
    summary       TEXT    NOT NULL           -- plain English, what a seat may say
  );

  CREATE TABLE IF NOT EXISTS profile_evidence (
    id            INTEGER PRIMARY KEY,
    fact_id       INTEGER NOT NULL,
    event_id      INTEGER,
    session_id    INTEGER,
    CHECK (event_id IS NOT NULL OR session_id IS NOT NULL),
    FOREIGN KEY (fact_id) REFERENCES profile_facts (id),
    FOREIGN KEY (event_id) REFERENCES events (id),
    FOREIGN KEY (session_id) REFERENCES sessions (id)
  );

  -- v2: the cost of every LLM call, so the Rs 5 cap is checked against real numbers
  CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL,
    seat          TEXT    NOT NULL,
    tokens_in     INTEGER NOT NULL,
    tokens_out    INTEGER NOT NULL,
    seconds       REAL,
    cost_rs       REAL    NOT NULL,          -- ESTIMATE: derived prices, see radio.Budget
    FOREIGN KEY (session_id) REFERENCES sessions (id)
  );
"""

# v2 columns added to tables that already exist in older databases
NEW_COLUMNS = {
    "sessions": [
        ("track", "TEXT"),
        ("session_type", "INTEGER"),
        ("end_reason", "TEXT"),
        ("ended_at", "TEXT"),
        ("grid", "INTEGER"),
        ("final_place", "INTEGER"),
        ("track_limit_strikes", "INTEGER"),
        ("first_phase", "INTEGER"),  # race phase when Apex first saw the session
        ("launch_id", "TEXT"),  # one apex.py launch: practice, quali and race share it
        ("car_class", "TEXT"),
        ("car_model", "TEXT"),
        # what he ran from the cockpit (27 Sep): setup advice names the setting it changes
        ("traction_control", "INTEGER"),
        ("abs", "INTEGER"),
        ("brake_bias_rear", "REAL"),
        ("motor_map", "INTEGER"),
    ],
    # time through the corner and how it was driven, so a reference can say what to DO
    "opponent_corners": [
        ("car_model", "TEXT"),
        ("time_s", "REAL"),
        ("brake_onset", "REAL"),
        ("throttle_on", "REAL"),
    ],
    "events": [("other_car", "TEXT"), ("magnitude", "REAL")],
}


def connect_db(db_path="apex.db"):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    add_new_columns(conn)
    return conn


def add_new_columns(conn):
    # CREATE TABLE IF NOT EXISTS never changes a table that is already there,
    # so an old apex.db needs its new columns added by hand
    for table, columns in NEW_COLUMNS.items():
        existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        for name, kind in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    conn.commit()


def latest_session_id(conn):
    row = conn.execute("SELECT MAX(id) FROM sessions").fetchone()
    return row[0]


def start_session(conn, started_at, tape_path, replay_speed, launch_id=None):
    cur = conn.execute(
        "INSERT INTO sessions (started_at,tape_path,replay_speed,launch_id) VALUES (?,?,?,?)",
        (started_at, tape_path, replay_speed, launch_id),
    )
    conn.commit()
    return cur.lastrowid


def save_event(conn, session_id, event):
    cur = conn.execute(
        "INSERT INTO events (session_id,kind,sim_time,speed_kmh,corner,conclusion,lap_dist,lap_count,other_car,magnitude) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            session_id,
            event.kind,
            event.sim_time,
            event.speed_kmh,
            event.corner,
            event.conclusion,
            event.lap_dist,
            event.lap_count,
            event.other_car,
            event.magnitude,
        ),
    )
    conn.commit()
    return cur.lastrowid


def save_lap(conn, session_id, lap_count, validity):
    cur = conn.execute(
        "INSERT INTO laps (session_id, lap_count, validity) VALUES (?, ?, ?)",
        (session_id, lap_count, validity),
    )
    conn.commit()
    return cur.lastrowid


def finish_session(conn, session_id, hash, end_reason=None, ended_at=None):
    conn.execute(
        "UPDATE sessions SET event_hash = ?, end_reason = ?, ended_at = ? WHERE id = ?",
        (hash, end_reason, ended_at, session_id),
    )
    conn.commit()


def save_car_settings(conn, session_id, settings):
    """His car's settings for the session: (traction control, ABS, rear brake bias, motor
    map), as the car had them."""
    traction_control, abs_level, brake_bias_rear, motor_map = settings
    conn.execute(
        "UPDATE sessions SET traction_control = ?, abs = ?, brake_bias_rear = ?, motor_map = ? WHERE id = ?",
        (traction_control, abs_level, brake_bias_rear, motor_map, session_id),
    )
    conn.commit()


def save_session_result(conn, session_id, grid, final_place, track_limit_strikes):
    conn.execute(
        "UPDATE sessions SET grid = ?, final_place = ?, track_limit_strikes = ? WHERE id = ?",
        (grid, final_place, track_limit_strikes, session_id),
    )
    conn.commit()


def save_rivals(conn, session_id, opponents):
    for o in opponents:
        conn.execute(
            "INSERT OR REPLACE INTO rivals_seen (session_id,steam_id,driver,car_class,best_lap,final_place) VALUES (?,?,?,?,?,?)",
            (session_id, identity(o), o.driver, o.car_class, o.best_lap, o.place),
        )
    conn.commit()


def save_opponent_corners(conn, session_id, rows):
    for row in rows:
        conn.execute(
            "INSERT INTO opponent_corners (session_id,steam_id,driver,car_class,car_model,corner,lap_count,min_speed,time_s,brake_onset,throttle_on) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                row.who,
                row.driver,
                row.car_class,
                row.car_model,
                row.corner,
                row.lap,
                row.min_speed,
                row.time_s,
                row.brake_onset,
                row.throttle_on,
            ),
        )
    conn.commit()


def save_pass_attempts(conn, session_id, attempts):
    for steam_id, driver, corner, lap_count, outcome in attempts:
        conn.execute(
            "INSERT INTO pass_attempts (session_id,steam_id,driver,corner,lap_count,outcome) VALUES (?,?,?,?,?,?)",
            (session_id, steam_id, driver, corner, lap_count, outcome),
        )
    conn.commit()


def set_session_track(conn, session_id, race):
    """What the session is, from its first race snapshot: the track, the session type, the
    phase it started in, and his car (class and model)."""
    car_class = None
    car_model = None
    if race.me:
        car_class = race.me.car_class
        car_model = race.me.car_model
    conn.execute(
        "UPDATE sessions SET track = ?, session_type = ?, first_phase = ?, car_class = ?, car_model = ? WHERE id = ?",
        (
            race.session.track,
            race.session.session,
            race.session.game_phase,
            car_class,
            car_model,
            session_id,
        ),
    )
    conn.commit()


def track_key(track):
    # v1 sessions have no track name: they were all Monza
    if track is None or "monza" in track.lower():
        return "monza"
    return track


def track_of(conn, session_id):
    row = conn.execute(
        "SELECT track FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return track_key(None)
    return track_key(row[0])


def save_radio(conn, session_id, call, status, details=None):
    """One row of the radio log: the call and what became of it (status). details, for a
    line that was cooked: its words (line), why it failed (reason) and how long it took to
    start (latency_ms), as the desk reports them; any of them may be missing."""
    if details is None:
        details = {}
    line = details.get("line")
    reason = details.get("reason")
    latency_ms = details.get("latency_ms")
    cur = conn.execute(
        "INSERT INTO radio_log (session_id,sim_time,seat,kind,priority,urgent,status,line,reason,conclusion,facts,evidence,latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            session_id,
            call.sim_time,
            call.seat,
            call.kind,
            call.priority,
            int(call.urgent),
            status,
            line,
            reason,
            call.conclusion,
            json.dumps(call.facts),
            json.dumps(call.evidence),
            latency_ms,
        ),
    )
    conn.commit()
    return cur.lastrowid


def save_llm_call(conn, session_id, seat, llm):
    conn.execute(
        "INSERT INTO llm_calls (session_id,seat,tokens_in,tokens_out,seconds,cost_rs) VALUES (?,?,?,?,?,?)",
        (
            session_id,
            seat,
            llm["tokens_in"],
            llm["tokens_out"],
            llm["seconds"],
            llm["cost_rs"],
        ),
    )
    conn.commit()


def save_corner_stat(conn, session_id, stat):
    cur = conn.execute(
        "INSERT OR REPLACE INTO corner_stats (session_id,lap_count,corner,brake_onset,min_speed,slow_zone,coast) VALUES (?,?,?,?,?,?,?)",
        (
            session_id,
            stat.lap_count,
            stat.corner,
            stat.brake_onset,
            stat.min_speed,
            stat.slow_zone,
            stat.coast,
        ),
    )
    conn.commit()
    return cur.lastrowid
