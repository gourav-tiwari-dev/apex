"""How the radio words things: lap times ("3:59.4"), gaps ("6 tenths"), and Max's lines in turn.

One place, so the seats, the answers, the coach's tools and the phrase bank can never write the
same number two ways. Until 27 Sep 2026 the lap time was typed out in five files, the gap in two,
and the turn-taking of Max's lines in three."""


def lap_time_parts(seconds):
    """(minutes, seconds to a tenth): 239.96 s is (4, 0.0), not (3, 60.0)."""
    minutes = int(seconds // 60)
    rest = round(seconds - minutes * 60, 1)
    if rest >= 60.0:
        minutes += 1
        rest = round(rest - 60.0, 1)
    return minutes, rest


def lap_text(seconds):
    """A lap time as the radio writes it, "3:59.4"; None when there is no time (None, 0 or less)."""
    if seconds is None or seconds <= 0:
        return None
    minutes, rest = lap_time_parts(seconds)
    return f"{minutes}:{rest:04.1f}"


def tenths_words(seconds):
    """A gap as the radio says it: "a tenth", "6 tenths", "1.2 seconds"."""
    tenths = round(seconds * 10)
    if tenths <= 1:
        return "a tenth"
    if tenths >= 10:
        return f"{round(seconds, 1)} seconds"
    return f"{tenths} tenths"


class Rotation:
    """Picks from pools of (swearing, clean) pairs in turn, never at random, so a replay says the
    same thing. Each key keeps its own turn; callers that want pools to share one turn use one
    key for all of them."""

    def __init__(self, clean=False):
        self.clean = clean
        self.turns = {}  # key -> how many lines it has given

    def next(self, key, pool):
        turn = self.turns.get(key, 0)
        self.turns[key] = turn + 1
        swearing, clean = pool[turn % len(pool)]
        return clean if self.clean else swearing
