"""Turn LMU's own telemetry recordings into Apex tapes.

    python lmu_import.py                    convert every recording in LMU's Telemetry folder
    python lmu_import.py FILE.duckdb        convert one
    python lmu_import.py --load             convert, then load them into apex.db + team memory

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
from race_state import Session, Me, RaceSnapshot

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
    start = laps[0][0]

    speed = channel(con, "Ground Speed")
    throttle = channel(con, "Throttle Pos")
    brake = channel(con, "Brake Pos")
    steering = channel(con, "Steering Pos Unfiltered")
    steering_filtered = channel(con, "Steering Pos")
    rpm = channel(con, "Engine RPM")
    lap_dist = channel(con, "Lap Dist")
    # swapped on purpose: LMU labels them the wrong way round (see the top of this file)
    g_lat = channel(con, "G Force Long")
    g_long = channel(con, "G Force Lat")
    wheels = channel(con, "Wheel Speed", "value1, value2, value3, value4")
    surfaces = channel(con, "SurfaceTypes", "value1, value2, value3, value4")
    gear_events = events(con, "Gear")
    max_rpm = events(con, "Engine Max RPM")[0][1]
    impacts = [ts for ts, hit in events(con, "LastImpactMagnitude") if hit]

    fuel = channel(con, "Fuel Level")
    energy = channel(con, "Virtual Energy")
    temps = {
        side: channel(con, f"TyresTemp{side}", "value1, value2, value3, value4")
        for side in ("Left", "Centre", "Right")
    }
    pressures = channel(con, "TyresPressure", "value1, value2, value3, value4")
    wear = channel(con, "Tyres Wear", "value1, value2, value3, value4")
    brake_temps = channel(con, "Brakes Temp", "value1, value2, value3, value4")
    behind_next = channel(con, "Time Behind Next")
    lap_times = events(con, "Lap Time")
    tc = events(con, "TCLevel")
    abs_level = events(con, "ABSLevel")
    bias = events(con, "Brake Bias Rear")

    duration = len(speed) / rate_of["Ground Speed"]
    end = start + duration
    session_type = SESSION_TYPES.get(meta.get("SessionType"), 1)
    track = meta.get("TrackName", "unknown")

    os.makedirs(out_folder, exist_ok=True)
    name = os.path.basename(path).replace(".duckdb", "")
    tape_path = os.path.join(
        out_folder, "tape_lmu_" + name.replace(" ", "_") + ".jsonl.gz"
    )
    frames = int(duration * RATE_HZ)
    last_race_second = None
    best = 0.0

    with gzip.open(tape_path, "wt") as out:
        for i in range(frames):
            t = start + i / RATE_HZ

            second = int(t)
            if second != last_race_second:
                last_race_second = second
                laps_done = event_at(laps, t)
                last_lap = event_at(lap_times, t, 0.0)
                if last_lap > 0 and (best == 0 or last_lap < best):
                    best = last_lap
                temp_rows = [
                    [
                        round(
                            value_at(
                                temps[side], rate_of[f"TyresTemp{side}"], t, start
                            )[w],
                            1,
                        )
                        for side in ("Left", "Centre", "Right")
                    ]
                    for w in range(4)
                ]
                me = Me(
                    driver=meta.get("DriverName", ""),
                    car_class=meta.get("CarClass", ""),
                    place=0,
                    grid=0,
                    laps=laps_done,
                    time_behind_next=round(
                        value_at(behind_next, rate_of["Time Behind Next"], t, start), 3
                    ),
                    time_behind_leader=0.0,
                    laps_behind_leader=0,
                    best_lap=round(best, 3),
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
                    fuel=round(value_at(fuel, rate_of["Fuel Level"], t, start), 3),
                    fuel_capacity=0.0,
                    virtual_energy=round(
                        value_at(energy, rate_of["Virtual Energy"], t, start) / 100.0, 4
                    ),
                    battery=0.0,
                    lift_and_coast=0,
                    track_limit_steps=0,
                    gap_car_ahead=0.0,
                    gap_car_behind=0.0,
                    gap_place_ahead=0.0,
                    gap_place_behind=0.0,
                    tyre_temps=temp_rows,
                    tyre_pressures=[
                        round(p, 1)
                        for p in value_at(pressures, rate_of["TyresPressure"], t, start)
                    ],
                    # LMU's export counts tread LEFT (100 = new); Apex counts wear done
                    tyre_wear=[
                        round((100.0 - w) / 100.0, 4)
                        for w in value_at(wear, rate_of["Tyres Wear"], t, start)
                    ],
                    brake_temps=[
                        round(b, 1)
                        for b in value_at(brake_temps, rate_of["Brakes Temp"], t, start)
                    ],
                    compound="",
                    dents=[0] * 8,
                    detached=False,
                    overheating=False,
                    brake_bias_rear=round(event_at(bias, t, 0.0), 4),
                    tc=int(event_at(tc, t, 0)),
                    abs=int(event_at(abs_level, t, 0)),
                    motor_map=0,
                    arb_front=0,
                    arb_rear=0,
                    car_model=meta.get("CarName", ""),
                )
                phase = 8 if t >= end - 1.0 else 5
                session = Session(
                    track=track,
                    session=session_type,
                    game_phase=phase,
                    time_remaining=round(end - t, 1),
                    end_time=round(end, 1),
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
                out.write(
                    json.dumps(
                        asdict(
                            RaceSnapshot(sim_time=round(t, 3), session=session, me=me)
                        )
                    )
                    + "\n"
                )

            wheel_mps = value_at(wheels, rate_of["Wheel Speed"], t, start)
            radii = [FRONT_RADIUS, FRONT_RADIUS, REAR_RADIUS, REAR_RADIUS]
            hits_so_far = bisect.bisect_right(impacts, t)
            last_impact = impacts[hits_so_far - 1] if hits_so_far else None
            frame = CarState(
                speed_kmh=round(value_at(speed, rate_of["Ground Speed"], t, start), 3),
                throttle=round(
                    value_at(throttle, rate_of["Throttle Pos"], t, start) / 100.0, 4
                ),
                brake=round(value_at(brake, rate_of["Brake Pos"], t, start) / 100.0, 4),
                gear=int(event_at(gear_events, t, 0)),
                rpm=round(value_at(rpm, rate_of["Engine RPM"], t, start), 1),
                max_rpm=max_rpm,
                lap_dist=round(value_at(lap_dist, rate_of["Lap Dist"], t, start), 2),
                lap_invalidated=False,
                # Apex keeps wheel rotation in rad/s, like the shared memory, with forward negative
                wheel_rot=[round(-wheel_mps[w] / radii[w], 3) for w in range(4)],
                accel_long=round(
                    value_at(g_long, rate_of["G Force Lat"], t, start) * G, 3
                ),
                accel_lat=round(
                    value_at(g_lat, rate_of["G Force Long"], t, start) * G, 3
                ),
                surface=list(value_at(surfaces, rate_of["SurfaceTypes"], t, start)),
                yaw_rate=0.0,  # no yaw channel in LMU's export (see top)
                elapsed_time=round(t, 4),
                steering=round(
                    value_at(steering, rate_of["Steering Pos Unfiltered"], t, start)
                    / STEERING_FULL_DEG,
                    4,
                ),
                steering_filtered=round(
                    value_at(steering_filtered, rate_of["Steering Pos"], t, start)
                    / STEERING_FULL_DEG,
                    4,
                ),
                last_impact_time=last_impact,
                last_impact_magnitude=1.0 if last_impact is not None else None,
            )
            out.write(json.dumps(asdict(frame)) + "\n")
    con.close()
    return {
        "tape": tape_path,
        "track": track,
        "session": meta.get("SessionType"),
        "car": meta.get("CarName"),
        "laps": len(laps) - 1,
        "minutes": round(duration / 60, 1),
        "setup": meta.get("CarSetup"),
    }


class NoVoice:
    """Imports replay silently and never call the LLM: the lines are not being heard."""

    def phrase(self, call):
        return None, 0, 0, 0.0


def load_into_apex(tape_paths):
    """Replay converted tapes into apex.db, once each, then rebuild the team memory."""
    import contextlib
    import io
    from live_telemetry import run_session
    from memory import connect_db
    from team_memory import build_profile

    conn = connect_db("apex.db")
    already = {row[0] for row in conn.execute("SELECT tape_path FROM sessions")}
    conn.close()
    for tape in tape_paths:
        if tape in already:
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            run_session(True, None, tape, out_loud=False, persona=NoVoice())
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
