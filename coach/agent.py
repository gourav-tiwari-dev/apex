"""The race engineer who can answer anything (M9 part 2, 24 Sep 2026).

The fixed push-to-talk list (answers.py) covers the everyday questions in ~50 ms. Everything
else - "the car behind is diving at me, he's 2 seconds faster, do I defend or let him go?" -
comes here: a model with tools that look at the live race, decides, and answers in the
engineer's voice. Gourav: "in real racing there will be countless possible questions and
only an agent can answer them. I can tolerate some latency."

Measured on aicredits (24 Sep), three real questions at lap 4 of his 23 Sep race:
  thinking ON  6.0-9.5 s, Rs 0.29-0.46 a question
  thinking OFF 2.1-4.5 s, Rs 0.12-0.35 a question, the same calls on all three
so thinking is off. Most of the cost is INPUT: every tool round sends the whole conversation
again, so the race picture goes with the question (one round fewer) and empty fields are
dropped from what the tools return.

How it stays honest:
  - The tools read a SNAPSHOT taken the moment he asked (built on the main thread), so the
    agent never reads the seats while the race loop is changing them.
  - Every number in the answer must come from a tool result or from his own question, and the
    answer passes its own gate (no speeds, no he/she for other drivers, no hedging). A refused
    answer gets one rewrite with the reason; a second refusal is replaced by an honest
    "no clean answer" line.

This module is RaceAgent: the thread that asks the model and hands the answers back to the
race loop. The rest of the coach: coach/prompt.py (what it is told, its tool list),
coach/snapshot.py (the race held still, and the tools), coach/fight_maths.py (the team call),
coach/answer_checks.py (the gate), coach/race_tools.py (what the tools return) and
coach/llm.py (the model).
"""

import json
import threading
import time
from dataclasses import dataclass
from queue import Queue, Empty

from radio.calls import Call, RACE_CONTROL
from talk.orders import current_plan
from coach.llm import LIVE_MODEL, open_client
from coach.prompt import (
    AGENT_PROMPT,
    CLEAN_RULE,
    NO_TACK_ON,
    TOOLS,
    VOICE_REMINDER,
    VOICE_REMINDER_CLEAN,
    for_driver,
)
from driver_profile import load_profile
from coach.answer_checks import (
    MAX_WORDS,
    asks_about_speed,
    asks_to_explain,
    check_answer,
    fallback,
    fuel_honest,
    neutral_pronouns,
    numbers_seen,
    split_call,
    split_orders,
    tacked_on,
    trend_honest,
)

MAX_ROUNDS = 4  # tool rounds before it must answer
ANSWER_TTL_S = 30.0  # an answer about the race 30 s ago is out of date
MAX_TOKENS = 1500
# 24 Sep: the provider went erratic - three identical calls took 60 s (timed out), 26 s, 1.3 s -
# and an answer came 38 s after the question. On track that is worse than no answer, so each
# model call gets 10 s, and past that the team's own call is said at once (see fallback()).
MODEL_TIMEOUT_S = 10
# a question within a minute of the last one may be a follow-up
FOLLOW_UP_WINDOW_S = 60.0
# v3 5b: "and what about the one behind?" can lean on 3 exchanges
FOLLOW_UP_EXCHANGES = 3

MAX_WORDS_EXPLAIN = 110
HEAVY_TIMEOUT_S = 20


def without_empty(value):
    """Tool results without None, empty lists and empty dicts: fewer tokens sent every round."""
    if isinstance(value, dict):
        kept = {}
        for key, item in value.items():
            item = without_empty(item)
            if item not in (None, [], {}):
                kept[key] = item
        return kept
    if isinstance(value, list):
        return [without_empty(item) for item in value]
    return value


@dataclass
class Asked:
    """What goes with one question to the coach, and the limits its answer is checked
    against."""

    system: str  # the system prompt (with the clean rule in clean mode)
    picture: str  # the race picture, as JSON
    given: str  # the cars either side, his habits, his standing orders, as JSON
    voice: str  # the voice reminder sent right next to the question
    max_words: int
    timeout: float  # seconds the model gets for each turn
    speeds_ok: bool  # he asked about speed: km/h may be said


# ---- the agent -------------------------------------------------------------------------------


class RaceAgent:
    """Runs on its own thread. ask() returns at once; finished() hands back the answer Calls
    and the cost of every model call, for the race loop to put on air and log."""

    def __init__(self, budget, clean=False, client=None, profile=None):
        self.budget = budget
        self.clean = clean
        # who it works for (product, 30 Sep): the prompt used to say Gourav for everyone
        self.profile = profile if profile is not None else load_profile()
        self.exchanges = []  # the last questions and answers, for follow-ups
        self.orders = (
            None  # his standing orders (orders.StandingOrders), set by the race loop
        )
        # live 24 Sep: the first question timed out on a cold connection (TLS, DNS) while the
        # provider was fine a minute later. One tiny call at startup opens it before he asks.
        if client is None:
            threading.Thread(target=self.warm_up, daemon=True).start()
        self.client = client
        self.jobs = Queue()
        self.results = Queue()
        threading.Thread(target=self.work, daemon=True).start()

    def connect(self):
        if self.client is None:
            self.client = open_client(timeout=MODEL_TIMEOUT_S)
        return self.client

    def warm_up(self):
        try:
            self.connect().chat.completions.create(
                model=LIVE_MODEL,
                messages=[{"role": "user", "content": "ok"}],
                max_tokens=1,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception:
            pass  # a failed warm-up changes nothing: the first question retries

    def ask(self, question, snapshot, sim_time):
        self.jobs.put((question, snapshot, sim_time, time.perf_counter()))

    def earlier(self, sim_time):
        """The recent exchanges, oldest first: "but he keeps hitting me" only makes sense after
        the question before it (24 Sep). Each must be within a minute of the next one."""
        recent = []
        after = sim_time
        for exchange in reversed(self.exchanges[-FOLLOW_UP_EXCHANGES:]):
            if after - exchange["sim_time"] > FOLLOW_UP_WINDOW_S:
                break
            recent.insert(0, exchange)
            after = exchange["sim_time"]
        if not recent:
            return ""
        lines = [
            f'he asked "{e["question"]}" and you answered "{e["answer"]}"'
            for e in recent
        ]
        return "Earlier on the radio, oldest first: " + "; then ".join(lines) + "."

    def finished(self):
        done = []
        while True:
            try:
                done.append(self.results.get_nowait())
            except Empty:
                return done

    def work(self):
        while True:
            question, snapshot, sim_time, asked_at = self.jobs.get()
            try:
                answer, info = self.think(question, snapshot, self.earlier(sim_time))
            except Exception as error:
                answer, info = (
                    "Lost the data on that one. Ask me again.",
                    {"error": error.__class__.__name__, "costs": []},
                )
            info["seconds"] = round(time.perf_counter() - asked_at, 2)
            self.exchanges = (
                self.exchanges
                + [{"question": question, "answer": answer, "sim_time": sim_time}]
            )[-FOLLOW_UP_EXCHANGES:]
            call = Call(
                seat="race_engineer",
                kind="ANSWER_AGENT",
                sim_time=sim_time,
                priority=RACE_CONTROL,
                ttl=ANSWER_TTL_S,
                conclusion=answer,
                template=answer,
                facts={
                    "heard": question,
                    "tools": info.get("tools", []),
                    "rounds": info.get("rounds"),
                    "seconds": info["seconds"],
                    "refused": info.get("refused"),
                    "call": info.get("call"),
                    "override": info.get("override"),
                },
                asked=True,
            )
            self.results.put(
                {
                    "call": call,
                    "costs": info.get("costs", []),
                    "actions": list(snapshot.actions),
                    "orders": info.get("orders", []),
                }
            )

    def model_turn(self, messages, timeout=MODEL_TIMEOUT_S):
        started = time.perf_counter()
        response = self.connect().chat.completions.create(
            model=LIVE_MODEL,
            messages=messages,
            max_tokens=MAX_TOKENS,
            timeout=timeout,
            tools=[{"type": "function", "function": tool} for tool in TOOLS],
            # thinking off: 2-3x quicker and cheaper, the same calls (module docstring)
            extra_body={"thinking": {"type": "disabled"}},
        )
        usage = response.usage
        cost = self.budget.charge(usage.prompt_tokens, usage.completion_tokens)
        spent = {
            "tokens_in": usage.prompt_tokens,
            "tokens_out": usage.completion_tokens,
            "seconds": round(time.perf_counter() - started, 3),
            "cost_rs": round(cost, 5),
        }
        return response.choices[0].message, spent

    def think(self, question, snapshot, earlier=""):
        """(answer, info): ask the model, run the tools it calls, check the answer; one rewrite
        if refused, then an honest "no clean answer". The model down or slow: the team's own
        call, from code (fallback)."""
        # no budget check: push-to-talk never stops (his call, 25 Sep - the Rs 5 cap silenced the
        # coach after 7 answers in a live race). Every call is still charged and logged.
        asked = self.asked_with(question, snapshot)
        messages = [
            {"role": "system", "content": asked.system},
            {
                "role": "user",
                "content": f"{earlier}\n\n{question}\n\n(Race picture right now, from race_picture: {asked.picture})"
                f"\n(Already given, no need to call driver or my_habits for these: {asked.given})\n\n{asked.voice}",
            },
        ]
        costs = []
        tools_used = ["race_picture"]
        tool_texts = [asked.picture, asked.given]
        refused = None
        # tool rounds, then the answer, then at most one rewrite
        for round_number in range(1, MAX_ROUNDS + 3):
            try:
                message, spent = self.model_turn(messages, asked.timeout)
            except Exception as error:
                # slow or down: the decision still gets through, from code
                return fallback(snapshot, question), {
                    "costs": costs,
                    "tools": tools_used,
                    "rounds": None,
                    "refused": f"model {error.__class__.__name__}",
                    "call": "TEAM",
                }
            costs.append(spent)
            if message.tool_calls and round_number <= MAX_ROUNDS:
                messages.append(message.model_dump(exclude_none=True))
                self.run_tools(message, snapshot, messages, tools_used, tool_texts)
                continue
            raw = message.content or ""
            given_orders, raw = split_orders(raw)
            call, override, text = split_call(raw)  # the CALL line is never spoken
            # a free fix instead of a paid rewrite round (25 Sep)
            text = neutral_pronouns(text)
            ok, reason = self.check_spoken(
                question, snapshot, (call, override, text), tool_texts, asked
            )
            if ok:
                return text, {
                    "costs": costs,
                    "tools": tools_used,
                    "rounds": round_number,
                    "refused": refused,
                    "call": call,
                    "override": override,
                    "orders": given_orders,
                }
            if refused is not None:
                break  # one rewrite only
            refused = reason
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": f"That answer was refused: {reason}. Rewrite it, same call, following the spoken-answer rules. {asked.voice}",
                }
            )
        return (
            "No clean answer on that one, mate. Ask it another way.",
            {"costs": costs, "tools": tools_used, "rounds": None, "refused": refused},
        )

    def asked_with(self, question, snapshot):
        """What goes with this question: the system prompt, the race picture, the cars either
        side and his habits, and the voice reminder (longer for "why", speeds only if asked)."""
        system = for_driver(AGENT_PROMPT, self.profile) + (
            "\n" + CLEAN_RULE if self.clean else ""
        )
        if snapshot.picture.get("session") == "race":
            snapshot.picture["plan_now"] = current_plan(
                self.orders,
                snapshot.car_state.get("fuel_at_the_flag"),
                bool(snapshot.car_state.get("damage")),
                snapshot.picture.get("laps_to_go"),
            )
        picture = json.dumps(without_empty(snapshot.picture))
        # the voice goes right next to the question: in the system prompt alone it got lost
        # (1 answer in 4 swore on 24 Sep), the same lesson as the persona's per-line flag
        voice = (
            VOICE_REMINDER_CLEAN
            if self.clean
            else for_driver(VOICE_REMINDER, self.profile)
        )
        explain = asks_to_explain(question)
        max_words = MAX_WORDS_EXPLAIN if explain else MAX_WORDS
        timeout = HEAVY_TIMEOUT_S if explain else MODEL_TIMEOUT_S
        speeds_ok = asks_about_speed(question)
        if explain:
            voice = voice.replace(
                "About 35 words.",
                "He asked for the reasons: up to about 70 words, the reasons in order.",
            )
        if speeds_ok:
            voice += " He asked about speed: speeds in km/h are allowed in this answer."
        if not snapshot.team_calls:
            voice += " " + NO_TACK_ON
        # live 25 Sep: 25 model calls for 7 answers - nearly every answer first asked for the cars
        # ahead/behind and his habits, a whole extra round (2-8 s, ~Rs 0.25). They go with the
        # question now; the tools stay for everything else.
        near = {
            side: without_empty(snapshot.drivers[side])
            for side in ("ahead", "behind")
            if side in snapshot.drivers
        }
        habits = (snapshot.habits or [])[:3]
        snapshot.orders = self.orders
        standing = self.orders.for_coach() if self.orders is not None else {}
        given = json.dumps(
            {
                "driver_ahead": near.get("ahead"),
                "driver_behind": near.get("behind"),
                "his_habits": habits,
                **standing,
            }
        )
        return Asked(system, picture, given, voice, max_words, timeout, speeds_ok)

    def run_tools(self, message, snapshot, messages, tools_used, tool_texts):
        """Every tool the model called this round, run on the snapshot; the results go back
        into the conversation (and every number in them may be said)."""
        for tool_call in message.tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            result = json.dumps(
                without_empty(snapshot.run_tool(tool_call.function.name, arguments))
            )
            tools_used.append(tool_call.function.name)
            tool_texts.append(result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )

    def check_spoken(self, question, snapshot, answer, tool_texts, asked):
        """(ok, reason): the checks an answer passes before it goes on air, in order - the team
        call, the words and numbers, fuel, the trend, tacked on, his standing orders."""
        call, override, text = answer
        ok, reason = snapshot.check_call(call, override, question)
        if ok:
            ok, reason = check_answer(
                text,
                numbers_seen(question, *tool_texts),
                self.clean,
                snapshot.driver_names(),
                asked.max_words,
                asked.speeds_ok,
            )
        if ok:
            ok, reason = fuel_honest(
                question, text, snapshot.car_state.get("fuel_at_the_flag")
            )
        if ok:
            ok, reason = trend_honest(text, snapshot.picture)
        if ok and tacked_on(question, text, bool(snapshot.team_calls)):
            ok, reason = (
                False,
                (
                    "it talks about the car ahead or behind, but he did not ask about them "
                    "and nobody is within a second: answer only his question"
                ),
            )
        if ok:
            ok, reason = snapshot.check_orders(call, override)
        return ok, reason
