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
    assert snapshot.picture["behind"]["their_pace"] == "1.1 s a lap quicker than you"
    assert snapshot.picture["ahead"]["their_pace"] == "0.4 s a lap slower than you"
    assert snapshot.run_tool("driver", {"who": "behind"})["driver"] == "Chabbi Zino"
    assert snapshot.run_tool("driver", {"who": "zino"})["where"] == "0.8 s behind you"
    assert "error" in snapshot.run_tool("driver", {"who": "Hamilton"})


def test_the_agent_uses_tools_then_answers_and_the_cost_is_counted():
    model = ScriptedModel(
        Message(tool_calls=[ToolCall("driver", json.dumps({"who": "behind"}))]),
        Message(content="Let Chabbi Zino go, mate. 1.1 a lap quicker, not 2. Clean room out of Tertre Rouge."))
    agent = RaceAgent(Budget(), client=model)
    result = ask(agent, "He's 2 seconds faster, defend or let him go?", snapshot_at_lap_4())
    call = result["call"]
    assert call.template == "Let Chabbi Zino go, mate. 1.1 a lap quicker, not 2. Clean room out of Tertre Rouge."
    assert call.asked and not call.phrase and call.kind == "ANSWER_AGENT"
    assert call.facts["tools"] == ["race_picture", "driver"]
    assert len(result["costs"]) == 2
    # the race picture went with the question, so the first round could skip it
    assert "Race picture right now" in model.sent[0][1]["content"]


def test_a_refused_answer_gets_one_rewrite_then_an_honest_fallback():
    model = ScriptedModel(Message(content="He is 7 seconds a lap faster, let him go."),
                          Message(content="Chabbi Zino is 1.1 a lap quicker. Let Chabbi Zino go."))
    result = ask(RaceAgent(Budget(), client=model), "defend or let the car behind go?", snapshot_at_lap_4())
    assert result["call"].template == "Chabbi Zino is 1.1 a lap quicker. Let Chabbi Zino go."
    assert "he" in result["call"].facts["refused"]
    stubborn = ScriptedModel(Message(content="He's quicker."), Message(content="He's still quicker."))
    result = ask(RaceAgent(Budget(), client=stubborn), "defend or let the car behind go?", snapshot_at_lap_4())
    assert result["call"].template == "No clean answer on that one, mate. Ask it another way."


def test_the_answer_gate():
    known = numbers_seen("he's 2 seconds faster", '{"gap_s": 0.8, "their_pace": "1.1 s a lap quicker than you"}')
    assert check_answer("Let Chabbi Zino go. 1.1 a lap quicker, not 2.", known)[0]
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
