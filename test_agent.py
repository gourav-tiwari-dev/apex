"""The push-to-talk agent, with a scripted model: no API calls, no cost."""
import json
import time
from dataclasses import replace

from agent import RaceAgent, Snapshot, check_answer, numbers_seen, pace_words, trend_words, lap_text
from answers import needs_agent
from radio import Budget, Governor
from seats.performance import PerformanceEngineer
from seats.race_engineer import RaceEngineer
from seats.racecraft import Racecraft
from seats.strategist import Strategist
from test_seats import race, behind_car


class Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class ToolCall:
    def __init__(self, name, arguments="{}"):
        self.id = "call_" + name
        self.function = Function(name, arguments)


class Message:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none=True):
        dumped = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            dumped["tool_calls"] = [{"id": c.id, "type": "function",
                                     "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                    for c in self.tool_calls]
        return dumped


class Usage:
    prompt_tokens = 500
    completion_tokens = 40


class Response:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message, "finish_reason": "stop"})()]
        self.usage = Usage()


class ScriptedModel:
    """Plays back the model turns it is given, and keeps what it was sent."""
    def __init__(self, *turns):
        self.turns = list(turns)
        self.sent = []
        self.chat = self
        self.completions = self

    def create(self, **request):
        self.sent.append(request["messages"])
        return Response(self.turns.pop(0))


def snapshot_at_lap_4():
    zino = replace(behind_car(0.0), driver="Chabbi Zino", steam_id=77, time_behind_leader=8.8, last_lap=238.9)
    kossman = replace(behind_car(0.0), id=3, driver="Jarek Kossman", steam_id=33, place=4,
                      time_behind_leader=5.4, last_lap=240.4)
    now = race(1175.0, {"time_remaining": 210.0}, {"place": 5, "time_behind_leader": 8.0, "last_lap": 240.0},
               opponents=[kossman, zino])
    performance = PerformanceEngineer()
    return Snapshot(now, 5, 9000.0, [], RaceEngineer(), Strategist(), performance,
                    Racecraft(performance), Governor(), ["lap 1 contact in 3 of 5 races"], {})


def ask(agent, question, snapshot):
    agent.ask(question, snapshot, 1175.0)
    for _ in range(200):
        done = agent.finished()
        if done:
            return done[0]
        time.sleep(0.01)
    raise AssertionError("no answer")


def test_long_or_unmatched_questions_go_to_the_agent_short_ones_stay_fixed():
    assert needs_agent("The car behind me is diving at me aggressively, do I defend or let him go?")
    assert needs_agent("Should I pit?")                 # not on the fixed list
    assert not needs_agent("Who's behind me?")
    assert not needs_agent("Quiet for two laps please, I need to focus on this fight")   # always fixed


def test_directions_are_words_never_a_sign():
    assert pace_words(240.4, 240.0) == "0.4 s a lap slower than you"
    assert pace_words(238.9, 240.0) == "1.1 s a lap quicker than you"
    assert trend_words("behind", 0.2) == "closing on you: 0.2 s since the last lap"
    assert trend_words("ahead", -0.3) == "pulling away from you: 0.3 s lost since the last lap"
    assert lap_text(239.96) == "4:00.0"


def test_the_snapshot_tells_the_truth_about_the_cars_around_him():
    snapshot = snapshot_at_lap_4()
    assert snapshot.picture["behind"]["their_pace"].startswith("1.1 s a lap quicker than you")
    assert snapshot.picture["ahead"]["their_pace"].startswith("0.4 s a lap slower than you")
    assert snapshot.run_tool("driver", {"who": "behind"})["driver"] == "Chabbi Zino"
    assert snapshot.run_tool("driver", {"who": "zino"})["where"] == "0.8 s behind you"
    assert "error" in snapshot.run_tool("driver", {"who": "Hamilton"})


def test_the_agent_uses_tools_then_answers_and_the_cost_is_counted():
    model = ScriptedModel(
        Message(tool_calls=[ToolCall("driver", json.dumps({"who": "behind"}))]),
        Message(content="CALL: DEFEND\nDefend, mate. Last lap, 1.1 a lap quicker, not 2. One line into Tertre Rouge."))
    agent = RaceAgent(Budget(), client=model)
    result = ask(agent, "He's 2 seconds faster, defend or let him go?", snapshot_at_lap_4())
    call = result["call"]
    # the CALL line is for the pit wall: logged, never spoken
    assert call.template == "Defend, mate. Last lap, 1.1 a lap quicker, not 2. One line into Tertre Rouge."
    assert call.facts["call"] == "DEFEND" and call.facts["override"] is None
    assert call.asked and not call.phrase and call.kind == "ANSWER_AGENT"
    assert call.facts["tools"] == ["race_picture", "driver"]
    assert len(result["costs"]) == 2
    # the race picture went with the question, so the first round could skip it
    assert "Race picture right now" in model.sent[0][1]["content"]


def test_a_refused_answer_gets_one_rewrite_then_an_honest_fallback():
    model = ScriptedModel(Message(content="CALL: DEFEND\nChabbi Zino is quicker, hold it."),
                          Message(content="CALL: DEFEND\nThe car behind is 1.1 a lap quicker. Defend, one line."))
    result = ask(RaceAgent(Budget(), client=model), "defend or let the car behind go?", snapshot_at_lap_4())
    assert result["call"].template == "The car behind is 1.1 a lap quicker. Defend, one line."
    assert "name" in result["call"].facts["refused"]
    stubborn = ScriptedModel(Message(content="CALL: DEFEND\nZino's quicker."), Message(content="CALL: DEFEND\nZino's still quicker."))
    result = ask(RaceAgent(Budget(), client=stubborn), "defend or let the car behind go?", snapshot_at_lap_4())
    assert result["call"].template == "No clean answer on that one, mate. Ask it another way."


def test_the_answer_gate():
    known = numbers_seen("he's 2 seconds faster", '{"gap_s": 0.8, "their_pace": "1.1 s a lap quicker than you"}')
    assert check_answer("Let the car behind go. 1.1 a lap quicker, not 2.", known)[0]
    assert not check_answer("Let Chabbi Zino go. 1.1 a lap quicker.", known, names=["chabbi zino"])[0]
    assert not check_answer("Let Zino go. 1.1 a lap quicker.", known, names=["chabbi zino"])[0]
    assert not check_answer("Chabbi Zino is 5 seconds quicker.", known)[0]             # invented
    assert not check_answer("Chabbi Zino is 12 km/h quicker at Arnage.", known)[0]    # a speed
    assert not check_answer("Let him go.", known)[0]                                  # he/him
    assert not check_answer("Do you want to defend?", known)[0]                        # a question
    assert not check_answer("Maybe let Chabbi Zino go.", known)[0]                     # hedging


def test_over_budget_the_agent_says_so_without_calling_the_model():
    model = ScriptedModel()
    result = ask(RaceAgent(Budget(cap_rs=0.0), client=model), "should I pit?", snapshot_at_lap_4())
    assert "budget" in result["call"].template
    assert model.sent == []


def test_judgment_questions_go_to_the_agent_however_short():
    assert needs_agent("Can I catch the car ahead?")        # 24 Sep: got the bare gap
    assert needs_agent("Should I pit?")
    assert not needs_agent("What's the gap ahead?")


def test_code_does_the_race_maths_so_every_answer_agrees():
    snapshot = snapshot_at_lap_4()
    behind = snapshot.picture["behind"]
    # Zino 0.8 s back, 1.1 s a lap quicker: there within a lap
    assert behind["race_maths"]["at_this_pace"] == "they reach you in about 0.7 laps: before the flag"
    assert behind["race_maths"]["to_keep_them_behind"] == "lap 3:58.9 or quicker"
    ahead = snapshot.picture["ahead"]
    assert ahead["race_maths"]["at_this_pace"] == "you reach them in about 6.5 laps: not before the flag"
    assert ahead["fight"].startswith("not a fight yet")


def test_a_lap_the_game_did_not_post_falls_back_to_the_best_lap_and_says_so():
    snapshot = snapshot_at_lap_4()
    kossman = replace(behind_car(0.0), id=3, driver="Jarek Kossman", steam_id=33, place=4,
                      time_behind_leader=5.4, last_lap=-1.0, best_lap=240.5)
    now = race(1175.0, {"time_remaining": 210.0}, {"place": 5, "time_behind_leader": 8.0, "last_lap": 240.0},
               opponents=[kossman])
    performance = PerformanceEngineer()
    picture = Snapshot(now, 5, 9000.0, [], RaceEngineer(), Strategist(), performance, Racecraft(performance),
                       Governor(), [], {}).picture
    assert picture["ahead"]["their_lap"] == "4:00.5 (best lap, last lap not posted)"
    nobody = replace(kossman, best_lap=-1.0)
    now = replace(now, opponents=[nobody])
    picture = Snapshot(now, 5, 9000.0, [], RaceEngineer(), Strategist(), performance, Racecraft(performance),
                       Governor(), [], {}).picture
    assert picture["ahead"]["their_pace"].startswith("not known")


def test_a_judgment_anywhere_in_the_question_goes_to_the_agent():
    assert needs_agent("realistically which position can we get")     # 24 Sep: got a bare "P5."
    assert needs_agent("why am I slow at Arnage")
    assert not needs_agent("what position")


def test_the_fight_call_is_made_by_code():
    from agent import team_call
    # 24 Sep: last lap, 0.2 s behind, only 0.3 s a lap quicker - the model said "let it go"
    assert team_call("behind", 0.2, 239.7, 240.0, 1).startswith("DEFEND: last lap")
    assert team_call("behind", 0.3, 239.8, 240.0, 3).startswith("DEFEND: similar pace")
    assert team_call("behind", 0.5, 238.0, 240.0, 3).startswith("LET BY: 2.0 s a lap quicker")
    assert team_call("behind", 0.5, 238.0, 240.0, 1).startswith("DEFEND")       # even quicker cars, last lap
    assert team_call("ahead", 0.4, 240.6, 240.0, 3).startswith("ATTACK")
    assert team_call("ahead", 0.4, 240.1, 240.0, 3).startswith("FOLLOW")
    assert team_call("behind", 1.7, 239.0, 240.0, 3) is None                    # not a fight yet


def test_an_override_needs_a_reason_the_data_backs():
    from agent import split_call
    assert split_call("CALL: LET BY | OVERRIDE: contact\nLet Zino go.") == ("LET BY", "contact", "Let Zino go.")
    snapshot = snapshot_at_lap_4()                       # last lap: the team says DEFEND
    assert snapshot.check_call("DEFEND", None)[0]
    assert not snapshot.check_call(None, None)[0]        # in a fight the CALL line is required
    refused = snapshot.check_call("LET BY", "contact")   # no contact in the data
    assert not refused[0] and "supports no override" in refused[1]
    # the same override when that car HAS hit him: accepted
    zino = snapshot.drivers["behind"]
    zino["contacts_with_you_this_race"] = 2
    snapshot.contacts_in_fight = 2
    assert snapshot.check_call("LET BY", "contact")[0]
    assert not snapshot.check_call("LET BY", "damage")[0]      # a reason the data does not show


def test_an_unbacked_override_ends_in_the_honest_line_not_on_the_radio():
    model = ScriptedModel(Message(content="CALL: LET BY | OVERRIDE: damage\nLet Chabbi Zino go, the car's hurt."),
                          Message(content="CALL: LET BY | OVERRIDE: damage\nLet Chabbi Zino go."))
    result = ask(RaceAgent(Budget(), client=model), "defend or let go?", snapshot_at_lap_4())
    assert result["call"].template == "No clean answer on that one, mate. Ask it another way."


def test_a_slow_model_still_gets_the_team_call_through():
    class Slow:
        def __init__(self):
            self.chat = self
            self.completions = self
        def create(self, **request):
            raise TimeoutError("provider took 60 s")      # 24 Sep: 60 s, 26 s, 1.3 s in a row
    result = ask(RaceAgent(Budget(), client=Slow()), "defend or let go?", snapshot_at_lap_4())
    assert result["call"].template.startswith("Radio's lagging, mate. Team says DEFEND: last lap")


def test_the_fallback_states_the_facts_that_could_change_the_call():
    from agent import fallback
    snapshot = snapshot_at_lap_4()
    snapshot.contacts_in_fight = 2                     # the car behind has hit him
    assert fallback(snapshot, "the car behind keeps hitting me").endswith("Careful: that car has already hit you.")


def test_a_follow_up_carries_the_last_exchange():
    model = ScriptedModel(Message(content="CALL: DEFEND\nDefend, mate. One line."),
                          Message(content="CALL: DEFEND\nStill defend, mate. One line, no weaving."))
    agent = RaceAgent(Budget(), client=model)
    ask(agent, "The car behind is all over me, what do I do?", snapshot_at_lap_4())
    ask(agent, "But it keeps hitting me.", snapshot_at_lap_4())
    second_question = model.sent[1][1]["content"]
    assert 'Earlier on the radio, oldest first: he asked "The car behind is all over me' in second_question


def test_follow_ups_carry_the_last_three_exchanges_oldest_first():
    model = ScriptedModel(*[Message(content="CALL: DEFEND\nDefend it, mate. One line.") for n in range(5)])
    agent = RaceAgent(Budget(), client=model)
    for n in range(4):
        ask(agent, f"Question {n}", snapshot_at_lap_4())
    ask(agent, "And now?", snapshot_at_lap_4())
    last = model.sent[4][1]["content"]
    assert '"Question 0"' not in last
    assert last.index('"Question 1"') < last.index('"Question 2"') < last.index('"Question 3"')


def test_the_fallback_gives_the_fight_call_only_to_a_fight_question():
    from agent import fallback
    snapshot = snapshot_at_lap_4()
    assert fallback(snapshot, "What's the strategy for this race?") == "Radio's lagging, mate. Ask me again."
    assert fallback(snapshot, "The car behind is diving at me").startswith("Radio's lagging, mate. Team says DEFEND")


def test_a_fight_question_goes_to_the_agent_however_short():
    assert needs_agent("Car ahead is defending aggressively")      # live 24 Sep: got the bare gap
    assert needs_agent("He keeps diving")


# ---- v3 step 5b: the wider tools --------------------------------------------------------------

def test_the_new_tools_answer_from_the_snapshot():
    snapshot = snapshot_at_lap_4()
    table = snapshot.run_tool("standings", {})
    assert table["your_place_overall"] == 5 and all("driver" not in row for row in table["cars"])
    assert snapshot.run_tool("session", {})["session"] == "race"
    assert snapshot.run_tool("lap_history", {})["note"].startswith("no full lap")
    assert snapshot.run_tool("calculator", {"expression": "(240.4 - 240.0) * 3"})["result"] == 1.2
    assert "error" in snapshot.run_tool("calculator", {"expression": "__import__('os')"})
    rules = snapshot.run_tool("knowledge", {"topic": "can I overtake under yellow"})
    assert rules["sections"][0]["topic"] == "Yellow flag"
    assert "plan_in_order" in snapshot.run_tool("strategy", {})
    assert "tyre_pressures_kpa" in snapshot.run_tool("car", {})


def test_a_reminder_is_set_only_for_a_later_lap():
    snapshot = snapshot_at_lap_4()                       # lap 5 of a race ending on lap 5
    assert "no lap 8" in snapshot.run_tool("remind_me", {"lap": 8, "what": "box this lap"})["error"]
    snapshot.picture["laps_to_go"] = 5                   # now it ends on lap 9
    assert "error" in snapshot.run_tool("remind_me", {"lap": 3, "what": "box"})
    assert "error" in snapshot.run_tool("remind_me", {"lap": 12, "what": "box"})
    assert snapshot.run_tool("remind_me", {"lap": 8, "what": "box this lap"})["ok"]
    assert snapshot.actions == [{"remind_lap": 8, "what": "box this lap"}]


def test_the_database_tool_reads_only(tmp_path):
    import sqlite3
    from race_tools import query_db
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sessions (id INTEGER, track TEXT)")
    conn.execute("INSERT INTO sessions VALUES (1, 'Le Mans')")
    conn.commit()
    conn.close()
    assert query_db(str(db), "SELECT track FROM sessions")["rows"] == [["Le Mans"]]
    assert "error" in query_db(str(db), "DELETE FROM sessions")
    assert "error" in query_db(str(db), "SELECT 1; DROP TABLE sessions")
    assert "error" in query_db(str(db), "WITH x AS (SELECT 1) INSERT INTO sessions VALUES (2, 'x')")


def test_explain_mode_allows_a_longer_answer_and_speeds_only_when_asked():
    from agent import asks_to_explain, asks_about_speed, check_answer
    assert asks_to_explain("What's the plan for the rest of the race?")
    assert asks_to_explain("why am I slow at Arnage") and not asks_to_explain("what's the gap")
    long = " ".join(["word"] * 80)
    assert not check_answer(long, [])[0]
    assert check_answer(long, [], max_words=110)[0]
    assert not check_answer("You did 120 km/h there, mate.", [120])[0]
    assert asks_about_speed("what speed do I carry through Arnage")
    assert check_answer("You did 120 km/h there, mate.", [120], speeds_ok=True)[0]



def test_an_answer_about_something_else_may_not_tack_on_the_car_behind():
    from agent import tacked_on
    answer = "ABS 9, mate. The car behind is 0.7 a lap quicker, focus on that."
    assert tacked_on("What ABS setting do I have?", answer, in_fight=False)
    assert not tacked_on("What ABS setting do I have?", answer, in_fight=True)       # a fight: it matters
    assert not tacked_on("Is the car behind closing?", answer, in_fight=False)       # he asked
    assert not tacked_on("What ABS setting do I have?", "ABS 9, mate.", in_fight=False)


def test_the_car_behind_gets_the_team_call_for_when_it_arrives():
    snapshot = snapshot_at_lap_4()
    behind = snapshot.picture["behind"]
    if "team_call" not in behind:
        assert behind["team_call_when_it_reaches_you"].split(":")[0] in ("DEFEND", "LET BY")


def test_pace_is_never_his_start_lap_or_best_lap():
    # live 25 Sep: lap 2 invalid (-1), best = lap 1 with the start (4:17.9) -> "17 s a lap quicker"
    from dataclasses import replace
    from agent import my_pace, recent_lap
    snapshot_race = snapshot_at_lap_4()
    engineer = RaceEngineer()
    engineer.my_lap = 242.0                              # Apex's own clock for that lap
    me = replace(snapshot_race.race.me, laps=2, last_lap=-1.0, best_lap=257.9)
    assert my_pace(me, engineer) == 242.0
    assert my_pace(replace(me, laps=1), engineer) is None          # lap 1 is never pace
    assert recent_lap(replace(behind_car(0.0), laps=1, last_lap=280.0)) == (None, None)



def test_pace_is_measured_on_the_road_a_lap_apart():
    from gaps import TrackClock
    clock = TrackClock()
    clock.lap_length = 1000.0
    # I do 50 m/s; the car behind does 55 m/s, starting 150 m back
    for step in range(0, 700):
        t = step * 0.1
        clock.mine.add(1000.0 + 50.0 * t, t)
        clock.theirs.setdefault(9, __import__("gaps").Trail()).add(850.0 + 55.0 * t, t)
    quicker = clock.pace_vs_me(9)
    assert quicker is not None and 1.5 < quicker < 2.0      # ~1.8 s a lap quicker over a 1000 m lap


def test_let_by_only_for_a_car_genuinely_fast_and_measured():
    from agent import team_call
    assert team_call("behind", 0.4, 240.0, 241.5, 5).startswith("DEFEND")          # 1.5 s: fight it
    assert team_call("behind", 0.4, 238.0, 240.5, 5).startswith("LET BY")          # 2.5 s, measured
    assert team_call("behind", 0.4, 238.0, 240.5, 5, measured=False).startswith("DEFEND")


def test_the_coach_cannot_contradict_a_sure_road_trend():
    from agent import trend_honest
    picture = {"field_around_you": [
        {"place": 6, "side": "behind", "gap_s": 0.4, "trend": "the gap is growing 0.6 s a lap (sure, 2 laps)"},
        {"place": 7, "side": "behind", "gap_s": 2.0, "trend": "the car behind is catching 0.8 s a lap (sure, 2 laps)"}]}
    assert not trend_honest("Defend, mate, it's catching you.", picture)[0]      # P6 is dropping back
    assert trend_honest("It's dropping back, mate. Clean laps.", picture)[0]
    unsure = {"field_around_you": [{"place": 6, "side": "behind", "gap_s": 0.4,
                                    "trend": "the car behind is catching 0.3 s a lap (1 lap only, NOT sure)"}]}
    assert trend_honest("Early to tell, mate.", unsure)[0]


def test_never_let_a_slower_car_by_and_one_contact_is_not_enough():
    # live 25 Sep: "that Mercedes hit you once and it's 1.9 s a lap slower, let it go"
    snapshot = snapshot_at_lap_4()
    snapshot.team_calls = {"behind": "DEFEND: similar pace"}
    snapshot.quicker_by["behind"] = -1.9
    snapshot.contacts_in_fight = 1
    ok, reason = snapshot.check_call("LET BY", "contact")
    assert not ok and "SLOWER" in reason
    snapshot.quicker_by["behind"] = 0.2
    assert not snapshot.check_call("LET BY", "contact")[0]              # one contact, not quicker
    snapshot.contacts_in_fight = 2
    assert snapshot.check_call("LET BY", "contact")[0]                  # hit twice: fair


def test_he_and_she_become_it_for_free():
    # 25 Sep: two answers refused for "he" cost two extra model rounds
    from agent import neutral_pronouns
    assert neutral_pronouns("He's diving, let him go, his exit is better.") == "It's diving, let it go, its exit is better."
