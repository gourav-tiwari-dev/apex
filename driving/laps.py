"""His own laps, counted from his car.

LapCounter: a line crossing is his lap distance dropping by over 1000 m in one frame; race_lap turns
that into the lap he is on as the game counts it. LapDistance: how far into the lap he really is
between the game's lap-distance updates (they come about 5 times a second), from speed x time."""

# the game's lap count trails the line by up to a scoring update (5 a second)
SCORING_LAG_S = 0.5
# green running before a crossing that makes it a lap finished, not the start
REAL_LAP_S = 30.0


class LapCounter:
    def __init__(self):
        self.previous_lap_dist = 0
        self.lap_count = 0
        self.wrapped = False
        self.green_time = 0.0  # green running since his last line crossing
        self.last_time = None
        self.finished_at = None  # when he last crossed the line finishing a green lap
        self.laps_at_finish = None  # the game's laps done at that moment

    def update(self, frame):
        self.wrapped = False
        if self.previous_lap_dist - frame.lap_dist > 1000:
            self.lap_count += 1
            self.wrapped = True
        self.previous_lap_dist = frame.lap_dist
        return self.lap_count

    def race_lap(self, game_laps, now, green):
        """The lap he is on as the game counts it: its laps done + 1. His own crossings (lap_count)
        made the formation lap "lap 1", so race lap 1 was "lap 2" for the coach, his reminders and
        the debrief (replay of 25 Sep night, 27 Sep). A lap he has just finished counts before the
        game's count catches up; the start line at lights out is not a lap finished."""
        if self.last_time is not None and green:
            self.green_time += max(0.0, now - self.last_time)
        self.last_time = now
        if self.wrapped:
            if self.green_time >= REAL_LAP_S:
                self.finished_at = now
                self.laps_at_finish = game_laps
            self.green_time = 0.0
        on_lap = game_laps + 1
        just_finished = (
            self.finished_at is not None and now - self.finished_at < SCORING_LAG_S
        )
        if just_finished and game_laps == self.laps_at_finish:
            on_lap += 1
        return on_lap


class LapDistance:
    def __init__(self):
        self.previous_lap_dist = None
        self.travelled_distance = 0
        self.previous_time = 0

    def update(self, frame):
        if frame.lap_dist != self.previous_lap_dist:
            self.previous_lap_dist = frame.lap_dist
            self.travelled_distance = 0
        else:
            self.travelled_distance += (
                frame.speed_kmh / 3.6 * (frame.elapsed_time - self.previous_time)
            )
        self.previous_time = frame.elapsed_time
        final_distance = self.previous_lap_dist + self.travelled_distance
        return final_distance
