from words import Rotation

"""Max's voice for code's own lines, with no model in the loop.

v3 (24 Sep 2026): the audit of that night's race found 27 of 38 kinds of line went through
the model only to be reworded. The facts were already written by code, and the rewording cost
about 1.8 s a line. Now the seat's words are said as they are, and a short closer in Max's
voice is added from the pools below. Rotating, never random, so a replay says the same thing.

Each closer is (with swearing, clean). An empty closer means "just the facts": short lines
are part of the voice, and not every line needs a flourish.
"""

CLOSERS = {
    "CATCHING": [
        ("Keep fucking pushing.", "Keep pushing."),
        ("", ""),
        ("Go and get it, mate.", "Go and get it, mate."),
    ],
    "PACE_TARGET": [
        ("Simple as that.", "Simple as that."),
        ("", ""),
        ("Fucking do it.", "Do it."),
    ],
    "THREAT_BEHIND": [
        ("Don't give them a fucking sniff.", "Don't give them a sniff."),
        ("", ""),
        ("Head down, mate.", "Head down, mate."),
    ],
    "GAP_GROWING": [("", ""), ("Lovely.", "Lovely.")],
    "GAP_REPORT": [("", "")],
    "FUEL": [("", ""), ("No bullshit, that's the number.", "That's the number.")],
    "TYRE_HOT": [("Stop sliding the fucking thing.", "Stop sliding it."), ("", "")],
    "CORNER_LOSS": [("", ""), ("Sort it out.", "Sort it out.")],
    "FASTEST_CAR": [("", ""), ("That's free time, mate.", "That's free time, mate.")],
    "BALANCE": [("", "")],
    "PRAISE": [("Fucking lovely.", "Lovely."), ("Simply lovely.", "Simply lovely.")],
    "LOCKUP": [("", ""), ("Bloody hell.", "Careful.")],
    "OFF_TRACK": [("", ""), ("Reset. Next corner.", "Reset. Next corner.")],
    "SPIN": [
        ("", "")
    ],  # a safety call: "wait for the traffic, then rejoin", nothing added
    "DAMAGE": [("", "")],
    "SETTLED": [("Now we fucking race.", "Now we race."), ("Let's go.", "Let's go.")],
    "ATTACK_PLAN": [("", ""), ("Patience, mate.", "Patience, mate.")],
    "DEFEND_PLAN": [("", "")],
    "CONTACT_RESET": [("", "")],
    "PASSED": [("", "")],
}


class MaxLines:
    def __init__(self, clean=False):
        self.closers = Rotation(clean)

    def line(self, call):
        """The words to say for a call that is not phrased by the model."""
        words = call.template or call.conclusion
        pool = CLOSERS.get(call.kind)
        if not pool:
            return words
        closer = self.closers.next(call.kind, pool)
        if not closer:
            return words
        return f"{words} {closer}"
