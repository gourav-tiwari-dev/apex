"""Open beta (1 Oct 2026): every tester's race can come back to us as a rating, a comment and,
if they tick the box, the race's tape, which replays here exactly as it happened.

Gourav's idea: "if i release a testing model online and ask people for improvements... we can
get a better app which testing by me alone would never be possible". The tape is what makes
that work: a bug a tester heard on the radio can be replayed and fixed on this laptop.

Only on a beta install: server.json carries the Apex door's address (the installer writes it).
  ensure_registered()   first start: ask the door for this install's own token, save it
  ask_after_race(...)   after a finished race: a small window, 1-5, a comment, "send my tape"
  send_feedback(...)    the rating and a summary; the tape only when ticked, with every
                        driver's name replaced first (the other drivers never agreed to it)
"""

import gzip
import io
import json
import os
import urllib.parse
import urllib.request

from coach.llm import SERVER_FILE

APP_VERSION = "0.1.0-beta"
TAPE_LIMIT = 25 * 1024 * 1024  # the door refuses bigger


def read_door(path=SERVER_FILE):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def post(url, body, token=None, raw=False, timeout=30):
    data = body if raw else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("content-type", "application/gzip" if raw else "application/json")
    # Cloudflare refuses Python's default "Python-urllib" signature with error 1010 (found 1 Oct
    # testing the installed beta: every tester's registration would have failed)
    request.add_header("user-agent", f"Apex/{APP_VERSION}")
    if token:
        request.add_header("authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ensure_registered(path=SERVER_FILE):
    """A beta install without a token asks the door for one. True once it has one."""
    door = read_door(path)
    if door is None:
        return False                     # not a beta install (a developer's machine)
    if door.get("token"):
        return True
    try:
        door["token"] = post(door["url"].rstrip("/") + "/v1/register", {})["token"]
    except Exception as error:
        print(f"[beta: couldn't register yet ({error.__class__.__name__}) - the coach waits]")
        return False
    with open(path, "w", encoding="utf-8") as f:
        json.dump(door, f, indent=1)
    return True


def anonymised_tape(path):
    """The tape, gzipped, with the tester called 'Me' and every other driver 'Driver N'
    (the same N for the same driver all race)."""
    codes = {}
    out = io.BytesIO()
    with gzip.open(path, "rt", encoding="utf-8") as source, gzip.open(out, "wt", encoding="utf-8") as target:
        for line in source:
            frame = json.loads(line)
            if isinstance(frame.get("me"), dict) and "driver" in frame["me"]:
                frame["me"]["driver"] = "Me"
            for car in frame.get("opponents") or []:
                if isinstance(car, dict) and "driver" in car:
                    name = car["driver"]
                    if name not in codes:
                        codes[name] = f"Driver {len(codes) + 1}"
                    car["driver"] = codes[name]
            target.write(json.dumps(frame) + "\n")
    return out.getvalue()


def race_summary(conn, session_id):
    """What the race was and everything the radio said: enough to read a complaint against."""
    row = conn.execute(
        "SELECT track, session_type, grid, final_place, car_class, car_model, end_reason, tape_path "
        "FROM sessions WHERE id = ?", (session_id,)).fetchone()
    keys = ["track", "session_type", "grid", "final_place", "car_class", "car_model", "end_reason", "tape_path"]
    summary = dict(zip(keys, row)) if row else {}
    summary["tape"] = os.path.basename(summary.pop("tape_path", None) or "")
    summary["radio"] = [
        {"t": round(r[0], 1), "seat": r[1], "kind": r[2], "line": r[3]}
        for r in conn.execute(
            "SELECT sim_time, seat, kind, line FROM radio_log WHERE session_id = ? AND status = 'spoken' "
            "AND line IS NOT NULL ORDER BY sim_time", (session_id,))
    ]
    return summary


def send_feedback(conn, session_id, rating, comment, send_tape, path=SERVER_FILE):
    """Sends it; returns the feedback id, or None when the door can't be reached."""
    door = read_door(path)
    if door is None or not door.get("token"):
        return None
    base = door["url"].rstrip("/") + "/v1/feedback"
    summary = race_summary(conn, session_id)
    body = {"version": APP_VERSION, "rating": rating, "comment": comment.strip(), "race": summary}
    try:
        feedback_id = post(base, body, door["token"])["id"]
    except Exception as error:
        print(f"[beta: feedback not sent ({error.__class__.__name__})]")
        return None
    tape = summary.get("tape")
    if send_tape and tape and os.path.exists(tape):
        data = anonymised_tape(tape)
        if len(data) <= TAPE_LIMIT:
            try:
                post(f"{base}/{urllib.parse.quote(feedback_id)}/tape", data, door["token"], raw=True, timeout=120)
            except Exception as error:
                print(f"[beta: tape not sent ({error.__class__.__name__})]")
        else:
            print("[beta: the tape is over 25 MB - sent the feedback without it]")
    print("[beta: thanks - feedback sent]")
    return feedback_id


def ask_after_race(conn, session_id, path=SERVER_FILE):
    """The window after a race. Closing it sends nothing."""
    if read_door(path) is None:
        return None
    import tkinter as tk
    from setup_wizard import sharp_on_scaled_screens

    sharp_on_scaled_screens()
    answer = {}
    root = tk.Tk()
    root.title("Apex beta: how was the radio?")
    root.configure(bg="#0b0e12", padx=24, pady=20)
    root.attributes("-topmost", True)
    style = {"bg": "#0b0e12", "fg": "#eef1f4", "font": ("Bahnschrift", 12)}
    tk.Label(root, text="How was the radio this race?", **{**style, "font": ("Bahnschrift", 16, "bold")}).pack(anchor="w")
    rating = tk.IntVar(value=0)
    row = tk.Frame(root, bg="#0b0e12")
    row.pack(anchor="w", pady=10)
    for score in range(1, 6):
        tk.Radiobutton(row, text=str(score), value=score, variable=rating, indicatoron=0, width=4,
                       font=("Bahnschrift", 13, "bold"), bg="#141920", fg="#eef1f4", selectcolor="#ff5b1f",
                       bd=0, relief="flat").pack(side="left", padx=3)
    tk.Label(root, text="What was wrong, or what do you want it to do?", **style).pack(anchor="w")
    comment = tk.Text(root, width=48, height=4, font=("Bahnschrift", 11))
    comment.pack(anchor="w", pady=(2, 10))
    send_tape = tk.BooleanVar(value=True)
    tk.Checkbutton(root, text="Send this race's tape so the bug can be replayed\n"
                              "(other drivers' names are removed first)",
                   variable=send_tape, justify="left", bg="#0b0e12", fg="#eef1f4", selectcolor="#141920",
                   activebackground="#0b0e12", activeforeground="#eef1f4", font=("Bahnschrift", 11)).pack(anchor="w")

    def done():
        answer.update(rating=rating.get(), comment=comment.get("1.0", "end"), tape=send_tape.get())
        root.destroy()

    tk.Button(root, text="Send", command=done, font=("Bahnschrift", 12, "bold"), bg="#ff5b1f", fg="#07090c",
              bd=0, padx=18, pady=6).pack(anchor="e", pady=(14, 0))
    root.mainloop()
    if not answer or (answer["rating"] == 0 and not answer["comment"].strip()):
        return None
    return send_feedback(conn, session_id, answer["rating"], answer["comment"], answer["tape"], path)
