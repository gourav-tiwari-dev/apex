"""Setup engineer: between sessions, what to change in the car, and only from evidence.

Ranked races run fixed setups, but brake bias, traction control and ABS can still be
changed from the cockpit, so that is what this seat advises on. Every piece of advice
names the events it rests on; no evidence, no advice. "No change needed" is advice too,
when the laps prove it.

GUESSED directions until checked in the game: a higher TC or ABS number means more
intervention, and brake_bias_rear is the share of braking on the rear axle.
"""
ENOUGH_TO_ACT = 3            # a problem seen fewer times than this is not a setup problem
MIN_LAPS_FOR_ALL_CLEAR = 5   # "the car is fine" needs this many laps to mean anything


def events_of(conn, session_id, kind):
    return conn.execute("SELECT id, corner FROM events WHERE session_id = ? AND kind = ?",
                        (session_id, kind)).fetchall()


def corners_named(rows):
    counts = {}
    for _, corner in rows:
        counts[corner] = counts.get(corner, 0) + 1
    worst = sorted(counts.items(), key=lambda item: -item[1])
    return ", ".join(f"{corner} ({count})" for corner, count in worst[:2])


def advice_for(conn, session_id):
    """A list of {kind, conclusion, facts, evidence}: what the setup engineer would say."""
    laps = conn.execute("SELECT COUNT(*) FROM laps WHERE session_id = ? AND lap_count > 0",
                        (session_id,)).fetchone()[0]
    snaps = events_of(conn, session_id, "REAR_SNAP")
    lockups = events_of(conn, session_id, "LOCKUP")
    spins = events_of(conn, session_id, "WHEELSPIN")
    advice = []

    if len(snaps) >= ENOUGH_TO_ACT:
        advice.append({
            "kind": "BIAS_FORWARD",
            "conclusion": f"The rear snapped under braking {len(snaps)} times, mostly at {corners_named(snaps)}. "
                          f"Move the brake bias 1 click forward, and trail off the brake a little earlier there.",
            "facts": {"snaps": len(snaps), "clicks": 1},
            "evidence": [row[0] for row in snaps]})
    elif len(lockups) >= ENOUGH_TO_ACT:
        advice.append({
            "kind": "ABS_UP",
            "conclusion": f"Front lock-ups {len(lockups)} times, mostly at {corners_named(lockups)}. "
                          f"Go 1 step up on ABS, or bias 1 click rearward.",
            "facts": {"lockups": len(lockups), "steps": 1},
            "evidence": [row[0] for row in lockups]})
    if len(spins) >= ENOUGH_TO_ACT:
        advice.append({
            "kind": "TC_UP",
            "conclusion": f"Wheelspin on exit {len(spins)} times, mostly at {corners_named(spins)}. Go 1 step up on TC.",
            "facts": {"wheelspin": len(spins), "steps": 1},
            "evidence": [row[0] for row in spins]})

    if not advice and laps >= MIN_LAPS_FOR_ALL_CLEAR:
        advice.append({
            "kind": "NO_CHANGE",
            "conclusion": f"{laps} laps with no repeated snaps, lock-ups or wheelspin. The car is fine. No changes.",
            "facts": {"laps": laps},
            "evidence": [],
            "session_evidence": [session_id]})
    return advice
