import sqlite3

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



# TODO(next brick): corner stats + time-loss ranker — cut-line #6
def ranker(conn):
  cur = conn.execute("SELECT corner,SUM(CASE WHEN kind='CORNER_ENTRY' THEN 1 ELSE 0 END) AS entries,SUM(CASE WHEN kind IN ('OFF_TRACK','SPIN','LOCKUP') THEN 1 ELSE 0 END) AS incidents,SUM(CASE WHEN kind IN ('OFF_TRACK','SPIN','LOCKUP') THEN 1 ELSE 0 END) * 1.0 / SUM(CASE WHEN kind='CORNER_ENTRY' THEN 1 ELSE 0 END) AS rate FROM events GROUP BY corner ORDER BY rate DESC")
  return cur.fetchall()