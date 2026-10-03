"""The open beta's inbox (1 Oct 2026): pulls new feedback and tapes from the door's FEEDBACK store.

    python server/inbox.py            list feedback, newest first, and download any new tapes
    python server/inbox.py --replay   also replay each downloaded tape through Apex (no sound),
                                      so what the tester heard can be checked line by line

Uses wrangler (logged in once on this laptop). Everything lands in server/inbox/ (not committed).
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(HERE, "inbox")


def wrangler(*args, binary=False):
    """Runs a `wrangler kv` command on the door's FEEDBACK store and returns its output
    (bytes when binary). A failed command stops the inbox with wrangler's error."""
    out = subprocess.run(
        ["npx", "wrangler", "kv", *args, "--binding", "FEEDBACK", "--remote"],
        cwd=HERE,
        capture_output=True,
        shell=True,
    )
    if out.returncode != 0:
        raise SystemExit(out.stderr.decode("utf-8", "ignore"))
    return out.stdout if binary else out.stdout.decode("utf-8")


def main():
    """Pulls every tester's feedback into server/inbox/ (one line printed each) and
    their tape when they sent one; --replay plays each new tape through Apex."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true")
    args = ap.parse_args()
    os.makedirs(INBOX, exist_ok=True)
    keys = [k["name"] for k in json.loads(wrangler("key", "list"))]
    feedback = sorted((k for k in keys if k.startswith("feedback:")), reverse=True)
    for key in feedback:
        item = json.loads(wrangler("key", "get", key))
        race = item.get("race", {})
        print(
            f"{key[9:]}  {item.get('rating')}/5  {race.get('track')}  {race.get('car_class')}  "
            f"P{race.get('grid')}->P{race.get('final_place')}  {item.get('comment', '')[:80]!r}"
        )
        with open(
            os.path.join(INBOX, key.replace(":", "_") + ".json"), "w", encoding="utf-8"
        ) as f:
            json.dump(item, f, indent=1)
        tape_key = "tape:" + key[9:]
        tape_file = os.path.join(INBOX, tape_key.replace(":", "_") + ".jsonl.gz")
        if tape_key in keys and not os.path.exists(tape_file):
            with open(tape_file, "wb") as f:
                f.write(wrangler("key", "get", tape_key, binary=True))
            print(f"   tape -> {tape_file}")
            if args.replay:
                sys.path.insert(0, os.path.dirname(HERE))
                from session import run_replay

                run_replay(tape_file, out_loud=False, clean=True)


if __name__ == "__main__":
    main()
