"""Turn LMU's own telemetry recordings into Apex tapes.

    python dev/lmu_import.py                    convert every recording in LMU's Telemetry folder
    python dev/lmu_import.py FILE.duckdb        convert one
    python dev/lmu_import.py --load             convert, then load them into apex.db + team memory

LMU saves a .duckdb file per session by itself (UserData/Telemetry). It is my car only, no
other cars, but 101 channels, including steering, which Apex's old tapes never had.

What each channel is, measured on a Le Mans race file (23 Sep 2026):
  Ground Speed    km/h at 100 Hz (max 286.4 = a GT3 flat out on the Mulsanne)
  Wheel Speed     m/s at the tyre surface, 4 wheels (wheel / ground = 0.278 = 1 / 3.6)
  Brake, Throttle 0-100 at 50 Hz
  Steering Pos    degrees (-48 .. +69 seen)
  G Force         in g at 10 Hz, and the LABELS ARE SWAPPED in LMU's export (checked 23 Sep):
                  "G Force Lat" follows the brake (correlation 0.92, 0.03 with cornering)
                  "G Force Long" follows steering x speed^2 (correlation 0.83)
  Lap Dist        metres at 10 Hz
  GPS             latitude / longitude at 10 Hz. NOT used for yaw: tested 23 Sep, a yaw rate
                  from GPS heading gave 0.56x the physics (lat g / speed; real tapes give 1.01),
                  so snaps and spins from it would be false alarms. (Re-tested against the
                  correct lateral channel: median 0.98, right on average, but p10-p90 spans
                  0.23-3.27 against 1.0-2.0 on real yaw data: too noisy for a snap detector.) There is no yaw channel,
                  so imported tapes carry yaw_rate 0 and the spin / rear-snap detectors stay silent.
  Channels have no timestamps: sample i of a channel at f Hz is at start + i / f, where
  start is the first event time (GPS Time confirms it: 26.3725 + 5000 / 100 = 76.3725).

Tapes come out at 50 Hz, with a race line once a second (session, track, fuel, tyres...).
"""

import os
import sys

# run as `python dev/lmu_import.py` from the project folder: Apex's modules are one folder up
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bisect
import glob
import gzip
import json
import os
import sys
from dataclasses import asdict

import duckdb

from game.car_frame import CarState
from driving.detectors import FRONT_RADIUS, REAR_RADIUS
from game.race_snapshot import Session, Me, RaceSnapshot

LMU_TELEMETRY = (
    r"C:\Program Files (x86)\Steam\steamapps\common\Le Mans Ultimate\UserData\Telemetry"
)
TAPE_FOLDER = "tapes_lmu"
RATE_HZ = 50
# GUESSED: steering angle that counts as full lock, to scale degrees to Apex's -1..1.
# The most seen in a race was 69 degrees.
STEERING_FULL_DEG = 70.0
SESSION_TYPES = {"Practice": 1, "Qualify": 5, "Race": 10}
G = 9.81


def channel(con, name, columns="value"):
    return [
        row if len(row) > 1 else row[0]
        for row in con.execute(f'SELECT {columns} FROM "{name}"').fetchall()
    ]


def events(con, name):
    return Events(con.execute(f'SELECT ts, value FROM "{name}" ORDER BY ts').fetchall())


def value_at(samples, rate, t, start):
    """The latest sample of a rate-Hz channel at time t (sample and hold)."""
    index = int((t - start) * rate)
    index = max(0, min(index, len(samples) - 1))
    return samples[index]


def event_at(event_list, t, default=0):
    """The value of the latest event at or before t (binary search: called every frame)."""
    times = (
        [ts for ts, _ in event_list]
        if not hasattr(event_list, "times")
        else event_list.times
    )
    index = bisect.bisect_right(times, t) - 1
    if index < 0:
        return default
    return event_list[index][1]


class Events(list):
    """An event list that remembers its timestamps, so event_at can binary-search it."""

    def __init__(self, rows):
        super().__init__(rows)
        self.times = [ts for ts, _ in rows]


def read_metadata(con):
    return dict(con.execute("SELECT * FROM metadata").fetchall())


def convert(path, out_folder=TAPE_FOLDER):
    """One LMU recording to one Apex tape. Returns what was converted, or None for a
    recording with no laps."""
    con = duckdb.connect(path, read_only=True)
    meta = read_metadata(con)
    rate_of = dict(
        con.execute("SELECT channelName, frequency FROM channelsList").fetchall()
    )
    rate_of["G Force Long"], rate_of["G Force Lat"] = (
        rate_of["G Force Lat"],
        rate_of["G Force Long"],
    )
    laps = events(con, "Lap")
    if not laps:
        return None
    recording = Recording(con, meta, rate_of, laps)

    os.makedirs(out_folder, exist_ok=True)
    name = os.path.basename(path).replace(".duckdb", "")
    tape_path = os.path.join(
        out_folder, "tape_lmu_" + name.replace(" ", "_") + ".jsonl.gz"
    )
    write_tape(tape_path, recording)
    con.close()
    return {
        "tape": tape_path,
        "track": recording.track,
        "session": meta.get("SessionType"),
        "car": meta.get("CarName"),
        "laps": len(laps) - 1,
        "minutes": round(recording.duration / 60, 1),
        "setup": meta.get("CarSetup"),
    }


def write_tape(tape_path, recording):
    """A car frame every 1 / RATE_HZ s, and a race line whenever a new second starts."""
    last_race_second = None
    with gzip.open(tape_path, "wt") as out:
        for i in range(int(recording.duration * RATE_HZ)):
            t = recording.start + i / RATE_HZ
            second = int(t)
            if second != last_race_second:
                last_race_second = second
                snapshot = RaceSnapshot(
                    sim_time=round(t, 3),
                    session=recording.session_at(t),
                    me=recording.me_at(t),
                )
                out.write(json.dumps(asdict(snapshot)) + "\n")
            out.write(json.dumps(asdict(recording.frame_at(t))) + "\n")


class Recording:
    """One LMU recording, every channel Apex uses read once. A channel has no timestamps:
    sample i of a channel at f Hz is at start + i / f."""

    def __init__(self, con, meta, rate_of, laps):
        self.meta = meta
        self.rate_of = rate_of
        self.laps = laps
        self.start = laps[0][0]
        self.speed = channel(con, "Ground Speed")
        self.throttle = channel(con, "Throttle Pos")
        self.brake = channel(con, "Brake Pos")
        self.steering = channel(con, "Steering Pos Unfiltered")
        self.steering_filtered = channel(con, "Steering Pos")
        self.rpm = channel(con, "Engine RPM")
        self.lap_dist = channel(con, "Lap Dist")
        # swapped on purpose: LMU labels them the wrong way round (see the top of this file)
        self.g_lat = channel(con, "G Force Long")
        self.g_long = channel(con, "G Force Lat")
        self.wheels = channel(con, "Wheel Speed", "value1, value2, value3, value4")
        self.surfaces = channel(con, "SurfaceTypes", "value1, value2, value3, value4")
        self.gear_events = events(con, "Gear")
        self.max_rpm = events(con, "Engine Max RPM")[0][1]
        self.impacts = [ts for ts, hit in events(con, "LastImpactMagnitude") if hit]

        self.fuel = channel(con, "Fuel Level")
        self.energy = channel(con, "Virtual Energy")
        self.temps = {}
        for side in ("Left", "Centre", "Right"):
            self.temps[side] = channel(
                con, f"TyresTemp{side}", "value1, value2, value3, value4"
            )
        self.pressures = channel(con, "TyresPressure", "value1, value2, value3, value4")
        self.wear = channel(con, "Tyres Wear", "value1, value2, value3, value4")
        self.brake_temps = channel(con, "Brakes Temp", "value1, value2, value3, value4")
        self.behind_next = channel(con, "Time Behind Next")
        self.lap_times = events(con, "Lap Time")
        self.tc = events(con, "TCLevel")
        self.abs_level = events(con, "ABSLevel")
        self.bias = events(con, "Brake Bias Rear")

        self.duration = len(self.speed) / rate_of["Ground Speed"]
        self.end = self.start + self.duration
        self.session_type = SESSION_TYPES.get(meta.get("SessionType"), 1)
        self.track = meta.get("TrackName", "unknown")
        self.best = 0.0  # his best lap so far: race lines are built in time order

    def at(self, samples, name, t):
        """The latest sample at time t of the channel called name."""
        return value_at(samples, self.rate_of[name], t, self.start)

    def tyre_temps_at(self, t):
        """Each tyre's left, centre and right temperature."""
        rows = []
        for w in range(4):
            row = []
            for side in ("Left", "Centre", "Right"):
                row.append(
                    round(self.at(self.temps[side], f"TyresTemp{side}", t)[w], 1)
                )
            rows.append(row)
        return rows

    def me_at(self, t):
        """My car in the race line at time t. Keeps the best lap as it goes."""
        laps_done = event_at(self.laps, t)
        last_lap = event_at(self.lap_times, t, 0.0)
        if last_lap > 0 and (self.best == 0 or last_lap < self.best):
            self.best = last_lap
        return Me(
            driver=self.meta.get("DriverName", ""),
            car_class=self.meta.get("CarClass", ""),
            place=0,
            grid=0,
            laps=laps_done,
            time_behind_next=round(self.at(self.behind_next, "Time Behind Next", t), 3),
            time_behind_leader=0.0,
            laps_behind_leader=0,
            best_lap=round(self.best, 3),
            last_lap=round(last_lap, 3),
            sector=0,
            cur_sector1=0.0,
            cur_sector2=0.0,
            pitstops=0,
            penalties=0,
            in_pits=False,
            pit_state=0,
            finish_status=0,
            flag=0,
            under_yellow=False,
            count_lap_flag=2,
            fuel=round(self.at(self.fuel, "Fuel Level", t), 3),
            fuel_capacity=0.0,
            virtual_energy=round(self.at(self.energy, "Virtual Energy", t) / 100.0, 4),
            battery=0.0,
            lift_and_coast=0,
            track_limit_steps=0,
            gap_car_ahead=0.0,
            gap_car_behind=0.0,
            gap_place_ahead=0.0,
            gap_place_behind=0.0,
            tyre_temps=self.tyre_temps_at(t),
            tyre_pressures=rounded(self.at(self.pressures, "TyresPressure", t), 1),
            # LMU's export counts tread LEFT (100 = new); Apex counts wear done
            tyre_wear=wear_done(self.at(self.wear, "Tyres Wear", t)),
            brake_temps=rounded(self.at(self.brake_temps, "Brakes Temp", t), 1),
            compound="",
            dents=[0] * 8,
            detached=False,
            overheating=False,
            brake_bias_rear=round(event_at(self.bias, t, 0.0), 4),
            tc=int(event_at(self.tc, t, 0)),
            abs=int(event_at(self.abs_level, t, 0)),
            motor_map=0,
            arb_front=0,
            arb_rear=0,
            car_model=self.meta.get("CarName", ""),
        )

    def session_at(self, t):
        """The session in the race line at time t: the phase turns to over in the last second."""
        return Session(
            track=self.track,
            session=self.session_type,
            game_phase=8 if t >= self.end - 1.0 else 5,
            time_remaining=round(self.end - t, 1),
            end_time=round(self.end, 1),
            max_laps=2147483647,
            yellow_flag_state=0,
            sector_flags=[0, 0, 0],
            start_light=0,
            red_lights=0,
            raining=0.0,
            ambient_temp=0.0,
            track_temp=0.0,
            wetness=0.0,
            grip_level=0,
            fixed_setup=True,
            limit_steps_per_penalty=0,
            in_realtime=True,
        )

    def frame_at(self, t):
        """My car's telemetry frame at time t, in Apex's units."""
        wheel_mps = self.at(self.wheels, "Wheel Speed", t)
        radii = [FRONT_RADIUS, FRONT_RADIUS, REAR_RADIUS, REAR_RADIUS]
        hits_so_far = bisect.bisect_right(self.impacts, t)
        last_impact = self.impacts[hits_so_far - 1] if hits_so_far else None
        return CarState(
            speed_kmh=round(self.at(self.speed, "Ground Speed", t), 3),
            throttle=round(self.at(self.throttle, "Throttle Pos", t) / 100.0, 4),
            brake=round(self.at(self.brake, "Brake Pos", t) / 100.0, 4),
            gear=int(event_at(self.gear_events, t, 0)),
            rpm=round(self.at(self.rpm, "Engine RPM", t), 1),
            max_rpm=self.max_rpm,
            lap_dist=round(self.at(self.lap_dist, "Lap Dist", t), 2),
            lap_invalidated=False,
            # Apex keeps wheel rotation in rad/s, like the shared memory, with forward negative
            wheel_rot=[round(-wheel_mps[w] / radii[w], 3) for w in range(4)],
            accel_long=round(self.at(self.g_long, "G Force Lat", t) * G, 3),
            accel_lat=round(self.at(self.g_lat, "G Force Long", t) * G, 3),
            surface=list(self.at(self.surfaces, "SurfaceTypes", t)),
            yaw_rate=0.0,  # no yaw channel in LMU's export (see top)
            elapsed_time=round(t, 4),
            steering=round(
                self.at(self.steering, "Steering Pos Unfiltered", t)
                / STEERING_FULL_DEG,
                4,
            ),
            steering_filtered=round(
                self.at(self.steering_filtered, "Steering Pos", t) / STEERING_FULL_DEG,
                4,
            ),
            last_impact_time=last_impact,
            last_impact_magnitude=1.0 if last_impact is not None else None,
        )


def rounded(values, digits):
    """Each value rounded."""
    return [round(value, digits) for value in values]


def wear_done(tread_left):
    """LMU's tread left (100 = new) as Apex's wear done (0 = new, 1 = gone)."""
    return [round((100.0 - w) / 100.0, 4) for w in tread_left]


def load_into_apex(tape_paths):
    """Replay converted tapes into apex.db, once each, then rebuild the team memory."""
    import contextlib
    import io
    from session import run_session
    from memory.db import connect_db
    from memory.team_memory import build_profile

    conn = connect_db("apex.db")
    already = {row[0] for row in conn.execute("SELECT tape_path FROM sessions")}
    conn.close()
    for tape in tape_paths:
        if tape in already:
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            run_session(True, None, tape, out_loud=False)
        print(f"  loaded {tape}")
    conn = connect_db("apex.db")
    found = build_profile(conn)
    conn.close()
    return found


if __name__ == "__main__":
    load = "--load" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--load"]
    files = args or sorted(glob.glob(os.path.join(LMU_TELEMETRY, "*.duckdb")))
    converted = []
    for path in files:
        try:
            result = convert(path)
        except duckdb.IOException:
            print(f"  skipped (LMU is still writing it): {os.path.basename(path)}")
            continue
        if result:
            print(
                f"  {result['session']:8s} {result['laps']:3d} laps {result['minutes']:5.1f} min -> {result['tape']}"
            )
            if result["laps"] >= 1:
                converted.append(result["tape"])
    if load:
        print(load_into_apex(converted))
