from test_race_state import fake_game
from game.tape import ReplaySource
from dev.record_race import record


class FakeGame:
    """Stands in for the shared memory: the clock ticks 60 times a second,
    scoring updates every 12th frame (5 a second), and the session ends after 2 seconds."""

    def __init__(self):
        self.data = fake_game()
        self.ticks = 0

    def update(self):
        self.ticks += 1
        me = self.data.telemetry.telemInfo[self.data.telemetry.playerVehicleIdx]
        me.mElapsedTime = self.ticks / 60.0
        if self.ticks % 12 == 0:
            self.data.scoring.scoringInfo.mCurrentET = self.ticks / 60.0
        if self.ticks >= 120:
            self.data.scoring.scoringInfo.mGamePhase = 8

    def close(self):
        pass


def test_recording_stops_at_session_end_and_replays(tmp_path):
    tape = str(tmp_path / "recorded.jsonl.gz")
    record(FakeGame(), tape)

    source = ReplaySource(None, tape)
    frames = 0
    snapshots = 0
    for frame in source:
        frames += 1
        if source.new_race:
            snapshots += 1
    assert 100 <= frames <= 125  # it stopped by itself near the 2 s mark
    assert snapshots >= 9  # about 5 scoring updates a second
    assert source.race.session.game_phase == 8
    assert source.race.opponents[0].driver == "Ann"


def test_one_incident_with_several_hits_counts_once():
    from dataclasses import replace
    from driving.detectors import ContactDetection
    from test_seats import frame

    detector = ContactDetection()
    seen = []
    for t, hit in (
        (0.0, None),
        (1.0, None),
        (266.0, 266.0),
        (266.5, 266.4),
        (271.0, 270.9),
        (500.0, 499.8),
    ):
        event = detector.update(
            replace(frame(t), last_impact_time=hit, last_impact_magnitude=1.0),
            None,
            None,
        )
        if event:
            seen.append(event.kind)
    assert seen == [
        "IMPACT",
        "IMPACT",
    ]  # the 266-271 burst is one incident, 500 is another


def test_a_session_already_over_at_startup_does_not_end_straight_away(
    tmp_path, monkeypatch
):
    """Started on the results screen, Apex must wait, not end and restart in a loop."""
    import gzip, json
    from dataclasses import asdict
    import session, memory
    from game.race_snapshot import read_race_snapshot
    from test_race_state import fake_game, first_frames

    data = fake_game()
    data.scoring.scoringInfo.mGamePhase = 8
    over = read_race_snapshot(data)
    tape = str(tmp_path / "results_screen.jsonl.gz")
    with gzip.open(tape, "wt") as out:
        out.write(json.dumps(asdict(over)) + "\n")
        for frame in first_frames(50):
            out.write(json.dumps(asdict(frame)) + "\n")
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(session, "connect_db", lambda: memory.connect_db(db))
    session_id = session.run_replay(tape, out_loud=False)
    conn = memory.connect_db(db)
    reason = conn.execute(
        "SELECT end_reason FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()[0]
    assert reason == "tape_end"  # it kept going instead of ending on the first frame
