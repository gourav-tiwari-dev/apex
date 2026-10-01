"""Auto-clips (30 Sep): the parts that decide what goes on screen, without ffmpeg or Whisper.
The full chain (recording -> Whisper -> short) was checked by hand on a synthetic race built
from session 59's real radio lines (apex-launch/cutter/make_test_clip.py): every line placed
within 0.3 s of where it was put."""

import os

from clips import auto
from clips.find_moments import keep_in_order, norm_words, place_all
from clips.captions import chunks, star
from clips.beats import pick_ask, pick_hook, timed_line_words


def words(text):
    return [{"raw": w} for w in text.split()]


def shown(line):
    return [" ".join(w["raw"] for w in c) for c in chunks(words(line))]


def radio_line(kind, t, text, seat="racecraft"):
    n = len(text.split())
    return {
        "kind": kind,
        "seat": seat,
        "line": text,
        "video_t": t,
        "matched": True,
        "timed": [
            {"raw": w, "start": t + i * 0.3, "end": t + (i + 1) * 0.3}
            for i, w in enumerate(text.split())
        ][:n],
    }


def test_no_word_ever_flashes_up_alone():
    for line in (
        "Indianapolis next. You've had trouble there. Clean exit.",
        "You're faster out of Mulsanne Chicane 2. Pass into Mulsanne Corner. Not before.",
        "Yeah, you got hit. That fucking BMW in P6 clipped you at the Esses, mate.",
    ):
        assert all(len(chunk.split()) >= 2 for chunk in shown(line)), shown(line)


def test_swears_are_starred_on_screen():
    assert star("fucking") == "f******"
    assert star("mate.") == "mate."


def test_the_hook_is_the_attack_plan_followed_soonest_by_a_pass():
    radio = [
        radio_line(
            "ATTACK_PLAN",
            10.0,
            "You're faster out of Dunlop. Pass into Esses. Not before.",
        ),
        radio_line(
            "ATTACK_PLAN",
            70.0,
            "You're faster out of Arnage. Pass into Porsche. Not before.",
        ),
        radio_line("PASS_PRAISE", 85.0, "Simply lovely, mate. Great tow."),
    ]
    hook = pick_hook(radio)
    assert [beat.kind for beat in hook] == ["hook", "hook"]
    assert hook[0].lines[0]["line"].startswith("You're faster out of Arnage")
    # the call is cut at a sentence end, not run long
    assert hook[0].end < 70.0 + 6.0 + 0.5


def test_a_line_with_a_slur_is_never_picked():
    radio = [
        radio_line(
            "ATTACK_PLAN", 10.0, "The cunt's slow out of Dunlop. Pass into Esses."
        ),
        radio_line("PASS_PRAISE", 20.0, "Simply lovely, mate."),
    ]
    assert pick_hook(radio) == []


def test_a_long_wait_for_the_answer_is_jump_cut():
    question = {
        "video_t": 100.0,
        "video_end": 101.5,
        "text": "Did I get hit back there?",
    }
    answer = radio_line(
        "ANSWER_AGENT", 104.0, "Yeah, you got hit. Damage on the car.", "race_engineer"
    )
    beats = pick_ask([answer], [question])
    assert [beat.kind for beat in beats] == ["ask", "answer"]
    assert beats[0].end < 102.0 and beats[1].start > 103.5


def test_captions_show_apex_words_with_whispers_timing():
    # Whisper heard "toe" for tow; the caption must say what Apex said
    line = {
        "line": "Simply lovely, mate. Great tow.",
        "video_t": 93.8,
        "video_end": 97.16,
        "matched": True,
    }
    heard = [
        {"w": w, "start": s, "end": e}
        for w, s, e in [
            ("simply", 93.8, 94.26),
            ("lovely", 94.26, 94.78),
            ("mate", 94.78, 95.42),
            ("great", 96.18, 96.7),
            ("toe", 96.7, 97.16),
        ]
    ]
    timed = timed_line_words(line, heard)
    assert [w["raw"] for w in timed] == ["Simply", "lovely,", "mate.", "Great", "tow."]
    assert timed[-1]["start"] == 96.7


def test_matches_out_of_time_order_are_dropped():
    hits = [
        {"sim_time": 10, "video_t": 5},
        {"sim_time": 20, "video_t": 400},
        {"sim_time": 30, "video_t": 25},
    ]
    kept = keep_in_order(hits)
    assert [h["sim_time"] for h in kept] in ([10, 30], [10, 20])
    assert len(kept) == 2


def test_lines_whisper_missed_take_their_neighbours_offset():
    hits = [
        {"id": 1, "sim_time": 100.0, "video_t": 20.0},
        {"id": 3, "sim_time": 300.0, "video_t": 221.0},
    ]
    lines = [
        {"id": 1, "sim_time": 100.0},
        {"id": 2, "sim_time": 110.0},
        {"id": 3, "sim_time": 300.0},
    ]
    placed = {p["id"]: p for p in place_all(lines, hits)}
    assert placed[2]["matched"] is False
    assert placed[2]["video_t"] == 30.0


def test_numbers_match_whether_whisper_writes_them_or_spells_them():
    assert norm_words("Seven tenths") == norm_words("7 tenths")


def test_practice_recordings_are_deleted_and_nothing_is_built(tmp_path):
    class FakeRecorder:
        def __init__(self):
            self.piece_dir = str(tmp_path / "race_x")
            os.makedirs(self.piece_dir)

        def stop(self):
            return os.path.join(self.piece_dir, "race.mp4")

    clips = auto.AutoClips("apex.db", str(tmp_path))
    clips.recorder = FakeRecorder()
    folder = clips.recorder.piece_dir
    assert clips.session_over(7, was_a_finished_race=False) is None
    assert not os.path.exists(folder)


def test_the_hook_cuts_to_the_pass_the_data_saw_not_three_seconds_before_the_praise():
    # session 59: the pass at ~359 s, the praise waited for a quiet radio until 373.8 s
    from clips.beats import passes, pick_hook_measured

    race = [[t / 10, 0.1, None, 8 if t < 3590 else 7, 200.0] for t in range(3400, 3800)]
    assert passes(race) == [(359.0, 8, 7)]
    radio = [
        {
            **radio_line("STICK_IT", 28.0, "Stick it. They're in your tow."),
            "sim_time": 308.0,
        },
        {
            **radio_line(
                "ATTACK_PLAN",
                71.0,
                "You're faster out of Mulsanne Chicane 2. Pass into Mulsanne Corner. Not before.",
            ),
            "sim_time": 351.0,
        },
        {
            **radio_line("PASS_PRAISE", 94.0, "Simply lovely, mate. Great tow."),
            "sim_time": 374.0,
        },
    ]
    beats = pick_hook_measured(radio, race)
    # the plan that set THIS pass up, not "stick it"
    assert beats[0].lines[0]["kind"] == "ATTACK_PLAN"
    pass_beats = [beat for beat in beats if beat.kind == "pass"]
    assert pass_beats[0].overtake == {"pass_sim": 359.0, "before": 8, "after": 7}
    # sim 359 = video 79: the pass is on screen
    assert pass_beats[0].start <= 79.0 <= pass_beats[0].end


def test_the_gap_is_shown_in_metres_and_never_as_minus_zero():
    from clips.captions import Captions
    from clips.beats import Beat, VideoClock

    caps = Captions()
    race = [[10.0, 0.14, None, 8, 200.0], [10.1, -0.0, None, 8, 200.0]]
    the_pass = Beat("pass", 0.0, 20.0, [], overtake={"pass_sim": 10.2})
    same_time = VideoClock([{"matched": True, "sim_time": 0.0, "video_t": 0.0}])
    caps.gap_ticker(race, the_pass, same_time, 0.0)
    texts = [event[3] for event in caps.events]
    assert texts[0].endswith("GAP 7.8 m") and texts[1].endswith("GAP 0.0 m")


def test_the_race_data_samples_a_tape_through_apex_itself(tmp_path, monkeypatch):
    """1 Oct: race_data built its Session with every option by position, so removing one
    option broke the overlays with a TypeError, and no test ran it."""
    import gzip
    import json
    from dataclasses import asdict

    import memory
    import session
    from clips.race_data import sample
    from game.race_snapshot import read_race_snapshot
    from test_race_state import fake_game, first_frames

    tape = str(tmp_path / "race.jsonl.gz")
    with gzip.open(tape, "wt") as out:
        out.write(json.dumps(asdict(read_race_snapshot(fake_game()))) + "\n")
        for frame in first_frames(50):
            out.write(json.dumps(asdict(frame)) + "\n")
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(session, "connect_db", lambda: memory.connect_db(db))
    rows = sample(tape)
    assert rows  # a row every 0.1 s of sim time
    assert len(rows[0]) == 5  # sim time, gap ahead, gap behind, place, speed
