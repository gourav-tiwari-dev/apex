"""Le Mans Ultimate's numbers, by name, in one place.

Every value is the game's own, from its shared memory header (lmu_data.py); the comments add
what his races showed. Before 27 Sep 2026, 15 files defined these again under different
names (phase 8 alone was FLAG, FLAG_OUT, GAME_PHASE_OVER and SESSION_OVER), and 2 more used the
bare numbers.
"""

# mSession: 0 test day, 1-4 practice, 5-8 qualifying, 9 warm-up, 10-13 race
QUALIFYING_SESSIONS = range(5, 9)
RACE_SESSIONS = range(10, 14)

# mGamePhase: where the whole session is
FORMATION_LAP = 3
GREEN_FLAG = 5  # racing
SAFETY_CAR = 6  # the header's "full course yellow / safety car"
SESSION_OVER = 8  # the leader has taken the flag, or the qualifying clock ran out

# mWheels order
WHEEL_NAMES = ("front left", "front right", "rear left", "rear right")

# mFlag: the flag shown to one car (only 0, green, or 6)
BLUE_FLAG = 6

# mSectorFlag, one per sector: 1 is a local yellow, nothing else is. Measured on all 5 race
# tapes (26 Sep): with 1 a slow car sat in that sector 61.8% of the time, with 3 2.5% and with
# 11 (green) 1.7%. 3 shows before the race and once the clock runs out, 2 when the session is
# over: neither is a yellow
SECTOR_YELLOW = 1
