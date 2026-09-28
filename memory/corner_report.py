"""The corner report: per corner, his typical braking point, slowest speed, slow zone and
coasting over a session, with how much they spread, and the incidents there. Printed after
every session, and the base of the debrief's evidence."""

from statistics import median


def load_corner_rows(conn, session_id):
    # session_id None means every session
    if session_id is None:
        cur = conn.execute(
            "SELECT corner,lap_count,brake_onset,min_speed,slow_zone,coast FROM corner_stats WHERE lap_count > 0"
        )
    else:
        cur = conn.execute(
            "SELECT corner,lap_count,brake_onset,min_speed,slow_zone,coast FROM corner_stats WHERE session_id = ? AND lap_count > 0",
            (session_id,),
        )
    return cur.fetchall()


def load_incident_counts(conn, session_id):
    # session_id None means every session
    if session_id is None:
        cur = conn.execute(
            "SELECT corner,COUNT(*) FROM events WHERE kind IN ('OFF_TRACK','SPIN','LOCKUP') AND lap_count > 0 GROUP BY corner"
        )
    else:
        cur = conn.execute(
            "SELECT corner,COUNT(*) FROM events WHERE session_id = ? AND kind IN ('OFF_TRACK','SPIN','LOCKUP') AND lap_count > 0 GROUP BY corner",
            (session_id,),
        )

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


def corner_report(conn, session_id=None):
    """Per-corner report card. Median is the typical lap, spread is how repeatable you are.
    Sorted by brake-point spread: the least repeatable corner comes first."""
    rows = load_corner_rows(conn, session_id)
    incident_counts = load_incident_counts(conn, session_id)

    # group the laps by corner: {"T1 Rettifilo": [(brake_onset, min_speed), ...], ...}
    laps_by_corner = {}
    for row in rows:
        corner, lap_count, brake_onset, min_speed, slow_zone, coast = row
        if corner not in laps_by_corner:
            laps_by_corner[corner] = []
        laps_by_corner[corner].append((brake_onset, min_speed, slow_zone, coast))

    report = []
    for corner in laps_by_corner:
        laps = laps_by_corner[corner]

        # a lap with no braking (T3 is taken flat) has brake_onset None - leave it out
        onsets = []
        speeds = []
        zones = []
        coasts = []
        for brake_onset, min_speed, slow_zone, coast in laps:
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

        report.append(
            {
                "corner": corner,
                "laps": len(laps),
                "onset_median": middle_value(onsets),
                "onset_spread": spread(onsets),
                "speed_median": middle_value(speeds),
                "speed_spread": spread(speeds),
                "incidents": incidents,
                "zone_median": middle_value(zones),
                "coast_median": middle_value(coasts),
            }
        )

    with_spread = []
    without_spread = []
    for corner_row in report:
        if corner_row["onset_spread"] is None:
            without_spread.append(corner_row)
        else:
            with_spread.append(corner_row)
    with_spread.sort(key=onset_spread_of, reverse=True)

    return with_spread + without_spread


def format_number(value, width):
    if value is None:
        text = "--"
    else:
        text = f"{value:.1f}"
    return text.rjust(width)


def print_corner_report(conn, session_id=None):
    report = corner_report(conn, session_id)
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
        line += format_number(corner_row["onset_median"], 10)
        line += format_number(corner_row["onset_spread"], 8)
        line += format_number(corner_row["speed_median"], 9)
        line += format_number(corner_row["speed_spread"], 8)
        line += str(corner_row["incidents"]).rjust(5)
        print(line)
    print()
