"""The numbers behind a race, for the clip's overlays (1 Oct 2026).

    python -m clips.race_data TAPE [--out race_data.json]

What the short shows on screen has to be what Apex measured, not a guess: so the tape is played
back through Apex itself and racecraft's own numbers are read every 0.1 s of sim time:
  gap_ahead   the same-point gap to the car ahead (what the attack plan and the praise use)
  gap_behind  the same to the car behind
  place       his position in the race
  speed_kmh   his speed (the clip shows the gap in metres: gap x speed)
Run it in a scratch folder: a replay writes a session into ./apex.db (the dev wrapper below does
that for you).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

EVERY_S = 0.1


def sample(tape_path):
    """[(sim_time, gap_ahead, gap_behind, place)] for the whole tape. Call from a scratch folder."""
    from session import Session

    session = Session(
        True,
        None,
        tape_path,
        out_loud=False,
        clean=True,
        launch_id=None,
        voice=None,
        script=None,
    )
    samples = []
    one_frame = session.one_frame
    last = [-1.0]

    def watched(frame):
        ended = one_frame(frame)
        now = frame.elapsed_time
        if now - last[0] >= EVERY_S:
            last[0] = now
            race = session.source.race
            place = race.me.place if race is not None and race.me is not None else None
            racecraft = session.racecraft
            samples.append(
                [
                    round(now, 2),
                    racecraft.gap_ahead,
                    getattr(racecraft, "gap_behind", None),
                    place,
                    round(frame.speed_kmh, 1),
                ]
            )
        return ended

    session.one_frame = watched
    session.run()
    return samples


def write(tape_path, out):
    """Samples the tape in a scratch folder (so no apex.db is touched) and writes out."""
    apex = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if getattr(sys, "frozen", False):
        # the installed app has no separate Python to launch: measure in this process, in a
        # scratch folder, and come back
        here = os.getcwd()
        with tempfile.TemporaryDirectory() as scratch:
            os.chdir(scratch)
            try:
                rows = sample(os.path.join(here, tape_path))
            finally:
                os.chdir(here)
        with open(out, "w") as f:
            json.dump(rows, f)
        return out
    with tempfile.TemporaryDirectory() as scratch:
        code = (
            f"import json, sys; sys.path.insert(0, {apex!r}); from clips.race_data import sample; "
            f"json.dump(sample({os.path.abspath(tape_path)!r}), open({os.path.abspath(out)!r}, 'w'))"
        )
        subprocess.run(
            [sys.executable, "-c", code],
            cwd=scratch,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tape")
    ap.add_argument("--out")
    args = ap.parse_args()
    out = (
        args.out
        or os.path.splitext(os.path.splitext(args.tape)[0])[0] + ".race_data.json"
    )
    write(args.tape, out)
    rows = json.load(open(out))
    print(f"[clips] {len(rows)} samples, sim {rows[0][0]}-{rows[-1][0]} s -> {out}")


if __name__ == "__main__":
    main()
