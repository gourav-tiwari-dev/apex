import sqlite3, json
from statistics import median
from time import time

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

  CREATE TABLE IF NOT EXISTS  spoken(
    id            INTEGER PRIMARY KEY,
    event_id      INTEGER NOT NULL,
    spoken_at     REAL    NOT NULL,
    line          TEXT    NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events (id)
  );

  CREATE TABLE IF NOT EXISTS laps (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL,
    lap_count     INTEGER NOT NULL,
    validity      INTEGER NOT NULL,          -- mCountLapFlag: 0/1/2
    sector1       REAL,                      -- NOT WIRED YET (mCurSector1)
    sector2       REAL,                      -- NOT WIRED YET (mCurSector2, cumulative s1+s2)
    sector3       REAL,                      -- NOT WIRED YET (derive: mLastLapTime - sector2)
    fuel          REAL,                      -- NOT WIRED YET (mFuel, litres, at lap start)
    energy        REAL,                      -- NOT WIRED YET (mBatteryChargeFraction 0.0-1.0)
    tyre_wear     REAL,                      -- NOT WIRED YET (mWear; PROXY for age, not age)
    clean_air     INTEGER,                   -- NOT WIRED YET (mTimeBehindNext > 2.0 for the lap)
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
    "sessions": [("track", "TEXT"), ("session_type", "INTEGER"),
                 ("end_reason", "TEXT"), ("ended_at", "TEXT")],
}

def connect_db(db_path='apex.db'):
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

def start_session(conn,started_at,tape_path,replay_speed):
  cur = conn.execute("INSERT INTO sessions (started_at,tape_path,replay_speed) VALUES (?,?,?)",(started_at,tape_path,replay_speed))
  conn.commit()
  return cur.lastrowid

def save_event(conn,session_id,event):
  cur = conn.execute("INSERT INTO events (session_id,kind,sim_time,speed_kmh,corner,conclusion,lap_dist,lap_count) VALUES (?,?,?,?,?,?,?,?)",(session_id,event.kind,event.sim_time,event.speed_kmh,event.corner,event.conclusion,event.lap_dist,event.lap_count))
  conn.commit()
  return cur.lastrowid

def save_spoken(conn,event_id,spoken_at,line):
  cur = conn.execute("INSERT INTO spoken (event_id,spoken_at,line) VALUES (?,?,?)",(event_id,spoken_at,line))
  conn.commit()

def save_lap(conn,session_id,lap_count,validity,sector1=None,sector2=None,sector3=None,fuel=None,energy=None,tyre_wear=None,clean_air=None):
  cur = conn.execute("INSERT INTO laps (session_id,lap_count,validity,sector1,sector2,sector3,fuel,energy,tyre_wear,clean_air) VALUES (?,?,?,?,?,?,?,?,?,?)",(session_id,lap_count,validity,sector1,sector2,sector3,fuel,energy,tyre_wear,clean_air))
  conn.commit()
  return cur.lastrowid

def save_contract(conn,session_id,contract):
  cur = conn.execute("INSERT OR REPLACE INTO focus_contracts (session_id,corner,focus,metric,baseline,target,min_laps) VALUES (?,?,?,?,?,?,?)",(session_id,contract["corner"],contract["focus"],contract["metric"],contract["baseline"],contract["target"],contract["min_laps"]))
  conn.commit()
  return cur.lastrowid


def load_latest_contract(conn,before_session_id):
  row = conn.execute("SELECT session_id,corner,focus,metric,baseline,target,min_laps FROM focus_contracts WHERE session_id < ? ORDER BY session_id DESC LIMIT 1",(before_session_id,)).fetchone()
  if row is None:
    return None
  return {
    "session_id": row[0],
    "corner":     row[1],
    "focus":      row[2],
    "metric":     row[3],
    "baseline":   row[4],
    "target":     row[5],
    "min_laps":   row[6],
  }


def finish_session(conn,session_id,hash,end_reason=None,ended_at=None):
  cur = conn.execute("UPDATE sessions SET event_hash = ?, end_reason = ?, ended_at = ? WHERE id = ?",(hash,end_reason,ended_at,session_id))
  conn.commit()

def set_session_track(conn,session_id,track,session_type):
  conn.execute("UPDATE sessions SET track = ?, session_type = ? WHERE id = ?",(track,session_type,session_id))
  conn.commit()

def save_radio(conn,session_id,call,status,line=None,reason=None,latency_ms=None):
  cur = conn.execute("INSERT INTO radio_log (session_id,sim_time,seat,kind,priority,urgent,status,line,reason,conclusion,facts,evidence,latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
    (session_id,call.sim_time,call.seat,call.kind,call.priority,int(call.urgent),status,line,reason,call.conclusion,json.dumps(call.facts),json.dumps(call.evidence),latency_ms))
  conn.commit()
  return cur.lastrowid

def save_llm_call(conn,session_id,seat,llm):
  conn.execute("INSERT INTO llm_calls (session_id,seat,tokens_in,tokens_out,seconds,cost_rs) VALUES (?,?,?,?,?,?)",
    (session_id,seat,llm["tokens_in"],llm["tokens_out"],llm["seconds"],llm["cost_rs"]))
  conn.commit()


def save_corner_stat(conn,session_id,stat):
  cur = conn.execute("INSERT OR REPLACE INTO corner_stats (session_id,lap_count,corner,brake_onset,min_speed,slow_zone,coast) VALUES (?,?,?,?,?,?,?)",(session_id,stat.lap_count,stat.corner,stat.brake_onset,stat.min_speed,stat.slow_zone,stat.coast))
  conn.commit()
  return cur.lastrowid


def load_corner_rows(conn,session_id):
  # session_id None means every session
  if session_id is None:
    cur = conn.execute("SELECT corner,lap_count,brake_onset,min_speed,slow_zone,coast FROM corner_stats WHERE lap_count > 0")
  else:
    cur = conn.execute("SELECT corner,lap_count,brake_onset,min_speed,slow_zone,coast FROM corner_stats WHERE session_id = ? AND lap_count > 0",(session_id,))
  return cur.fetchall()


def load_incident_counts(conn,session_id):
  # session_id None means every session
  if session_id is None:
    cur = conn.execute("SELECT corner,COUNT(*) FROM events WHERE kind IN ('OFF_TRACK','SPIN','LOCKUP') AND lap_count > 0 GROUP BY corner")
  else:
    cur = conn.execute("SELECT corner,COUNT(*) FROM events WHERE session_id = ? AND kind IN ('OFF_TRACK','SPIN','LOCKUP') AND lap_count > 0 GROUP BY corner",(session_id,))

  incident_counts = {}
  for row in cur.fetchall():
    corner = row[0]
    count = row[1]
    incident_counts[corner] = count
  return incident_counts


def middle_value(numbers):
  if len(numbers) == 0:
    return None
  return median(numbers)


def spread(numbers):
  # one lap has nothing to compare against, so no spread
  if len(numbers) < 2:
    return None
  return max(numbers) - min(numbers)


def onset_spread_of(corner_row):
  return corner_row["onset_spread"]


def corner_report(conn,session_id=None):
  """Per-corner report card. Median is the typical lap, spread is how repeatable you are.
  Sorted by brake-point spread: the least repeatable corner comes first."""
  rows = load_corner_rows(conn,session_id)
  incident_counts = load_incident_counts(conn,session_id)

  # group the laps by corner: {"T1 Rettifilo": [(brake_onset, min_speed), ...], ...}
  laps_by_corner = {}
  for row in rows:
    corner, lap_count, brake_onset, min_speed,slow_zone,coast = row
    if corner not in laps_by_corner:
      laps_by_corner[corner] = []
    laps_by_corner[corner].append((brake_onset,min_speed,slow_zone,coast))

  report = []
  for corner in laps_by_corner:
    laps = laps_by_corner[corner]

    # a lap with no braking (T3 is taken flat) has brake_onset None - leave it out
    onsets = []
    speeds = []
    zones = []
    coasts = []
    for brake_onset, min_speed,slow_zone,coast in laps:
      if slow_zone is not None:
        zones.append(slow_zone)
      if coast is not None:
        coasts.append(coast)
      if brake_onset is not None:
        onsets.append(brake_onset)
      if min_speed is not None:
        speeds.append(min_speed)

    incidents = 0
    if corner in incident_counts:
      incidents = incident_counts[corner]

    report.append({
      "corner":        corner,
      "laps":          len(laps),
      "onset_median":  middle_value(onsets),
      "onset_spread":  spread(onsets),
      "speed_median":  middle_value(speeds),
      "speed_spread":  spread(speeds),
      "incidents":     incidents,
      "zone_median":   middle_value(zones),
      "coast_median":  middle_value(coasts)
    })


  with_spread = []
  without_spread = []
  for corner_row in report:
    if corner_row["onset_spread"] is None:
      without_spread.append(corner_row)
    else:
      with_spread.append(corner_row)
  with_spread.sort(key=onset_spread_of, reverse=True)

  return with_spread + without_spread


def format_number(value,width):
  if value is None:
    text = "--"
  else:
    text = f"{value:.1f}"
  return text.rjust(width)


def print_corner_report(conn,session_id=None):
  report = corner_report(conn,session_id)
  if not report:
    print("[no corner stats recorded]")
    return

  if session_id:
    title = "CORNER REPORT CARD - session " + str(session_id)
  else:
    title = "CORNER REPORT CARD - all sessions"

  header = "corner".ljust(16)
  header += "laps".rjust(5)
  header += "brake pt".rjust(10)
  header += "spread".rjust(8)
  header += "min spd".rjust(9)
  header += "spread".rjust(8)
  header += "inc".rjust(5)

  print()
  print(title)
  print(header)
  print("-" * 61)
  for corner_row in report:
    line = corner_row["corner"].ljust(16)
    line += str(corner_row["laps"]).rjust(5)
    line += format_number(corner_row["onset_median"],10)
    line += format_number(corner_row["onset_spread"],8)
    line += format_number(corner_row["speed_median"],9)
    line += format_number(corner_row["speed_spread"],8)
    line += str(corner_row["incidents"]).rjust(5)
    print(line)
  print()


def ranker(conn):
  cur = conn.execute("SELECT corner,SUM(CASE WHEN kind='CORNER_ENTRY' THEN 1 ELSE 0 END) AS entries,SUM(CASE WHEN kind IN ('OFF_TRACK','SPIN','LOCKUP') THEN 1 ELSE 0 END) AS incidents,SUM(CASE WHEN kind IN ('OFF_TRACK','SPIN','LOCKUP') THEN 1 ELSE 0 END) * 1.0 / SUM(CASE WHEN kind='CORNER_ENTRY' THEN 1 ELSE 0 END) AS rate FROM events GROUP BY corner ORDER BY rate DESC")
  return cur.fetchall()

def load_reference(path):
  with open(path, "r") as f:
    data = json.load(f)
    return data 

def compare_to_reference(conn,reference_path,session_id):
  report = corner_report(conn,session_id)
  reference_report = load_reference(reference_path)
  reference = reference_report["corners"]
  comparison = []

  for current_row in report:
    zone = current_row["zone_median"]
    coast = current_row["coast_median"]
    brake = current_row["onset_median"]
    current_corner = current_row["corner"]
    if current_corner not in reference:
      continue
    technique = reference[current_corner]["technique"]
    your_min_speed = current_row["speed_median"]
    ref_brake = reference[current_corner]["brake_point"]
    ref_min_speed = reference[current_corner]["min_speed"]
    
    time_lost = zone/(your_min_speed/3.6) - zone/(ref_min_speed/3.6)
    gap = your_min_speed - ref_min_speed
    braking_gap = ref_brake - brake
    comparison.append({"corner": current_corner, "yours": your_min_speed,"ref": ref_min_speed, "gap": gap, "confidence": reference[current_corner]["confidence"] , "time_lost": time_lost, "slow_zone": zone, "coast": coast, "brake_point":brake, "technique":technique, "braking_difference":braking_gap})
  
  return comparison

def time_lost_of(row):
  return row["time_lost"]
def rank_by_time_lost(comparison):
  comparison.sort(key = time_lost_of, reverse = True)
  return comparison

def build_evidence_pack(conn,reference, session_id):
  reference_report = load_reference(reference)
  ranked = rank_by_time_lost(compare_to_reference(conn,reference,session_id))
  corners = []
  for row in ranked:
    corners.append({
            "corner":             row["corner"],
            "time_lost_s":        round(row["time_lost"], 2),
            "your_min_kmh":       round(row["yours"], 1),
            "hymo_min_kmh":       row["ref"],
            "gap_kmh":            round(row["gap"], 1),
            "your_slow_zone_m":   round(row["slow_zone"], 1),
            "your_coast_m":       round(row["coast"], 1),
            "hymo_technique":     row["technique"],
            "braking_pt_difference_m":  round(row["braking_difference"],1)
        })

  pack = {                        
        "driver": {"input": "controller", "car": "BMW M4 LMGT3", "session_type": "race"},
        "reference": {"source": reference_report["source"],
                      "note": "min speeds checked on the HUD; technique text extracted by Gemini, unverified",
                      "brake_point_note": reference_report["brake_point_note"]},

        "focus": corners[0]["corner"],
        "corners": corners,
    }
  return pack
    


MIN_TARGET_STEP_KMH = 1.5
CONTRACT_MIN_LAPS = 8

def make_contract(pack, spoken):
  focus_corner = pack["corners"][0]
  baseline = focus_corner["your_min_kmh"]
  reference = focus_corner["hymo_min_kmh"]
  target = baseline + (reference - baseline) / 2

  if target - baseline < MIN_TARGET_STEP_KMH:
    return None

  contract = {
    "corner":   focus_corner["corner"],
    "focus":    spoken,
    "metric":   "min_speed",
    "baseline": round(baseline, 1),
    "target":   round(target, 1),
    "min_laps": CONTRACT_MIN_LAPS,
  }
  return contract

def load_contract_laps(conn, session_id, corner):
  cur = conn.execute("SELECT cs.min_speed FROM corner_stats cs JOIN laps l ON l.session_id = cs.session_id AND l.lap_count = cs.lap_count WHERE cs.session_id = ? AND cs.corner = ? AND cs.lap_count > 0 AND l.validity = 1",(session_id,corner))
  return [row[0] for row in cur.fetchall()]


def evaluate_contract(conn, contract, session_id):
  speeds = load_contract_laps(conn, session_id, contract["corner"])

  if len(speeds) < contract["min_laps"]:
    return {"verdict": "insufficient", "laps": len(speeds)}

  result = round(median(speeds), 1)
  gain = round(result - contract["baseline"], 1)

  if result >= contract["target"]:
    return {"verdict": "hit", "laps": len(speeds), "result": result, "gain": gain}

  if gain >= MIN_TARGET_STEP_KMH:
    return {"verdict": "moved", "laps": len(speeds), "result": result, "gain": gain}

  return {"verdict": "flat", "laps": len(speeds), "result": result, "gain": gain}
