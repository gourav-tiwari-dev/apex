from game.lmu_data import LMUObjectOut
from race_state import read_race_snapshot, read_near_cars
from game.tape import ReplaySource, Recorder, TAPE_PATH


def fake_game():
    data = LMUObjectOut()
    info = data.scoring.scoringInfo
    info.mNumVehicles = 3
    info.mTrackName = b"Monza"
    info.mSession = 10
    info.mGamePhase = 5
    info.mCurrentET = 100.0

    # scoring order: the leader, me, third place
    cars = data.scoring.vehScoringInfo
    cars[0].mID = 5
    cars[0].mPlace = 1
    cars[0].mDriverName = b"Ann"
    cars[0].mVehicleClass = b"GT3"
    cars[1].mID = 0
    cars[1].mPlace = 2
    cars[1].mIsPlayer = True
    cars[1].mDriverName = b"Gourav"
    cars[2].mID = 9
    cars[2].mPlace = 3
    cars[2].mDriverName = b"Bob"

    # telemetry is ordered differently from scoring: Bob first, then Ann, then me
    rows = data.telemetry.telemInfo
    rows[0].mID = 9
    rows[0].mPos.x = 10.0
    rows[1].mID = 5
    rows[1].mPos.x = 500.0
    rows[2].mID = 0
    rows[2].mFuel = 42.5
    data.telemetry.playerVehicleIdx = 2
    return data


def test_opponents_are_matched_by_id_not_by_position():
    snapshot = read_race_snapshot(fake_game())

    assert snapshot.me.driver == "Gourav"
    assert snapshot.me.place == 2
    assert snapshot.me.fuel == 42.5
    drivers = [o.driver for o in snapshot.opponents]
    assert drivers == ["Ann", "Bob"]
    assert snapshot.opponents[0].x == 500.0  # Ann's own telemetry row, not row 0
    assert snapshot.opponents[1].x == 10.0
    assert snapshot.session.track == "Monza"


def test_only_cars_inside_the_radius_are_near():
    near = read_near_cars(fake_game(), [0.0, 0.0, 0.0])
    assert [car.id for car in near.cars] == [9]


def test_nobody_near_gives_none():
    assert read_near_cars(fake_game(), [5000.0, 0.0, 0.0]) is None


def first_frames(count):
    frames = []
    for frame in ReplaySource(None, TAPE_PATH):
        frames.append(frame)
        if len(frames) == count:
            break
    return frames


def test_old_tape_still_loads_with_empty_v2_fields():
    frame = first_frames(1)[0]
    assert frame.steering is None
    assert frame.pos is None


def test_v2_tape_replays_race_lines_on_the_right_frame(tmp_path):
    data = fake_game()
    snapshot = read_race_snapshot(data)
    near = read_near_cars(data, [0.0, 0.0, 0.0])
    frame_a, frame_b = first_frames(2)

    tape = str(tmp_path / "tape_v2.jsonl.gz")
    recorder = Recorder(tape)
    recorder.record(snapshot)
    recorder.record(near)
    recorder.record(frame_a)
    recorder.record(frame_b)
    recorder.stop()

    source = ReplaySource(None, tape)
    seen = []
    for frame in source:
        seen.append(
            (frame.elapsed_time, source.new_race, source.near is not None, source.race)
        )

    assert len(seen) == 2
    assert seen[0][:3] == (frame_a.elapsed_time, True, True)
    assert seen[1][:3] == (frame_b.elapsed_time, False, False)
    # the snapshot survives the round trip exactly, and stays the latest one on later frames
    assert seen[0][3] == snapshot
    assert seen[1][3] == snapshot
