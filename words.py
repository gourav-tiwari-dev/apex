"""How numbers are written on the radio: lap times ("3:59.4") and gaps ("6 tenths").

One place, so the seats, the answers, the coach's tools and the phrase bank can never write the
same number two ways. Until 27 Sep 2026 the lap time was typed out in five files and the gap in
two."""


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
