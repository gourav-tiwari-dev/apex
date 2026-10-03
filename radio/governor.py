"""The team radio: who gets to speak, and when.

Every seat raises Calls. The Governor decides which one goes on air, using sim time only,
never the wall clock, so a replay at any speed makes the same decisions. That is what the
decision hash proves.

The rules, like a real pit wall:
  - urgent calls (spotter, flags) go out immediately, even over another line
  - everything else waits for a straight: nobody talks to the driver mid-corner
  - one line at a time: the radio is busy for about as long as a line takes to say
  - each seat has a cooldown, so no seat can talk over and over
  - a call that waited too long is dropped, not said late (stale advice is wrong advice)
  - an answer to a question he asked on the radio (push-to-talk) goes out as soon as nothing
    else is playing: he asked, so he is ready to listen, corner or not
  - "quiet for N laps" holds everything except urgent calls and answers (E17)
  - v3 (24 Sep): the engineer gets about 2 lines a minute; the spotter, flags and his own
    answers are never counted. Live that night: 84 lines in 14.5 minutes was noise
  - v3: at the start and after a restart, only the spotter, flags and answers speak until the
    race has settled (seats/settle.py decides when). Coaching in the chaos is noise
"""

import hashlib
import json


SECONDS_PER_WORD = 0.4  # measured edge-tts pace is about 2.5 words a second
LINE_OVERHEAD_S = 0.8  # a radio click and a breath around every line
# a call with no template: its words (conclusion + Max closer) are not counted, 10 assumed
UNKNOWN_LINE_WORDS = 10

DEFAULT_COOLDOWN_S = 8.0
# GUESSED from his words ("less noise"): 2 engineer lines a minute. Tune on the next race.
ENGINEER_LINES_PER_WINDOW = 2
ENGINEER_WINDOW_S = 60.0
NEVER_COUNTED_SEATS = ("spotter", "race_control")
CHAOS_ALLOWED = ("PASS_PRAISE", "STICK_IT")
# the same words again inside this window are dropped (replay of 24 Sep: "cover the inside
# into Arnage" four times, "stick it, cover Mulsanne Corner" twice in 8 s)
SAME_WORDS_S = 240.0
SEAT_COOLDOWN_S = {
    "spotter": 0.0,  # the spotter must never be held back
    "race_control": 0.0,
    "performance": 5.0,  # v1's SPEAK_COOLDOWN
}


def words_in(text):
    """How many words are in this text."""
    return len(text.split())


def estimated_duration(call):
    """How long a call takes to say: a radio click and a breath, then SECONDS_PER_WORD a
    word (UNKNOWN_LINE_WORDS when it has no template)."""
    if call.template:
        words = words_in(call.template)
    else:
        words = UNKNOWN_LINE_WORDS
    return LINE_OVERHEAD_S + words * SECONDS_PER_WORD


class Governor:
    """Decides which call goes on air and when, on sim time only, by the rules above."""
    def __init__(self):
        self.pending = []
        self.busy_until = 0.0
        self.last_spoken_by_seat = {}
        self.admitted = []  # (seat, kind, sim_time) of every call put on air - hashed
        self.dropped = []  # (call, reason) - calls that never went out, for the log
        self.lap = 0  # the lap he is on, kept up to date by the session loop
        self.quiet_until_lap = None
        self.orders = (
            None  # his standing orders (orders.StandingOrders), set by the race loop
        )
        self.settled = (
            True  # False from lights out until seats/settle.py says the race settled
        )
        self.engineer_air_times = []  # sim times of the counted lines, for the talk budget
        self.said_at = {}  # the words of every coaching line -> when it went on air
        self.chequered = False  # his flag is out: no more coaching this session

    def exempt(self, call):
        """The spotter, flags and his own answers: never held, never counted."""
        return (
            call.urgent
            or call.asked
            or call.immediate
            or call.seat in NEVER_COUNTED_SEATS
        )

    def hold_reason(self, call):
        """Why this call is held back now ("chequered", "quiet" or "start_chaos"), or
        None; the spotter, flags and his answers are never held."""
        # only the spotter, flags and his answers speak in the chaos or on quiet; an immediate
        # line (praise, "stick it") is still coaching
        if call.urgent or call.asked or call.seat in NEVER_COUNTED_SEATS:
            return None
        # 24 Sep replay: "Chequered flag. P13." then, in the same second, a braking tip for a
        # lap he will never drive. After the flag only the spotter and his answers talk.
        if self.chequered and call.kind != "FINISH":
            return "chequered"
        # his call, 25 Sep: earned praise and "stick it" go out even in the start chaos
        if call.kind in CHAOS_ALLOWED:
            return "quiet" if self.quiet() else None
        if self.quiet():
            return "quiet"
        if not self.settled:
            return "start_chaos"
        return None

    def offer(self, call):
        """False when the call is dropped on arrival (quiet, or the start is still chaos), so the
        caller does not spend a voice render on a line nobody will hear."""
        if call.kind == "FINISH":
            self.chequered = True
        if self.orders is not None:
            # his standing orders first: "we push" silences "lift and coast" (26 Sep)
            adjusted = self.orders.adjust(call)
            if adjusted is None:
                self.dropped.append((call, "his_order"))
                return False
            call = adjusted
        reason = self.hold_reason(call)
        # the spotter's hazards are kept apart per car by track awareness itself: the same words for
        # another car are news (27 Sep, 58-car race: two "LMP2 behind" calls dropped as said_recently)
        if (
            reason is None
            and not (call.urgent or call.asked)
            and call.template
            and call.seat != "spotter"
        ):
            said = self.said_at.get(call.template)
            if said is not None and call.sim_time - said < SAME_WORDS_S:
                reason = "said_recently"
        if reason is not None:
            self.dropped.append((call, reason))
            return False
        self.pending.append(call)
        return True

    def within_talk_budget(self, call, now):
        """The engineer has said fewer than ENGINEER_LINES_PER_WINDOW counted lines in
        the last ENGINEER_WINDOW_S; an exempt call always fits."""
        if self.exempt(call):
            return True
        recent = [t for t in self.engineer_air_times if now - t < ENGINEER_WINDOW_S]
        return len(recent) < ENGINEER_LINES_PER_WINDOW

    def cooldown_of(self, seat):
        """That seat's cooldown, in seconds."""
        return SEAT_COOLDOWN_S.get(seat, DEFAULT_COOLDOWN_S)

    def cooled_down(self, call, now):
        """That seat's cooldown has passed since it last spoke."""
        last = self.last_spoken_by_seat.get(call.seat)
        if last is None:
            return True
        return now - last >= self.cooldown_of(call.seat)

    def drop_stale(self, now):
        """Drops every waiting call older than its ttl: stale advice is wrong advice."""
        still_fresh = []
        for call in self.pending:
            if now - call.sim_time > call.ttl:
                self.dropped.append((call, "expired"))
            else:
                still_fresh.append(call)
        self.pending = still_fresh

    def best(self, calls):
        """The most important call (the lowest priority number); among equals, the one
        raised first."""
        # most important first; among equals, whoever asked first
        best_call = None
        for call in calls:
            if best_call is None:
                best_call = call
            elif call.priority < best_call.priority:
                best_call = call
            elif (
                call.priority == best_call.priority
                and call.sim_time < best_call.sim_time
            ):
                best_call = call
        return best_call

    def put_on_air(self, call, now):
        """Takes the call off the queue and onto the air: the radio is busy for about
        its length, the seat's cooldown starts, it counts against the talk budget unless
        exempt, and it goes into the decision hash."""
        self.pending.remove(call)
        self.busy_until = now + estimated_duration(call)
        self.last_spoken_by_seat[call.seat] = now
        if not self.exempt(call):
            self.engineer_air_times.append(now)
        self.admitted.append((call.seat, call.kind, round(call.sim_time, 4)))
        if call.template and not (call.urgent or call.asked):
            self.said_at[call.template] = now
        return call

    def quiet(self):
        """He asked for quiet, and those laps are not over."""
        return self.quiet_until_lap is not None and self.lap < self.quiet_until_lap

    def drop_held(self):
        """Drops every waiting call that is held now (hold_reason) or that an order he
        gave since forbids."""
        kept = []
        for call in self.pending:
            reason = self.hold_reason(call)
            # an order he gave after this call was queued (26 Sep): it waited for a straight or
            # a cooldown, and "no coaching" came in meanwhile
            if reason is None and self.orders is not None and self.orders.forbids(call):
                reason = "his_order"
            if reason is None:
                kept.append(call)
            else:
                self.dropped.append((call, reason))
        self.pending = kept

    def step(self, now, in_corner):
        """Call once per frame. Returns the call to put on air now, or None."""
        self.drop_stale(now)
        self.drop_held()
        if not self.pending:
            return None

        urgent = [c for c in self.pending if c.urgent]
        if urgent:
            return self.put_on_air(self.best(urgent), now)

        if now < self.busy_until:
            return None
        asked = [c for c in self.pending if c.asked or c.immediate]
        if asked:
            return self.put_on_air(self.best(asked), now)
        # flow-state rule: no talking to the driver in the middle of a corner
        if in_corner:
            return None
        ready = [
            c
            for c in self.pending
            if self.cooled_down(c, now) and self.within_talk_budget(c, now)
        ]
        if not ready:
            return None
        return self.put_on_air(self.best(ready), now)

    def decision_hash(self):
        """SHA-256 of every call put on air (seat, kind, sim time): the same race
        replayed gives the same hash."""
        return hashlib.sha256(json.dumps(self.admitted).encode()).hexdigest()
