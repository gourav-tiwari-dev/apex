"""TEAM MEMORY: what Apex knows about how Gourav drives, across every drive.

His rules (notion_apex.md section 8.1, and "don't make stuff up on ur own"):
  - a fact exists only with evidence: every fact links to the events or sessions it came from
  - nothing is a habit from one bad lap: a habit needs HABIT_MIN occurrences in at least
    HABIT_MIN_DRIVES separate drives
  - one drive counts once: a tape replayed ten times is still one drive

Everything here is rebuilt from the database by build_profile(). Like corner_stats, it is a
cache: safe to delete, and the next build puts it back.
"""

HABIT_MIN = 3
HABIT_MIN_DRIVES = 2
INCIDENT_KINDS = ("OFF_TRACK", "SPIN", "LOCKUP")
RACE_SESSIONS = (10, 11, 12, 13)
# GUESSED: sessions recorded before v2 have no track name; all of them were driven at Monza
LEGACY_TRACK = "Monza"

KIND_WORDS = {
    "OFF_TRACK": "off track",
    "SPIN": "rear stepped out",
    "LOCKUP": "locked up",
    "CONTACT": "contact",
}


def drives(conn):
    """One session per tape: the latest one that ran to an end, or for sessions recorded
    before end reasons existed, the one that got furthest (most laps, then most events)
    and only if it completed at least one lap."""
    chosen = []
    tapes = conn.execute("SELECT DISTINCT tape_path FROM sessions").fetchall()
    for (tape,) in tapes:
        finished = conn.execute(
            "SELECT id FROM sessions WHERE tape_path = ? AND end_reason IS NOT NULL ORDER BY id DESC LIMIT 1",
            (tape,),
        ).fetchone()
        if finished is not None:
            chosen.append(finished[0])
            continue
        legacy = conn.execute(
            "SELECT s.id, "
            "(SELECT COUNT(*) FROM laps l WHERE l.session_id = s.id) AS laps, "
            "(SELECT COUNT(*) FROM events e WHERE e.session_id = s.id) AS events "
            "FROM sessions s WHERE s.tape_path = ? ORDER BY laps DESC, events DESC, s.id DESC LIMIT 1",
            (tape,),
        ).fetchone()
        # an old session only counts as a drive if a lap was completed: session 1 was a
        # 36-byte test tape with 3 events and no laps (a pre-fix run, July 2026)
        if legacy is not None and legacy[1] > 0:
            chosen.append(legacy[0])
    return sorted(chosen)


def track_of(conn, session_id):
    row = conn.execute(
        "SELECT track FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None or not row[0]:
        return LEGACY_TRACK
    return row[0]


def save_fact(
    conn,
    kind,
    track,
    subject,
    value,
    occurrences,
    drive_count,
    summary,
    event_ids=(),
    session_ids=(),
):
    if not event_ids and not session_ids:
        raise ValueError(f"a fact needs evidence: {summary}")
    cur = conn.execute(
        "INSERT INTO profile_facts (kind, track, subject, value, occurrences, drives, summary) "
        "VALUES (?,?,?,?,?,?,?)",
        (kind, track, subject, value, occurrences, drive_count, summary),
    )
    fact_id = cur.lastrowid
    for event_id in event_ids:
        conn.execute(
            "INSERT INTO profile_evidence (fact_id, event_id) VALUES (?,?)",
            (fact_id, event_id),
        )
    for session_id in session_ids:
        conn.execute(
            "INSERT INTO profile_evidence (fact_id, session_id) VALUES (?,?)",
            (fact_id, session_id),
        )
    return fact_id


def events_in(conn, session_ids, kinds):
    if not session_ids:
        return []
    marks = ",".join("?" * len(session_ids))
    kind_marks = ",".join("?" * len(kinds))
    return conn.execute(
        f"SELECT id, session_id, kind, corner, lap_count, other_car FROM events "
        f"WHERE session_id IN ({marks}) AND kind IN ({kind_marks}) ORDER BY id",
        list(session_ids) + list(kinds),
    ).fetchall()


def corner_habits(conn, drive_ids):
    """Incidents that keep happening at the same corner."""
    groups = {}
    for event_id, session_id, kind, corner, lap, other in events_in(
        conn, drive_ids, INCIDENT_KINDS + ("CONTACT",)
    ):
        if corner is None or corner == "the straight":
            continue
        key = (track_of(conn, session_id), corner, kind)
        if key not in groups:
            groups[key] = []
        groups[key].append((event_id, session_id))
    made = 0
    for (track, corner, kind), found in groups.items():
        sessions = {session_id for _, session_id in found}
        if len(found) < HABIT_MIN or len(sessions) < HABIT_MIN_DRIVES:
            continue
        summary = f"{KIND_WORDS[kind]} at {corner}: {len(found)} times in {len(sessions)} drives"
        fact_kind = "contact_corner" if kind == "CONTACT" else "corner_habit"
        save_fact(
            conn,
            fact_kind,
            track,
            corner,
            len(found),
            len(found),
            len(sessions),
            summary,
            event_ids=[event_id for event_id, _ in found],
        )
        made += 1
    return made


def race_ids(conn, drive_ids):
    races = []
    for session_id in drive_ids:
        row = conn.execute(
            "SELECT session_type FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is not None and row[0] in RACE_SESSIONS:
            races.append(session_id)
    return races


def lap_one(conn, drive_ids):
    """Trouble on the first lap of races: where close racing is at its closest."""
    races = race_ids(conn, drive_ids)
    if len(races) < HABIT_MIN_DRIVES:
        return 0
    trouble = {}
    for event_id, session_id, kind, corner, lap, other in events_in(
        conn, races, INCIDENT_KINDS + ("CONTACT",)
    ):
        if lap <= 1:
            if session_id not in trouble:
                trouble[session_id] = []
            trouble[session_id].append(event_id)
    if len(trouble) < HABIT_MIN_DRIVES:
        return 0
    summary = f"lap 1 trouble (contact, off or spin) in {len(trouble)} of your last {len(races)} races"
    event_ids = [event_id for ids in trouble.values() for event_id in ids]
    save_fact(
        conn,
        "lap_one",
        None,
        "me",
        len(trouble) / len(races),
        len(event_ids),
        len(trouble),
        summary,
        event_ids=event_ids,
    )
    return 1


def rivals(conn, drive_ids):
    """Everyone raced against, with how it went. One race together is already a fact."""
    if not drive_ids:
        return 0
    marks = ",".join("?" * len(drive_ids))
    rows = conn.execute(
        f"SELECT r.steam_id, r.driver, r.session_id, r.final_place, s.final_place "
        f"FROM rivals_seen r JOIN sessions s ON s.id = r.session_id "
        f"WHERE r.session_id IN ({marks}) ORDER BY r.session_id",
        list(drive_ids),
    ).fetchall()
    by_rival = {}
    for steam_id, driver, session_id, their_place, my_place in rows:
        if steam_id not in by_rival:
            by_rival[steam_id] = {"driver": driver, "sessions": [], "ahead": 0}
        by_rival[steam_id]["driver"] = driver
        by_rival[steam_id]["sessions"].append(session_id)
        if my_place is not None and their_place is not None and my_place < their_place:
            by_rival[steam_id]["ahead"] += 1
    made = 0
    for steam_id, rival in by_rival.items():
        contact_ids = [
            row[0]
            for row in conn.execute(
                f"SELECT id FROM events WHERE kind = 'CONTACT' AND other_car = ? AND session_id IN ({marks})",
                [steam_id] + list(drive_ids),
            ).fetchall()
        ]
        races = len(rival["sessions"])
        summary = f"{rival['driver']}: raced {races} times, you finished ahead {rival['ahead']}"
        if contact_ids:
            summary += f", contact {len(contact_ids)} times"
        save_fact(
            conn,
            "rival",
            None,
            steam_id,
            rival["ahead"],
            races,
            races,
            summary,
            event_ids=contact_ids,
            session_ids=rival["sessions"],
        )
        made += 1
    return made


def clean_races(conn, drive_ids):
    """How clean each race was: contacts, walls, offs, spins, track-limit steps."""
    made = 0
    for session_id in race_ids(conn, drive_ids):
        counts = {}
        for kind in ("CONTACT", "IMPACT", "OFF_TRACK", "SPIN"):
            counts[kind] = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id = ? AND kind = ?",
                (session_id, kind),
            ).fetchone()[0]
        strikes = (
            conn.execute(
                "SELECT track_limit_strikes FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]
            or 0
        )
        marks = (
            counts["CONTACT"]
            + counts["IMPACT"]
            + counts["OFF_TRACK"]
            + counts["SPIN"]
            + strikes
        )
        summary = (
            f"race {session_id}: {counts['CONTACT']} contacts with a known car, "
            f"{counts['IMPACT']} other impacts (a wall, or a car Apex could not see), "
            f"{counts['OFF_TRACK']} offs, {counts['SPIN']} spins, {strikes} track-limit steps"
        )
        save_fact(
            conn,
            "clean_race",
            track_of(conn, session_id),
            f"race {session_id}",
            marks,
            marks,
            1,
            summary,
            session_ids=[session_id],
        )
        made += 1
    return made


def pass_attempts(conn, drive_ids):
    """The hasty-commit habit, measured: how many of my pass attempts ended in contact."""
    if not drive_ids:
        return 0
    marks = ",".join("?" * len(drive_ids))
    rows = conn.execute(
        f"SELECT session_id, outcome FROM pass_attempts WHERE session_id IN ({marks})",
        list(drive_ids),
    ).fetchall()
    if len(rows) < HABIT_MIN:
        return 0
    contacts = sum(1 for _, outcome in rows if outcome == "contact")
    passes = sum(1 for _, outcome in rows if outcome == "pass")
    sessions = sorted({session_id for session_id, _ in rows})
    summary = (
        f"pass attempts: {len(rows)}, {passes} passes, {contacts} ended in contact"
    )
    save_fact(
        conn,
        "pass_attempts",
        None,
        "me",
        contacts / len(rows),
        len(rows),
        len(sessions),
        summary,
        session_ids=sessions,
    )
    return 1


def build_profile(conn):
    """Throw the old profile away and rebuild it from every drive. Returns what was found."""
    conn.execute("DELETE FROM profile_evidence")
    conn.execute("DELETE FROM profile_facts")
    drive_ids = drives(conn)
    found = {
        "drives": len(drive_ids),
        "corner_habits": corner_habits(conn, drive_ids),
        "lap_one": lap_one(conn, drive_ids),
        "rivals": rivals(conn, drive_ids),
        "clean_races": clean_races(conn, drive_ids),
        "pass_attempts": pass_attempts(conn, drive_ids),
    }
    conn.commit()
    return found


# ---- what each seat may read -------------------------------------------------------------


def facts(conn, kind, track=None):
    if track is None:
        rows = conn.execute(
            "SELECT id, subject, value, summary FROM profile_facts WHERE kind = ? ORDER BY value DESC",
            (kind,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, subject, value, summary FROM profile_facts WHERE kind = ? AND track = ? ORDER BY value DESC",
            (kind, track),
        ).fetchall()
    return [
        {"fact_id": r[0], "subject": r[1], "value": r[2], "summary": r[3]} for r in rows
    ]


def habits_at(conn, track, corner):
    """For in-race recall: what keeps happening at this corner."""
    return [
        f
        for f in facts(conn, "corner_habit", track)
        + facts(conn, "contact_corner", track)
        if f["subject"] == corner
    ]


def rival(conn, steam_id):
    found = [f for f in facts(conn, "rival") if f["subject"] == str(steam_id)]
    if not found:
        return None
    return found[0]


def brief_facts(conn, track):
    """What the team-memory seat brings to the pre-race brief, most important first."""
    return (
        facts(conn, "lap_one")
        + facts(conn, "pass_attempts")
        + facts(conn, "contact_corner", track)
        + facts(conn, "corner_habit", track)
    )


def clean_race_trend(conn, last=5):
    rows = conn.execute(
        "SELECT id, subject, value, summary FROM profile_facts WHERE kind = 'clean_race' ORDER BY id DESC LIMIT ?",
        (last,),
    ).fetchall()
    return [
        {"fact_id": r[0], "subject": r[1], "value": r[2], "summary": r[3]} for r in rows
    ]


if __name__ == "__main__":
    from memory import connect_db

    conn = connect_db("apex.db")
    found = build_profile(conn)
    print(f"team memory rebuilt from {found['drives']} drives")
    rows = conn.execute(
        "SELECT kind, track, summary FROM profile_facts ORDER BY kind, id"
    ).fetchall()
    if not rows:
        print("  nothing meets the evidence bar yet - it needs more drives")
    for kind, track, summary in rows:
        print(f"  {kind:14s} {track or '':8s} {summary}")
    conn.close()
