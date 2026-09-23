from types import SimpleNamespace

from persona import gate, Persona
from radio import Call, PERFORMANCE


def call(kind="OFF_TRACK", facts=None):
    return Call(seat="performance", kind=kind, sim_time=0.0, priority=PERFORMANCE, ttl=6.0,
                conclusion="ran wide", facts=facts or {})


def test_the_two_bugs_from_the_july_persona_test_are_caught_in_code():
    # 1. the label printed as a spoken prefix
    assert gate("FLAG_ONCE: cold fronts, brake 8m earlier", call()) [0] is False
    # 2. a line longer than the limit
    assert gate("one two three four five six seven eight nine ten eleven twelve thirteen", call())[0] is False


def test_banned_words_are_refused():
    assert gate("Maybe brake later here.", call()) == (False, "banned word: maybe")
    assert gate("You should hold the line.", call())[0] is False


def test_back_off_is_only_allowed_for_physics_abort():
    assert gate("Brakes are gone. Back off.", call())[0] is False
    assert gate("Brakes are gone. Back off.", call(kind="PHYSICS_ABORT"))[0] is True


def test_numbers_must_come_from_the_facts():
    facts = {"corner": "T11 Parabolica", "speed_kmh": 170, "gap_s": 0.3}
    assert gate("Wide at T11 Parabolica, 170. Fix it.", call(facts=facts)) == (True, "ok")
    assert gate("3 tenths back. Hold it.", call(facts=facts)) == (True, "ok")
    assert gate("Wide at 185. Fix it.", call(facts=facts))[0] is False


def test_a_fact_of_5_7_does_not_let_the_model_say_57():
    assert gate("57 down. Push.", call(facts={"delta_kmh": 5.7}))[0] is False


def test_clean_mode_refuses_swearing():
    assert gate("Fucking send it.", call(), clean=False) == (True, "ok")
    assert gate("Fucking send it.", call(), clean=True)[0] is False


def test_no_questions_and_no_acknowledgements():
    assert gate("Where are you going?", call())[0] is False
    assert gate("Copy. Push now.", call())[0] is False


class FakeClient:
    def __init__(self, reply=None, finish="stop", fail=False):
        self.reply = reply
        self.finish = finish
        self.fail = fail
        self.sent = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.sent.append(kwargs)
        if self.fail:
            raise TimeoutError("slow network")
        choice = SimpleNamespace(message=SimpleNamespace(content=self.reply), finish_reason=self.finish)
        usage = SimpleNamespace(prompt_tokens=180, completion_tokens=12)
        return SimpleNamespace(choices=[choice], usage=usage)


def test_the_model_is_asked_with_thinking_off():
    client = FakeClient(reply="Wide. Tidy it.")
    line, tokens_in, tokens_out, _ = Persona(client=client).phrase(call())
    assert line == "Wide. Tidy it."
    assert client.sent[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert (tokens_in, tokens_out) == (180, 12)


def test_a_line_cut_off_by_the_token_limit_is_never_used():
    line, _, _, _ = Persona(client=FakeClient(reply="Wide at", finish="length")).phrase(call())
    assert line is None


def test_three_failures_take_the_model_offline():
    persona = Persona(client=FakeClient(fail=True))
    for _ in range(3):
        persona.phrase(call())
    assert persona.online() is False


def test_spelled_out_numbers_are_checked_too():
    facts = {"speed_kmh": 61, "gain_s": 0.2}
    assert gate("Sixty-one at Rettifilo. Commit.", call(facts=facts)) == (True, "ok")
    assert gate("Sixty-five at Rettifilo. Commit.", call(facts=facts))[0] is False
    assert gate("Two tenths. Bank it.", call(facts=facts)) == (True, "ok")


def test_the_model_is_told_the_message_not_asked_to_invent_one():
    from persona import facts_text
    text = facts_text(call(facts={"gap_s": 0.3}))
    assert text.startswith("tell him: ")
    assert "gap_s: 0.3" in text


def test_the_radio_never_guesses_a_rivals_gender():
    assert gate("Take her into Parabolica.", call())[0] is False
    assert gate("His tyres are gone. Go.", call())[0] is False
    assert gate("Take Ann into Parabolica.", call(facts={"driver": "Ann"})) == (True, "ok")
