import sqlite3, json
from statistics import median

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
    UNIQUE (session_id, lap_count, corner),
    FOREIGN KEY (session_id) REFERENCES sessions (id)
  );
"""

def connect_db(db_path='apex.db'):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn

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


def finish_session(conn,session_id,hash):
  cur = conn.execute("UPDATE sessions SET event_hash = ? WHERE id = ?",(hash,session_id))
  conn.commit()


def save_corner_stat(conn,session_id,stat):
  cur = conn.execute("INSERT OR REPLACE INTO corner_stats (session_id,lap_count,corner,brake_onset,min_speed) VALUES (?,?,?,?,?)",(session_id,stat.lap_count,stat.corner,stat.brake_onset,stat.min_speed))
  conn.commit()
  return cur.lastrowid


def load_corner_rows(conn,session_id):
  # session_id None means every session
  if session_id is None:
    cur = conn.execute("SELECT corner,lap_count,brake_onset,min_speed FROM corner_stats WHERE lap_count > 0")
  else:
    cur = conn.execute("SELECT corner,lap_count,brake_onset,min_speed FROM corner_stats WHERE session_id = ? AND lap_count > 0",(session_id,))
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
    corner, lap_count, brake_onset, min_speed = row
    if corner not in laps_by_corner:
      laps_by_corner[corner] = []
    laps_by_corner[corner].append((brake_onset,min_speed))

  report = []
  for corner in laps_by_corner:
    laps = laps_by_corner[corner]

    # a lap with no braking (T3 is taken flat) has brake_onset None - leave it out
    onsets = []
    speeds = []
    for brake_onset, min_speed in laps:
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
    })

  # corners with a spread first, biggest spread on top; corners with no spread at the bottom
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