"""What this race says about each car: how many incidents it has had today (walls
too), and how often it has hit him.

Racecraft reads it to warn him about a car to give room to, and to tell a gift from
a pass: a car that hit something seconds ago was not beaten, it crashed."""

from game.race_snapshot import identity


GIFT_IMPACT_S = 5.0  # a car that hit something this recently was not beaten, it crashed
INCIDENT_MERGE_S = (
    5.0  # hits closer than this are one incident (LMU logs several per crash)
)


REPUTATION_INCIDENTS = 2  # this many incidents today = a car to give room to


class Reputation:
    """What this race says about each car: incidents (any impact, walls too) and hits on him."""

    def __init__(self):
        self.incidents = {}  # car id -> count this race
        self.last_impact = {}  # car id -> the game's last impact time for that car
        self.hits_on_me = {}  # identity -> contacts with him this race

    def see_race(self, race):
        for opponent in race.opponents:
            impact = opponent.last_impact_time
            if impact is None or impact <= 0:
                continue
            last = self.last_impact.get(opponent.id)
            if last is None:
                self.incidents.setdefault(
                    opponent.id, 0
                )  # an impact from before we watched
            elif impact > last + INCIDENT_MERGE_S:
                self.incidents[opponent.id] = self.incidents.get(opponent.id, 0) + 1
            if last is None or impact > last:
                self.last_impact[opponent.id] = impact

    def hit_recently(self, car_id, now):
        impact = self.last_impact.get(car_id)
        return (
            impact is not None
            and 0 <= now - impact <= GIFT_IMPACT_S
            and self.incidents.get(car_id, 0) > 0
        )

    def words(self, car):
        hits = self.hits_on_me.get(identity(car), 0)
        if hits == 1:
            return "It's already hit you once."
        if hits > 1:
            return f"It's hit you {hits} times."
        incidents = self.incidents.get(car.id, 0)
        if incidents >= REPUTATION_INCIDENTS:
            return f"That car's had {incidents} incidents today."
        return None
