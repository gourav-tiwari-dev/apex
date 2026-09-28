import gzip
import json
from dataclasses import asdict

import session
import memory
from driving import track_map
from game.tape import ReplaySource
from driving.laps import LapCounter, LapDistance
from game.race_snapshot import read_race_snapshot
from test_race_state import fake_game
from driving.track_map import TrackMapLearner, MONZA_CORNERS, borrow_names, corner_at
from test_determinism import FakePersona

ELEVEN_LAPS = "tape_20260821_232642.jsonl.gz"


def test_corner_at_uses_start_inclusive_end_exclusive():
    corners = [{"name": "A", "start": 100, "end": 200}]
    assert corner_at(corners, 100) == "A"
    assert corner_at(corners, 199.9) == "A"
    assert corner_at(corners, 200) is None
    assert corner_at(None, 150) is None


def test_learner_rebuilds_the_hand_measured_monza_map():
    counter = LapCounter()
    distance = LapDistance()
    learner = TrackMapLearner()
    lap = 0
    for frame in ReplaySource(None, ELEVEN_LAPS):
        lap = counter.update(frame)
        learner.add(lap, distance.update(frame), frame.brake, frame.accel_lat)

    learned = borrow_names(learner.corners(lap), MONZA_CORNERS)

    assert [c["name"] for c in learned] == [c["name"] for c in MONZA_CORNERS]
    for mine, hand in zip(learned, MONZA_CORNERS):
        assert abs(mine["start"] - hand["start"]) <= 25, mine["name"]


def test_a_new_track_is_learned_while_driving_and_saved(tmp_path, monkeypatch):
    # a v2 tape on a track Apex has never seen: one race line, then 5 real laps of frames
    data = fake_game()
    data.scoring.scoringInfo.mTrackName = b"Test Ring"
    snapshot = read_race_snapshot(data)
    tape = str(tmp_path / "new_track.jsonl.gz")
    with gzip.open(ELEVEN_LAPS, "rt") as source, gzip.open(tape, "wt") as out:
        out.write(json.dumps(asdict(snapshot)) + "\n")
        for number, line in enumerate(source):
            if number > 62000:  # the out-lap + about 5 laps
                break
            out.write(line)

    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(track_map, "MAPS_FOLDER", str(tmp_path / "maps"))
    monkeypatch.setattr(session, "connect_db", lambda: memory.connect_db(db_path))

    session.run_session(True, None, tape, out_loud=False, persona=FakePersona())

    saved = track_map.load_map("Test Ring")
    assert saved is not None
    assert len(saved) == 7
    assert saved[0]["name"] == "Turn 1"
    # once the map was learned, events got tagged with the new corners
    conn = memory.connect_db(db_path)
    tagged = conn.execute(
        "SELECT COUNT(*) FROM events WHERE corner LIKE 'Turn %'"
    ).fetchone()[0]
    conn.close()
    assert tagged > 0
