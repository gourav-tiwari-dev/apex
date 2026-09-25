"""How good is the closing alarm? For each setting, replay the tapes and check every alarm:
did that car actually get within 0.35 s within 2 minutes, and at the predicted corner?"""
import io, contextlib, os, sys
os.chdir(r"C:\Users\gourav\Downloads\apex_telemetry")
sys.path.insert(0, os.getcwd())
import gaps
import seats.racecraft as rc
from live_telemetry import ReplaySource, corner_at
from seats import Moment
from seats.performance import PerformanceEngineer
from track_map import corners_for_track

TAPES = ["tape_20260924_201632.jsonl.gz", "tape_20260924_202800.jsonl.gz"]


def run(min_per_lap, max_gap):
    rc.ALARM_MIN_PER_LAP_S = min_per_lap
    rc.ALARM_MAX_GAP_S = max_gap
    results = []
    for tape in TAPES:
        with contextlib.redirect_stdout(io.StringIO()):
            src = ReplaySource(None, tape)
            seat = rc.Racecraft(PerformanceEngineer())
            corners = None
            watch = []
            for f in src:
                r = src.race
                if r is not None and corners is None:
                    corners = corners_for_track(r.session.track)
                if r is None or r.me is None:
                    continue
                corner = corner_at(corners, f.lap_dist) if corners else None
                m = Moment(frame=f, race=r, new_race=src.new_race, near=src.near, lap_count=r.me.laps + 1,
                           lap_wrapped=False, corner=corner, track=None, session_type=10, corners=corners)
                for c in seat.update(m):
                    if c.kind in ("CLOSING_ALARM", "CLOSING_ON"):
                        car = seat.behind if c.kind == "CLOSING_ALARM" else seat.ahead
                        watch.append([c.kind, car.id, f.elapsed_time, c.facts.get("catch_corner"), None, None])
                if not src.new_race:
                    continue
                for w in watch:
                    if w[4] is not None:
                        continue
                    if w[0] == "CLOSING_ALARM":
                        g = seat.clock.gap_behind(w[1], f.elapsed_time)
                    else:
                        g = seat.clock.gap_ahead(w[1], f.elapsed_time)
                    if g is not None and g <= 0.35:
                        if w[0] == "CLOSING_ALARM":
                            o = seat.cars.get(w[1])
                            where = corner_at(corners, o.lap_dist) if o else None
                        else:
                            where = corner
                        w[4] = "caught"
                        w[5] = (where, f.elapsed_time - w[2])
                    elif f.elapsed_time - w[2] > 120:
                        w[4] = "never"
            results += watch
    caught = [w for w in results if w[4] == "caught"]
    exact = [w for w in caught if w[5][0] == w[3]]
    return len(results), len(caught), len(exact), results


for min_per_lap in (0.2, 0.4):
    for max_gap in (1.5, 2.0):
        n, caught, exact, results = run(min_per_lap, max_gap)
        print(f"closing >= {min_per_lap} s/lap, gap <= {max_gap}: alarms {n:2d}  came true {caught:2d}  right corner {exact:2d}")
        for w in results:
            actual = f"{w[5][1]:.0f} s later, at {w[5][0]}" if w[4] == "caught" else "never"
            print(f"     {w[0]:14s} predicted {str(w[3]):20s} | actual {actual}")
