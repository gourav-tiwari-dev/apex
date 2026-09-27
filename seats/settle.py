"""Has the race start settled? Until it has, only the spotter, flags and his answers speak.

His rule (24 Sep 2026): "coaching on the chaos at the race start is just noise, that time I
need to focus. It should decide when the race is settled." Not "skip lap 1": a clean start
can settle by the third corner, a brawl can run into lap 2.

Settled = calm for SETTLE_S in a row, where calm means:
  - nobody alongside him (the spotter's own geometry)
  - no place changes between him and the same-class cars up to 2 places either side
  - no yellow in any sector
A stable train counts as settled: close cars are fine, cars swapping places are not.

The chaos starts at lights out, and again at a restart (full-course yellow back to green).
When it settles, the engineer says ONE summary line, in code's own words.
"""

from radio import Call, ENGINEER
from race_state import same_class_neighbours, said_place, class_place
from seats.spotter import sides_taken
from game.constants import GREEN_FLAG, RACE_SESSIONS, SAFETY_CAR, SECTOR_YELLOW

# GUESSED, then checked on the 24 Sep lap 1s (see test_settle.py): 15 s with nobody
# alongside and nobody swapping places around him
SETTLE_S = 15.0
SAY_GAP_UP_TO_S = 30.0
# his call, 25 Sep: the start took 167 s to "settle" and swallowed his 3-car pass on the
# straight (7 praise lines dropped). The chaos now lasts 60 s at most.
CHAOS_MAX_S = 60.0
NEIGHBOUR_PLACES = 2
# a brawl that never calms still hands over to the engineer after this many laps of chaos
SETTLE_WITHIN_LAPS = 2
SUMMARY_TTL_S = 20.0

PLACE_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}


def places_moved(start, now):
    moved = start - now
    if moved == 0:
        return "held position"
    words = PLACE_WORDS.get(abs(moved), str(abs(moved)))
    if moved > 0:
        return f"up {words}"
    return f"down {words}"


def neighbourhood(race):
    """Who sits in the places around him, in order: a change here is a place swap."""
    me = race.me
    around = []
    for opponent in race.opponents:
        if opponent.car_class != me.car_class:
            continue
        if abs(opponent.place - me.place) <= NEIGHBOUR_PLACES:
            around.append((opponent.place, opponent.id))
    around.sort()
    return me.place, tuple(around)


def yellow_anywhere(session):
    for flag in session.sector_flags:
        if flag == SECTOR_YELLOW:
            return True
    return False


class RaceSettle:
    def __init__(self):
        self.settled = True  # practice, qualifying, or Apex started mid-race
        self.last_phase = None
        self.calm_since = None
        self.last_neighbourhood = None
        self.place_at_start = None
        self.class_place_at_start = None
        self.chaos_lap = None  # the lap the chaos began on (lights out or a restart)
        self.chaos_since = None  # when it began

    def is_race(self, moment):
        if moment.session_type is not None:
            return moment.session_type in RACE_SESSIONS
        return moment.race.session.session in RACE_SESSIONS

    def update(self, moment):
        race = moment.race
        if race is None or race.me is None:
            return []
        phase = race.session.game_phase
        went_green = (
            phase == GREEN_FLAG
            and self.last_phase is not None
            and self.last_phase != GREEN_FLAG
        )
        # 24 Sep: Apex was restarted mid lap 1 (P24) and would have counted it as settled
        joined_on_lap_1 = (
            self.last_phase is None and phase == GREEN_FLAG and race.me.laps == 0
        )
        if (went_green or joined_on_lap_1) and self.is_race(moment):
            # lights out, or a restart after a full-course yellow: chaos until proven calm
            self.settled = False
            self.calm_since = None
            self.last_neighbourhood = None
            self.chaos_lap = moment.lap_count
            self.chaos_since = moment.now
            if self.place_at_start is None or self.last_phase != SAFETY_CAR:
                self.place_at_start = race.me.place
                self.class_place_at_start = class_place(
                    race, race.me.place, race.me.car_class
                )
        self.last_phase = phase
        if self.settled:
            return []

        now = moment.now
        brawl_too_long = moment.lap_count - self.chaos_lap >= SETTLE_WITHIN_LAPS or (
            self.chaos_since is not None and now - self.chaos_since >= CHAOS_MAX_S
        )
        if not self.calm(moment):
            self.calm_since = None
            if not brawl_too_long:
                return []
        elif self.calm_since is None:
            self.calm_since = now
        calm_long_enough = (
            self.calm_since is not None and now - self.calm_since >= SETTLE_S
        )
        if not calm_long_enough and not brawl_too_long:
            return []
        self.settled = True
        return [self.summary(race, now, moment.model)]

    def calm(self, moment):
        race = moment.race
        left, right = sides_taken(moment)
        if left or right:
            return False
        if yellow_anywhere(race.session):
            return False
        if moment.new_race:
            around = neighbourhood(race)
            changed = (
                self.last_neighbourhood is not None
                and around != self.last_neighbourhood
            )
            self.last_neighbourhood = around
            if changed:
                return False
        return True

    def summary(self, race, now, model=None):
        me = race.me
        ahead, gap_ahead, behind, gap_behind = same_class_neighbours(race, model)
        now_in_class = class_place(race, me.place, me.car_class)
        start_in_class = self.class_place_at_start or now_in_class
        words = [
            f"Settled. {said_place(race)}, {places_moved(start_in_class, now_in_class)}."
        ]
        # only a gap worth hearing (live 25 Sep, online: "Car ahead 148.4. Car behind -213.6.")
        if (
            ahead is not None
            and gap_ahead is not None
            and 0 <= gap_ahead <= SAY_GAP_UP_TO_S
        ):
            words.append(f"Car ahead {gap_ahead:.1f}.")
        if (
            behind is not None
            and gap_behind is not None
            and 0 <= gap_behind <= SAY_GAP_UP_TO_S
        ):
            words.append(f"Car behind {gap_behind:.1f}.")
        if len(words) == 1:
            words.append("Nobody close. Race the track.")
        text = " ".join(words)
        return Call(
            seat="race_engineer",
            kind="SETTLED",
            sim_time=now,
            priority=ENGINEER,
            ttl=SUMMARY_TTL_S,
            conclusion=text,
            template=text,
            phrase=False,
            facts={
                "place": me.place,
                "start_place": self.place_at_start,
                "gap_ahead": gap_ahead,
                "gap_behind": gap_behind,
            },
        )
