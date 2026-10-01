"""The words racecraft praises a pass with: a hype line, Max's (swearing, clean), then
how the pass was made. Taking turns, never at random (words.Rotation).

A brilliant move (late on the brakes, a switchback, two for one, brave through a
corner) gets the big hype; a solid one (a better exit, the tow) the calm kind.
radio/phrasebook.py renders every one of them ahead of time."""

BRILLIANT = [
    ("WHAT A FUCKING MOVE! Get in there!", "WHAT A MOVE! Get in there!"),
    ("Mega, mate. Absolutely fucking mega.", "Mega, mate. Absolutely mega."),
    ("Oh, get in there! Fucking lovely!", "Oh, get in there! Lovely!"),
]
SOLID = [
    ("Simply lovely, mate.", "Simply lovely, mate."),
    ("Lovely. That's how you fucking do it.", "Lovely. That's how you do it."),
    ("Good job. Clean as you like.", "Good job. Clean as you like."),
]
MOVE_WORDS = {
    "late_brake": "Late on the brakes.",
    "switchback": "Switchback!",
    "double": "Two for one!",
    "corner": "Brave through there.",
    "exit": "Better exit did it.",
    "tow": "Great tow.",
}
BRILLIANT_MOVES = ("late_brake", "switchback", "double", "corner")
