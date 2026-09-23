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
"""
import hashlib
import json
from dataclasses import dataclass, field

# Lower number = more important.
SPOTTER = 1          # car alongside, clear, three wide
RACE_CONTROL = 2     # flags, safety car, penalties, physics abort
RACECRAFT = 3        # attack, defend, "not here"
ENGINEER = 4         # gaps, damage, composure
STRATEGY = 5         # fuel, tyres, weather, pits
PERFORMANCE = 6      # corner deltas, lap feedback
MEMORY = 7           # habits recalled, earned praise

SECONDS_PER_WORD = 0.4      # measured edge-tts pace is about 2.5 words a second
LINE_OVERHEAD_S = 0.8       # a radio click and a breath around every line
UNKNOWN_LINE_WORDS = 10     # an LLM line's length is unknown when it is admitted

DEFAULT_COOLDOWN_S = 8.0
SEAT_COOLDOWN_S = {
    "spotter": 0.0,         # the spotter must never be held back
    "race_control": 0.0,
    "performance": 5.0,     # v1's SPEAK_COOLDOWN
}


@dataclass
class Call:
    seat: str                 # "spotter", "race_engineer", "strategist", ...
    kind: str                 # what happened, e.g. "CAR_LEFT", "OFF_TRACK", "FUEL_TO_FINISH"
    sim_time: float           # when the seat raised it
    priority: int             # see the ladder above
    ttl: float                # seconds of sim time it stays worth saying
    conclusion: str           # the plain-English sentence code wrote; the LLM only rephrases it
    facts: dict = field(default_factory=dict)      # the only numbers the spoken line may use
    urgent: bool = False      # time-critical: goes out at once, from the pre-rendered voice bank
    template: str | None = None                    # exact words; urgent calls always have one
    evidence: dict = field(default_factory=dict)   # database ids this call rests on


def words_in(text):
    return len(text.split())


def estimated_duration(call):
    if call.template:
        words = words_in(call.template)
    else:
        words = UNKNOWN_LINE_WORDS
    return LINE_OVERHEAD_S + words * SECONDS_PER_WORD


class Governor:
    def __init__(self):
        self.pending = []
        self.busy_until = 0.0
        self.last_spoken_by_seat = {}
        self.admitted = []        # (seat, kind, sim_time) of every call put on air - hashed
        self.dropped = []         # (call, reason) - calls that never went out, for the log

    def offer(self, call):
        self.pending.append(call)

    def cooldown_of(self, seat):
        return SEAT_COOLDOWN_S.get(seat, DEFAULT_COOLDOWN_S)

    def cooled_down(self, call, now):
        last = self.last_spoken_by_seat.get(call.seat)
        if last is None:
            return True
        return now - last >= self.cooldown_of(call.seat)

    def drop_stale(self, now):
        still_fresh = []
        for call in self.pending:
            if now - call.sim_time > call.ttl:
                self.dropped.append((call, "expired"))
            else:
                still_fresh.append(call)
        self.pending = still_fresh

    def best(self, calls):
        # most important first; among equals, whoever asked first
        best_call = None
        for call in calls:
            if best_call is None:
                best_call = call
            elif call.priority < best_call.priority:
                best_call = call
            elif call.priority == best_call.priority and call.sim_time < best_call.sim_time:
                best_call = call
        return best_call

    def put_on_air(self, call, now):
        self.pending.remove(call)
        self.busy_until = now + estimated_duration(call)
        self.last_spoken_by_seat[call.seat] = now
        self.admitted.append((call.seat, call.kind, round(call.sim_time, 4)))
        return call

    def step(self, now, in_corner):
        """Call once per frame. Returns the call to put on air now, or None."""
        self.drop_stale(now)
        if not self.pending:
            return None

        urgent = [c for c in self.pending if c.urgent]
        if urgent:
            return self.put_on_air(self.best(urgent), now)

        if now < self.busy_until:
            return None
        # flow-state rule: no talking to the driver in the middle of a corner
        if in_corner:
            return None
        ready = [c for c in self.pending if self.cooled_down(c, now)]
        if not ready:
            return None
        return self.put_on_air(self.best(ready), now)

    def decision_hash(self):
        return hashlib.sha256(json.dumps(self.admitted).encode()).hexdigest()


class Budget:
    """What the LLM has cost this session. Past the cap, Apex speaks template lines only."""

    # GUESSED prices: derived from the 23 Sep balance drop (Rs 0.62 for 3884 tokens in and
    # 3496 out), assuming output costs 4x input as on aicredits' V3 price list.
    RS_PER_MILLION_IN = 35.0
    RS_PER_MILLION_OUT = 139.0

    def __init__(self, cap_rs=5.0):
        self.cap_rs = cap_rs
        self.spent_rs = 0.0

    def cost_of(self, tokens_in, tokens_out):
        return (tokens_in * self.RS_PER_MILLION_IN + tokens_out * self.RS_PER_MILLION_OUT) / 1_000_000

    def charge(self, tokens_in, tokens_out):
        cost = self.cost_of(tokens_in, tokens_out)
        self.spent_rs += cost
        return cost

    def allows_llm(self):
        return self.spent_rs < self.cap_rs
