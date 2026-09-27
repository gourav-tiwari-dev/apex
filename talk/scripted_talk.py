"""His voice on a replay: words written down with when he says them.

There is no microphone on a replay, so orders and questions are tested on a real tape by writing
them into a script. ScriptedTalk hands them to the race loop exactly as push-to-talk hands over
what it heard."""

import ptt as push_to_talk


class ScriptedTalk:
    """His voice for a replay: what he says and when, so orders can be tested on a real tape without
    a race (26 Sep). script = [(laps_done, seconds_into_that_lap, words)], said once each, in order,
    at the first race snapshot past that point. The game's lap count, the same one his follows.
    laps_done = None: seconds is the sim time itself (tools/replay_orders.py aims at a known call)."""

    def __init__(self, source, script):
        self.source = source
        self.script = list(script)  # said in the order written
        self.lap_started = {}  # laps done -> sim time the game first showed it

    def poll(self):
        race = self.source.race
        if race is None or race.me is None or not self.source.new_race:
            return []
        self.lap_started.setdefault(race.me.laps, race.sim_time)
        heard = []
        while self.script:
            laps, into, words = self.script[0]
            if laps is None:
                if race.sim_time < into:
                    break
            else:
                start = self.lap_started.get(laps)
                if race.me.laps < laps or start is None or race.sim_time < start + into:
                    break
            self.script.pop(0)
            heard.append(push_to_talk.Heard(words, 1.5, 0, confidence=-0.2))
        return heard

    def set_track_words(self, corner_names):
        pass  # no Whisper to prime: the words are already written

    def close(self):
        pass
