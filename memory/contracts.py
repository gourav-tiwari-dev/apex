"""The one job for the next race (a focus contract) and whether he did it.

His corners are compared with a reference lap (Monza's hand-checked lap, the fastest car of his
model in the race, or his own best), the corner that costs most becomes the job with a target,
and later sessions grade it: hit, moved, no change, or not enough clean laps yet."""

import json
from statistics import median

from memory.corner_report import corner_report
from memory.db import track_key, track_of


def save_contract(conn, session_id, contract):
    cur = conn.execute(
        "INSERT OR REPLACE INTO focus_contracts (session_id,corner,focus,metric,baseline,target,min_laps) VALUES (?,?,?,?,?,?,?)",
        (
            session_id,
            contract["corner"],
            contract["focus"],
            contract["metric"],
            contract["baseline"],
            contract["target"],
            contract["min_laps"],
        ),
    )
    conn.commit()
    return cur.lastrowid


def load_latest_contract(conn, before_session_id, track=None):
    """The newest job set before this session. With a track, only a job set at that track:
    an Arnage job means nothing at Monza."""
    rows = conn.execute(
        "SELECT session_id,corner,focus,metric,baseline,target,min_laps FROM focus_contracts WHERE session_id < ? ORDER BY session_id DESC",
        (before_session_id,),
    ).fetchall()
    row = None
    for candidate in rows:
        if track is None or track_of(conn, candidate[0]) == track_key(track):
            row = candidate
            break
    if row is None:
        return None
    return {
        "session_id": row[0],
        "corner": row[1],
        "focus": row[2],
        "metric": row[3],
        "baseline": row[4],
        "target": row[5],
        "min_laps": row[6],
    }


def load_reference(path):
    with open(path, "r") as f:
        data = json.load(f)
        return data


def reference_of(reference):
    # a reference is a file (the Monza hand-checked lap) or a dict built from a race
    if isinstance(reference, str):
        return load_reference(reference)
    return reference


def compare_to_reference(conn, reference_path, session_id):
    report = corner_report(conn, session_id)
    reference_report = reference_of(reference_path)
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

        time_lost = zone / (your_min_speed / 3.6) - zone / (ref_min_speed / 3.6)
        gap = your_min_speed - ref_min_speed
        # no reference brake point on tracks referenced by another car: not measured, never guessed
        braking_gap = None
        if ref_brake is not None and brake is not None:
            braking_gap = ref_brake - brake
        comparison.append(
            {
                "corner": current_corner,
                "yours": your_min_speed,
                "ref": ref_min_speed,
                "gap": gap,
                "confidence": reference[current_corner]["confidence"],
                "time_lost": time_lost,
                "slow_zone": zone,
                "coast": coast,
                "brake_point": brake,
                "technique": technique,
                "braking_difference": braking_gap,
            }
        )

    return comparison


def time_lost_of(row):
    return row["time_lost"]


def rank_by_time_lost(comparison):
    comparison.sort(key=time_lost_of, reverse=True)
    return comparison


def build_evidence_pack(conn, reference, session_id):
    reference_report = reference_of(reference)
    ranked = rank_by_time_lost(compare_to_reference(conn, reference, session_id))
    corners = []
    for row in ranked:
        corners.append(
            {
                "corner": row["corner"],
                "time_lost_s": round(row["time_lost"], 2),
                "your_min_kmh": round(row["yours"], 1),
                "hymo_min_kmh": row["ref"],
                "gap_kmh": round(row["gap"], 1),
                "your_slow_zone_m": round(row["slow_zone"], 1),
                "your_coast_m": round(row["coast"], 1),
                "hymo_technique": row["technique"],
                "braking_pt_difference_m": None
                if row["braking_difference"] is None
                else round(row["braking_difference"], 1),
            }
        )

    pack = {
        "driver": {
            "input": "controller",
            "car": "BMW M4 LMGT3",
            "session_type": "race",
        },
        "reference": {
            "source": reference_report["source"],
            "note": "min speeds checked on the HUD; technique text extracted by Gemini, unverified",
            "brake_point_note": reference_report["brake_point_note"],
        },
        "focus": corners[0]["corner"],
        "corners": corners,
    }
    return pack


REFERENCE_LAPS = 2  # one lap of another car is not a reference


def reference_from_race(conn, session_id, car_class=None, car_model=None):
    """For tracks with no hand-checked reference lap: the fastest car of MY MODEL in this race
    (else of my class), corner by corner (its median min speed over its laps). A Porsche and
    a BMW take a hairpin differently (23 Sep). None if nobody qualifies."""
    rows = conn.execute(
        "SELECT driver, car_class, car_model, corner, min_speed FROM opponent_corners WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    same_model = []
    for row in rows:
        if car_model is not None and row[2] == car_model:
            same_model.append(row)
    pool = rows
    if same_model:
        pool = same_model
    speeds = {}
    for driver, row_class, row_model, corner, speed in pool:
        if car_class is not None and row_class != car_class:
            continue
        speeds.setdefault((corner, driver), []).append(speed)
    corners = {}
    drivers = set()
    for (corner, driver), values in speeds.items():
        if len(values) < REFERENCE_LAPS:
            continue
        typical = round(median(values), 1)
        if corner not in corners or typical > corners[corner]["min_speed"]:
            corners[corner] = {
                "min_speed": typical,
                "brake_point": None,
                "technique": "",
                "confidence": f"{driver}'s own telemetry, 5 Hz, good to 1-2 km/h",
            }
            drivers.add(driver)
    if not corners:
        return None
    return {
        "source": "the fastest car in your class in this race: "
        + ", ".join(sorted(drivers)),
        "brake_point_note": "not measured on this track",
        "corners": corners,
    }


def reference_from_self(conn, track):
    """Last resort, for a track with no reference lap and no other cars recorded (LMU's own
    telemetry files): your own best, corner by corner, across every drive there. The best is
    the 90th percentile of your min speeds, so one freak lap does not set it."""
    rows = conn.execute(
        "SELECT cs.corner, cs.min_speed FROM corner_stats cs JOIN sessions s ON s.id = cs.session_id "
        "WHERE s.track = ? AND cs.lap_count > 0 AND cs.min_speed IS NOT NULL",
        (track,),
    ).fetchall()
    speeds = {}
    for corner, speed in rows:
        speeds.setdefault(corner, []).append(speed)
    corners = {}
    for corner, values in speeds.items():
        if len(values) < 5:
            continue
        values.sort()
        best = values[int(len(values) * 0.9) - 1]
        corners[corner] = {
            "min_speed": round(best, 1),
            "brake_point": None,
            "technique": "",
            "confidence": f"your own best (90th percentile of {len(values)} laps)",
        }
    if not corners:
        return None
    return {
        "source": "your own best laps at this track (no faster car recorded here yet)",
        "brake_point_note": "not measured on this track",
        "corners": corners,
    }


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
        "corner": focus_corner["corner"],
        "focus": spoken,
        "metric": "min_speed",
        "baseline": round(baseline, 1),
        "target": round(target, 1),
        "min_laps": CONTRACT_MIN_LAPS,
    }
    return contract


def load_contract_laps(conn, session_id, corner):
    cur = conn.execute(
        "SELECT cs.min_speed FROM corner_stats cs JOIN laps l ON l.session_id = cs.session_id AND l.lap_count = cs.lap_count WHERE cs.session_id = ? AND cs.corner = ? AND cs.lap_count > 0 AND l.validity = 1",
        (session_id, corner),
    )
    return [row[0] for row in cur.fetchall()]


def contract_laps_so_far(conn, contract, session_id):
    """Clean laps at the job's corner in EVERY session at that track since the job was set, up
    to this one. A Le Mans daily race is 3-6 laps (24 Sep), so one race alone could never reach
    8 and every job read "insufficient" for ever."""
    track = track_of(conn, contract["session_id"])
    speeds = []
    sessions = conn.execute(
        "SELECT id FROM sessions WHERE id > ? AND id <= ? ORDER BY id",
        (contract["session_id"], session_id),
    ).fetchall()
    for (later_session,) in sessions:
        if track_of(conn, later_session) == track:
            speeds.extend(load_contract_laps(conn, later_session, contract["corner"]))
    return speeds


def evaluate_contract(conn, contract, session_id):
    speeds = contract_laps_so_far(conn, contract, session_id)

    if len(speeds) < contract["min_laps"]:
        return {"verdict": "insufficient", "laps": len(speeds)}

    result = round(median(speeds), 1)
    gain = round(result - contract["baseline"], 1)

    if result >= contract["target"]:
        return {"verdict": "hit", "laps": len(speeds), "result": result, "gain": gain}

    if gain >= MIN_TARGET_STEP_KMH:
        return {"verdict": "moved", "laps": len(speeds), "result": result, "gain": gain}

    return {"verdict": "flat", "laps": len(speeds), "result": result, "gain": gain}
