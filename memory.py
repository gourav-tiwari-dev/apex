import sqlite3
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


def corner_report(conn,session_id=None):
  """Per-corner report card. Median is the typical lap, spread is how repeatable you are.
  Sorted by brake-point spread: the least repeatable corner comes first."""
  if session_id is None:
    rows = conn.execute("SELECT corner,lap_count,brake_onset,min_speed FROM corner_stats").fetchall()
    incident_rows = conn.execute("SELECT corner,COUNT(*) FROM events WHERE kind IN ('OFF_TRACK','SPIN','LOCKUP') GROUP BY corner").fetchall()
  else:
    rows = conn.execute("SELECT corner,lap_count,brake_onset,min_speed FROM corner_stats WHERE session_id = ?",(session_id,)).fetchall()
    incident_rows = conn.execute("SELECT corner,COUNT(*) FROM events WHERE session_id = ? AND kind IN ('OFF_TRACK','SPIN','LOCKUP') GROUP BY corner",(session_id,)).fetchall()

  incidents = dict(incident_rows)
  by_corner = {}
  for corner,lap_count,brake_onset,min_speed in rows:
    by_corner.setdefault(corner,[]).append((brake_onset,min_speed))

  report = []
  for corner,values in by_corner.items():
    onsets = [o for o,_ in values if o is not None]
    speeds = [s for _,s in values if s is not None]
    report.append({
      "corner":        corner,
      "laps":          len(values),
      "onset_median":  median(onsets) if onsets else None,
      "onset_spread":  max(onsets)-min(onsets) if len(onsets) > 1 else None,
      "speed_median":  median(speeds) if speeds else None,
      "speed_spread":  max(speeds)-min(speeds) if len(speeds) > 1 else None,
      "incidents":     incidents.get(corner,0),
    })
  report.sort(key=lambda r: (r["onset_spread"] is None, -(r["onset_spread"] or 0)))
  return report


def print_corner_report(conn,session_id=None):
  report = corner_report(conn,session_id)
  if not report:
    print("[no corner stats recorded]")
    return
  def fmt(v,width,dp):
    return " " * (width-2) + "--" if v is None else f"{v:>{width}.{dp}f}"
  print()
  print("CORNER REPORT CARD" + (f" - session {session_id}" if session_id else " - all sessions"))
  print(f"{'corner':<16}{'laps':>5}{'brake pt':>10}{'spread':>8}{'min spd':>9}{'spread':>8}{'inc':>5}")
  print("-" * 61)
  for r in report:
    print(f"{r['corner']:<16}{r['laps']:>5}{fmt(r['onset_median'],10,1)}{fmt(r['onset_spread'],8,1)}{fmt(r['speed_median'],9,1)}{fmt(r['speed_spread'],8,1)}{r['incidents']:>5}")
  print()


def ranker(conn):
  cur = conn.execute("SELECT corner,SUM(CASE WHEN kind='CORNER_ENTRY' THEN 1 ELSE 0 END) AS entries,SUM(CASE WHEN kind IN ('OFF_TRACK','SPIN','LOCKUP') THEN 1 ELSE 0 END) AS incidents,SUM(CASE WHEN kind IN ('OFF_TRACK','SPIN','LOCKUP') THEN 1 ELSE 0 END) * 1.0 / SUM(CASE WHEN kind='CORNER_ENTRY' THEN 1 ELSE 0 END) AS rate FROM events GROUP BY corner ORDER BY rate DESC")
  return cur.fetchall()
