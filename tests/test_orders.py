"""Standing orders (the adaptability brick, 26 Sep): what he tells the team stands for the race."""

import sqlite3

from orders import StandingOrders, current_plan
from radio.governor import Governor
from radio.calls import Call, STRATEGY, MEMORY
from coach.agent import RaceAgent
from coach.answer_checks import split_orders
from coach.llm import Budget
from test_agent import ScriptedModel, Message, snapshot_at_lap_4


def fuel_call(words, spare=0.3):
    return Call(
        seat="strategist",
        kind="FUEL",
        sim_time=100.0,
        priority=STRATEGY,
        ttl=25.0,
        conclusion=words,
        template=words,
        facts={"spare_laps": spare, "laps_left": 3},
    )


def test_his_own_words_set_push_and_are_said_back():
    orders = StandingOrders()
    heard = orders.hear(
        "Don't give me that bullshit, we will push, no holding back man.", 4, 500.0
    )
    words, given = heard
    assert [(o.topic, o.stance) for o in given] == [("pace", "push")]
    assert words.startswith("Copy. We push")


def test_a_question_is_not_an_order():
    assert StandingOrders().hear("Should we push?", 4, 500.0) is None
    assert StandingOrders().hear("How's the fuel", 4, 500.0) is None


def test_push_silences_lift_and_coast_and_says_a_real_box_once_as_his_call():
    orders = StandingOrders()
    orders.hear("we push, no holding back", 4, 500.0)
    assert (
        orders.adjust(
            fuel_call(
                "Fuel's tight, 0.3 laps spare. Lift and coast into the big stops."
            )
        )
        is None
    )
    box = orders.adjust(
        fuel_call(
            "Box this lap for fuel. Fuel won't make the flag, short by 0.7 laps.", 0.7
        )
    )
    assert (
        box.template.startswith("You said push, your call.")
        and "short by 0.7" in box.template
    )
    assert (
        orders.adjust(
            fuel_call(
                "Box this lap for fuel. Fuel won't make the flag, short by 0.8 laps.",
                0.8,
            )
        )
        is None
    )


def test_push_still_hears_once_that_the_fuel_will_not_make_the_flag():
    # replay of 25 Sep 12:31 (26 Sep): under "we push" the radio stayed silent on "short by 0.1
    # laps", yet his order was said back as "I'll only come back on fuel if it won't make the flag"
    orders = StandingOrders()
    orders.hear("we push, no holding back", 4, 500.0)
    short = fuel_call(
        "Fuel's short by 0.1 laps. Lift and coast every braking zone and short-shift, "
        "or you won't make it.",
        0.1,
    )
    short.facts["verdict"] = "save"
    said = orders.adjust(short)
    assert said is short
    assert said.template.startswith("You said push, your call.")
    assert "short by 0.1 laps" in said.template
    again = fuel_call(
        "Fuel's short by 0.2 laps. Lift and coast every braking zone and short-shift, "
        "or you won't make it.",
        0.2,
    )
    again.facts["verdict"] = "save"
    assert orders.adjust(again) is None  # said once: his call now
    at_the_line = fuel_call(
        "We're short on fuel. Lift and coast every braking zone.", 0.3
    )
    at_the_line.facts["verdict"] = "short"
    assert orders.adjust(at_the_line) is None  # the same news from the other check
    box = fuel_call(
        "Box this lap for fuel. Fuel won't make the flag, short by 0.7 laps.", 0.7
    )
    box.facts["verdict"] = "box"
    assert orders.adjust(box) is box  # worse news: said once more


def test_a_save_order_is_reopened_once_when_the_fuel_turns_fine():
    # Leclerc, Singapore 2025: "Tell me when I can push again." Before (27 Sep): under his own
    # "we save" the strategist said "Fuel's fine to the flag... Push." - against his order
    orders = StandingOrders()
    orders.hear("ok save, lift and coast", 3, 400.0)
    assert orders.get("pace") == "save"
    fine = fuel_call("Fuel's fine to the flag, 1.2 laps spare. Push.", 1.2)
    fine.facts["verdict"] = "fine"
    said = orders.adjust(fine)
    assert (
        said.template
        == "You said save. Fuel's fine now, 1.2 laps spare. You can push again, your call."
    )
    assert orders.get("pace") == "save"  # his order stands until he changes it
    again = fuel_call("Fuel's fine to the flag, 1.4 laps spare. Push.", 1.4)
    again.facts["verdict"] = "fine"
    assert orders.adjust(again) is None  # said once: never nag
    tight = fuel_call(
        "Fuel's tight, 0.3 laps spare. Lift and coast into the big stops.", 0.3
    )
    tight.facts["verdict"] = "tight"
    assert orders.adjust(tight) is tight  # saving again matches his order


def test_the_save_reopen_says_energy_when_energy_is_the_limit():
    # replay of 24 Sep: the limit was virtual energy, and the line said "Fuel's fine now"
    orders = StandingOrders()
    orders.hear("ok save", 3, 400.0)
    fine = fuel_call("Energy's fine to the flag, 1.8 laps spare. Push.", 1.8)
    fine.facts["verdict"] = "fine"
    assert orders.adjust(fine).template.startswith(
        "You said save. Energy's fine now, 1.8 laps spare."
    )


def test_what_he_asks_for_he_gets_whatever_the_orders():
    orders = StandingOrders()
    orders.hear("no more coaching", 4, 500.0)
    habit = Call(
        seat="memory",
        kind="CORNER_HABIT",
        sim_time=1.0,
        priority=MEMORY,
        ttl=5.0,
        conclusion="Esses next.",
        template="Esses next.",
    )
    assert orders.adjust(habit) is None
    asked = fuel_call("Fuel's tight.")
    asked.asked = True
    orders.hear("we push", 4, 500.0)
    assert orders.adjust(asked) is asked


def test_the_governor_drops_what_his_orders_say_not_to_say():
    governor = Governor()
    governor.orders = StandingOrders()
    governor.orders.hear("we push", 4, 500.0)
    assert governor.offer(fuel_call("Fuel's tight, 0.3 laps spare.")) is False
    assert governor.dropped[-1][1] == "his_order"


def test_a_call_already_waiting_when_he_gives_an_order_does_not_go_out():
    # replay of 23 Sep with his orders scripted (26 Sep): "Lap 1: survive it" was queued in the
    # same frame as "no more coaching" and still went out right after "Copy. No more coaching"
    governor = Governor()
    governor.orders = StandingOrders()
    habit = Call(
        seat="memory",
        kind="LAP_ONE_HABIT",
        sim_time=106.0,
        priority=MEMORY,
        ttl=30.0,
        conclusion="Lap 1: survive it.",
        template="Lap 1: survive it.",
    )
    assert governor.offer(habit) is True
    governor.orders.hear("no more coaching", 1, 106.0)
    assert governor.step(106.1, in_corner=False) is None
    assert governor.dropped[-1] == (habit, "his_order")


def test_a_box_call_already_waiting_still_goes_out_under_push():
    # the one fact that beats a push order: running out of fuel ends the race
    governor = Governor()
    governor.orders = StandingOrders()
    box = fuel_call(
        "Box this lap for fuel. Fuel won't make the flag, short by 0.7 laps.", 0.7
    )
    assert governor.offer(box) is True
    governor.orders.hear("we push", 4, 100.0)
    assert governor.step(100.5, in_corner=False) is box


def test_back_to_normal_clears_and_the_coach_sees_the_history():
    orders = StandingOrders()
    orders.hear("fight everyone", 2, 100.0)
    orders.hear("gaps every lap", 3, 200.0)
    assert orders.get("fight") == "fight" and orders.gaps_every_lap()
    orders.hear("okay back to normal", 4, 300.0)
    assert orders.orders == {}
    seen = orders.for_coach()
    assert seen["standing_orders"] == ["none: your calls stand"]
    assert seen["his_decisions_this_race"][-1] == "lap 4: all=back_to_normal"


def test_an_order_for_good_outlives_the_race():
    conn = sqlite3.connect(":memory:")
    first = StandingOrders()
    first.hear("never tell me the gaps, no gaps", 1, 10.0)
    first.save(conn)
    next_race = StandingOrders()
    next_race.load(conn)
    assert (
        next_race.get("gaps") == "off"
        and next_race.orders["gaps"].source == "remembered"
    )


def test_the_coach_can_set_an_order_in_words_the_phrases_miss():
    assert split_orders("ORDER: pace=push\nCALL: DEFEND\nCopy, mate, we go.") == (
        [("pace", "push")],
        "CALL: DEFEND\nCopy, mate, we go.",
    )
    model = ScriptedModel(
        Message(
            content="ORDER: fight=fight\nCALL: DEFEND\nCopy, mate. It fights for it."
        )
    )
    agent = RaceAgent(Budget(), client=model)
    agent.orders = StandingOrders()
    answer, info = agent.think(
        "that guy is getting nothing from me the whole race", snapshot_at_lap_4()
    )
    assert info["orders"] == [("fight", "fight")] and "ORDER" not in answer


def test_a_fight_order_binds_the_coach():
    snapshot = snapshot_at_lap_4()
    snapshot.orders = StandingOrders()
    snapshot.orders.hear("nobody gets past", 3, 100.0)
    assert not snapshot.check_orders("LET BY", None)[0]
    assert snapshot.check_orders("LET BY", "damage")[0]
    assert snapshot.check_orders("DEFEND", None)[0]


def test_the_plan_follows_the_race_and_his_orders():
    orders = StandingOrders()
    assert current_plan(orders, {"verdict": "tight"}, False, 4).startswith(
        "fuel is tight"
    )
    orders.hear("we push", 4, 500.0)
    assert current_plan(orders, {"verdict": "tight"}, False, 4).startswith(
        "push to the flag"
    )
    assert current_plan(orders, {"verdict": "box"}, True, 1).startswith("box this lap")
    assert "last lap" in current_plan(orders, "not known yet", False, 1)
