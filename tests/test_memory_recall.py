from dataclasses import replace

from seats.memory_recall import MemoryRecall
from test_seats import moment, race, kinds

CORNERS = [{"name": "T11 Parabolica", "start": 4992, "end": 5584}]
HABIT = {
    "fact_id": 7,
    "subject": "T11 Parabolica",
    "summary": "off track at T11 Parabolica: 3 times in 2 drives",
}


def at(seat, t, lap_dist, phase=5, corner=None):
    m = replace(
        moment(t, race(t, {"game_phase": phase}), corner=corner), corners=CORNERS
    )
    m.frame.lap_dist = lap_dist
    return seat.update(m)


def test_corner_habit_reminder_before_the_corner_once_per_race():
    seat = MemoryRecall(corner_habits={"T11 Parabolica": HABIT})
    assert at(seat, 1.0, 4000.0) == []  # too far away
    calls = at(seat, 2.0, 4700.0)  # 292 m before
    assert kinds(calls) == ["CORNER_HABIT"]
    assert calls[0].evidence == {"fact_id": 7}
    assert at(seat, 3.0, 4750.0) == []  # once per race


def test_lap_one_habit_on_the_formation_lap():
    fact = {
        "fact_id": 3,
        "subject": "me",
        "summary": "lap 1 trouble (contact, off or spin) in 2 of your last 3 races",
    }
    seat = MemoryRecall(lap_one=fact)
    assert kinds(at(seat, 0.0, 100.0, phase=3)) == ["LAP_ONE_HABIT"]
    assert at(seat, 1.0, 200.0, phase=3) == []


def test_no_facts_means_silence():
    seat = MemoryRecall()
    assert at(seat, 1.0, 4700.0, phase=3) == []
