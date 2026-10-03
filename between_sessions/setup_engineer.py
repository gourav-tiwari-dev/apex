"""Setup engineer: between sessions, what to change in the car, and only from evidence.

Ranked races run fixed setups, but brake bias, traction control and ABS can still be
changed from the cockpit, so that is what this seat advises on. Every piece of advice
names the events it rests on; no evidence, no advice. "No change needed" is advice too,
when the laps prove it.

GUESSED directions until checked in the game: a higher TC or ABS number means more
intervention, and brake_bias_rear is the share of braking on the rear axle.
"""

import sqlite3

ENOUGH_TO_ACT = 3  # a problem seen fewer times than this is not a setup problem
MIN_LAPS_FOR_ALL_CLEAR = 5  # "the car is fine" needs this many laps to mean anything
# What an incident causes says nothing about the car. Replay of 25 Sep night (27 Sep): punted at
# the Porsche Curves, and that one hit made 2 of the 6 snaps, a lock-up and the rejoin wheelspin
# behind "move the brake bias forward". Events before his own spin still count: the snap is the car.
AFTER_HIT_S = (
    15.0  # a car or a wall: the car was upset and off line this long (spun 4.6 s after)
)
AFTER_SPIN_S = 10.0  # rejoining from a stop spins the wheels whatever the setup


def incident_windows(conn, session_id):
    """[(start, end)] of the seconds after every hit and every spin: wheelspin and
    lock-ups in them come from the incident, not from the setup."""
    windows = []
    for (sim_time,) in conn.execute(
        "SELECT sim_time FROM events WHERE session_id = ? AND kind IN ('CONTACT', 'IMPACT')",
        (session_id,),
    ):
        windows.append((sim_time, sim_time + AFTER_HIT_S))
    for (sim_time,) in conn.execute(
        "SELECT sim_time FROM events WHERE session_id = ? AND kind = 'SPIN'",
        (session_id,),
    ):
        windows.append((sim_time, sim_time + AFTER_SPIN_S))
    return windows


def events_of(conn, session_id, kind):
    """The events of this kind that say something about the car: none an incident caused."""
    windows = incident_windows(conn, session_id)
    rows = []
    for event_id, corner, sim_time in conn.execute(
        "SELECT id, corner, sim_time FROM events WHERE session_id = ? AND kind = ?",
        (session_id, kind),
    ):
        caused = False
        for start, end in windows:
            if start <= sim_time <= end:
                caused = True
                break
        if not caused:
            rows.append((event_id, corner))
    return rows


def step_words(now):
    """ ", 4 to 5" when the setting he ran is known, nothing otherwise."""
    if now is None:
        return ""
    return f", {now} to {now + 1}"


def corners_named(rows):
    """The two corners where these events happened most, with their counts: "Arnage (4),
    Indianapolis (2)"."""
    counts = {}
    for _, corner in rows:
        counts[corner] = counts.get(corner, 0) + 1
    worst = sorted(counts.items(), key=lambda item: -item[1])
    return ", ".join(f"{corner} ({count})" for corner, count in worst[:2])


def settings_of(conn, session_id):
    """(TC, ABS) he ran in this session, or (None, None) before 27 Sep, when they were not kept."""
    try:
        row = conn.execute(
            "SELECT traction_control, abs FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None, None  # an apex.db older than the columns, opened read-only
    if row is None:
        return None, None
    return row[0], row[1]


def advice_for(conn, session_id):
    """A list of {kind, conclusion, facts, evidence}: what the setup engineer would say."""
    laps = conn.execute(
        "SELECT COUNT(*) FROM laps WHERE session_id = ? AND lap_count > 0",
        (session_id,),
    ).fetchone()[0]
    snaps = events_of(conn, session_id, "REAR_SNAP")
    lockups = events_of(conn, session_id, "LOCKUP")
    spins = events_of(conn, session_id, "WHEELSPIN")
    tc_now, abs_now = settings_of(conn, session_id)
    advice = []
    braking = braking_advice(snaps, lockups, abs_now)
    if braking is not None:
        advice.append(braking)
    if len(spins) >= ENOUGH_TO_ACT:
        advice.append(traction_advice(spins, tc_now))
    if not advice and laps >= MIN_LAPS_FOR_ALL_CLEAR:
        advice.append(
            {
                "kind": "NO_CHANGE",
                "conclusion": f"{laps} laps with no repeated snaps, lock-ups or wheelspin. The car is fine. No changes.",
                "facts": {"laps": laps},
                "evidence": [],
                "session_evidence": [session_id],
            }
        )
    return advice


def braking_advice(snaps, lockups, abs_now):
    """The rear snapping under braking: bias forward. Else front lock-ups: ABS up (or bias
    rearward). None when neither happened often enough."""
    if len(snaps) >= ENOUGH_TO_ACT:
        return {
            "kind": "BIAS_FORWARD",
            "conclusion": f"The rear snapped under braking {len(snaps)} times, mostly at {corners_named(snaps)}. "
            f"Move the brake bias 1 click forward, and trail off the brake a little earlier there.",
            "facts": {"snaps": len(snaps), "clicks": 1},
            "evidence": [row[0] for row in snaps],
        }
    if len(lockups) >= ENOUGH_TO_ACT:
        return {
            "kind": "ABS_UP",
            "conclusion": f"Front lock-ups {len(lockups)} times, mostly at {corners_named(lockups)}. "
            f"Go 1 step up on ABS{step_words(abs_now)}, or bias 1 click rearward.",
            "facts": {"lockups": len(lockups), "steps": 1, "abs_now": abs_now},
            "evidence": [row[0] for row in lockups],
        }
    return None


def traction_advice(spins, tc_now):
    """Wheelspin on exit, often: one step up on TC."""
    return {
        "kind": "TC_UP",
        "conclusion": f"Wheelspin on exit {len(spins)} times, mostly at {corners_named(spins)}. "
        f"Go 1 step up on TC{step_words(tc_now)}.",
        "facts": {"wheelspin": len(spins), "steps": 1, "tc_now": tc_now},
        "evidence": [row[0] for row in spins],
    }
