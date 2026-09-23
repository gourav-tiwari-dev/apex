from test_race_state import fake_game
from live_telemetry import ReplaySource
from record_race import record


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
    assert 100 <= frames <= 125          # it stopped by itself near the 2 s mark
    assert snapshots >= 9                # about 5 scoring updates a second
    assert source.race.session.game_phase == 8
    assert source.race.opponents[0].driver == "Ann"
