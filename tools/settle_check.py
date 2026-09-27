import sys

sys.path.insert(0, r"C:\Users\gourav\Downloads\apex_telemetry")
import os

os.chdir(r"C:\Users\gourav\Downloads\apex_telemetry")
from live_telemetry import ReplaySource
from seats import Moment
from seats.settle import RaceSettle, neighbourhood

for tape in sys.argv[1:]:
    src = ReplaySource(None, tape)
    settle = RaceSettle()
    green_at = None
    last_phase = None
    alongside = 0
    swaps = 0
    last_n = None
    for frame in src:
        race = src.race
        if race is None or race.me is None:
            continue
        ph = race.session.game_phase
        if ph != last_phase:
            print(
                f"  t={frame.elapsed_time:7.1f} phase {last_phase}->{ph} session={race.session.session} lap={race.me.laps} P{race.me.place}"
            )
            last_phase = ph
            if ph == 5 and green_at is None:
                green_at = frame.elapsed_time
        m = Moment(
            frame=frame,
            race=race,
            new_race=src.new_race,
            near=src.near,
            lap_count=race.me.laps + 1,
            lap_wrapped=False,
            corner=None,
            session_type=race.session.session,
        )
        if green_at is not None and not settle.settled and src.new_race:
            n = neighbourhood(race)
            if last_n is not None and n != last_n:
                swaps += 1
            last_n = n
        was = settle.settled
        for c in settle.update(m):
            print(
                f"  SETTLED at t={frame.elapsed_time:.1f} ({frame.elapsed_time - green_at:.0f} s after green), lap {m.lap_count}, swaps seen {swaps}: {c.template}"
            )
        if was and not settle.settled:
            print(f"  chaos starts t={frame.elapsed_time:.1f}")
    print(tape, "done")
