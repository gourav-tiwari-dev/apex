"""Spotter: who is beside me, left or right, and when it is clear again.

Every frame, each nearby car's world position is turned into my car's own frame:
how far to my side, how far ahead or behind. A car overlapping me in length and sitting
one lane over is "alongside". Calls are urgent and come from the voice bank, so they
start in 0.01 ms.
"""
from radio import Call, SPOTTER

CAR_LENGTH_M = 4.7            # a GT3 is about 4.6-4.7 m long
LANE_MIN_M = 1.2              # closer sideways than this is not alongside, it is in line
LANE_MAX_M = 6.0              # further than this is two lanes away
CLEAR_AFTER_S = 0.4           # a side must stay empty this long before "clear"
CALL_TTL_S = 1.0              # a spotter call even a second late is wrong
# v3 (24 Sep): 17 of 34 spotter lines that night were "Clear", mostly after a car that only
# brushed past. Real spotters (NASA Speed News guide, iRacing's spotter) say "clear" to end a
# real overlap and repeat "still there" every few seconds while it lasts.
CLEAR_NEEDS_ALONGSIDE_S = 1.0 # a car alongside for less than this passed by: no "clear"
STILL_THERE_EVERY_S = 4.0
# 25 Sep replay of the 24 Sep start: one car alongside for ~40 s down to the first chicane got
# "still there" 10 times. The wait doubles after each one (4, 8, 16 s), so a long drag gets 4.
STILL_THERE_MAX_S = 16.0

# GUESSED until the first v2 tape: in LMU's car frame +x points to the driver's left
# (the rFactor 2 convention). The AI-race tape settles it: if the spotter says "left"
# for a car on the right, flip this to -1.
LEFT_SIGN = 1


def side_and_overlap(my_pos, my_ori, car):
    """Returns (lateral, longitudinal) of a car in my frame: lateral > 0 is my left."""
    rx = car.x - my_pos[0]
    ry = car.y - my_pos[1]
    rz = car.z - my_pos[2]
    # my_ori holds the 3 rows of the matrix that turns car-frame into world;
    # its columns turn world back into car-frame
    local_x = my_ori[0] * rx + my_ori[3] * ry + my_ori[6] * rz
    local_z = my_ori[2] * rx + my_ori[5] * ry + my_ori[8] * rz
    return LEFT_SIGN * local_x, local_z


MOVING_KMH = 30.0
GREEN = 5                     # mGamePhase: racing. The formation lap (3) runs at 70-75 km/h in
                              # a tight line, and "car left" there is pure noise (24 Sep)


def on_track(moment):
    if moment.frame.speed_kmh < MOVING_KMH:
        return False
    race = moment.race
    if race is not None and race.me is not None and race.me.in_pits:
        return False
    if race is not None and race.session.game_phase != GREEN:
        return False
    return True


def sides_taken(moment):
    left = False
    right = False
    frame = moment.frame
    if moment.near is None or frame.pos is None or frame.ori is None:
        return left, right
    for car in moment.near.cars:
        lateral, longitudinal = side_and_overlap(frame.pos, frame.ori, car)
        if abs(longitudinal) >= CAR_LENGTH_M:
            continue
        if LANE_MIN_M <= lateral <= LANE_MAX_M:
            left = True
        elif -LANE_MAX_M <= lateral <= -LANE_MIN_M:
            right = True
    return left, right


class Spotter:
    def __init__(self):
        self.left = False
        self.right = False
        self.three_wide = False
        self.empty_since = None      # when both sides last became empty
        self.alongside_since = None  # when this overlap began
        self.last_said_at = None     # the last spotter line, for "still there"
        self.still_every = STILL_THERE_EVERY_S

    def call(self, kind, text, now):
        self.last_said_at = now
        return Call(seat="spotter", kind=kind, sim_time=now, priority=SPOTTER, ttl=CALL_TTL_S,
                    conclusion=text, template=text, urgent=True)

    def update(self, moment):
        now = moment.now
        # in the garage the cars in the next stalls are "alongside" (23 Sep: "car right,
        # three wide" while waiting for the race): the spotter only works on track, moving
        if not on_track(moment):
            self.left = self.right = self.three_wide = False
            self.empty_since = None
            self.alongside_since = None
            return []
        left, right = sides_taken(moment)
        calls = []

        if left and right and not self.three_wide:
            calls.append(self.call("THREE_WIDE", "Three wide. You're in the middle.", now))
            self.three_wide = True
        elif left and not self.left and not right:
            calls.append(self.call("CAR_LEFT", "Car left.", now))
        elif right and not self.right and not left:
            calls.append(self.call("CAR_RIGHT", "Car right.", now))
        if calls:
            self.still_every = STILL_THERE_EVERY_S

        if left or right:
            # someone is beside me: remember which side, and cancel any pending "clear"
            if self.alongside_since is None:
                self.alongside_since = now
            elif not calls and now - (self.last_said_at or self.alongside_since) >= self.still_every:
                calls.append(self.call("STILL_THERE", "Still there.", now))
                self.still_every = min(self.still_every * 2, STILL_THERE_MAX_S)
            self.left = left
            self.right = right
            self.empty_since = None
        elif self.left or self.right:
            # both sides just emptied. Wait before saying "clear": a car flickering at the
            # edge of the lane must not fire "car left" twice
            if self.empty_since is None:
                self.empty_since = now
            if now - self.empty_since >= CLEAR_AFTER_S:
                if self.empty_since - self.alongside_since >= CLEAR_NEEDS_ALONGSIDE_S:
                    calls.append(self.call("CLEAR", "Clear.", now))
                self.alongside_since = None
                self.left = False
                self.right = False
                self.three_wide = False
                self.empty_since = None
        return calls
