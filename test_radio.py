from radio import Call, Governor, Budget, SPOTTER, RACECRAFT, PERFORMANCE
from voice import RadioDesk


def call(seat="performance", kind="OFF_TRACK", at=10.0, priority=PERFORMANCE, ttl=6.0,
         urgent=False, template=None, facts=None, phrase=True):
    # phrase=True: the model path, still there for a line that needs judgment. v3 default is False
    return Call(seat=seat, kind=kind, sim_time=at, priority=priority, ttl=ttl,
                conclusion="ran wide at T11 Parabolica", facts=facts or {},
                urgent=urgent, template=template, phrase=phrase)


def test_nothing_is_said_mid_corner_but_waits_for_the_straight():
    governor = Governor()
    governor.offer(call(at=10.0))
    assert governor.step(10.0, in_corner=True) is None
    assert governor.step(11.0, in_corner=False).kind == "OFF_TRACK"


def test_urgent_calls_go_out_even_mid_corner_and_over_another_line():
    governor = Governor()
    governor.offer(call(at=10.0))
    assert governor.step(10.0, in_corner=False) is not None       # radio now busy
    governor.offer(call(seat="spotter", kind="CAR_LEFT", at=10.5, priority=SPOTTER,
                        urgent=True, template="Car left."))
    assert governor.step(10.5, in_corner=True).kind == "CAR_LEFT"


def test_one_line_at_a_time():
    governor = Governor()
    governor.offer(call(seat="performance", at=10.0))
    governor.offer(call(seat="racecraft", kind="ATTACK", at=10.0, priority=RACECRAFT))
    first = governor.step(10.0, in_corner=False)
    assert first.kind == "ATTACK"                        # more important goes first
    assert governor.step(10.1, in_corner=False) is None  # the radio is still busy
    assert governor.step(20.0, in_corner=False) is None  # OFF_TRACK expired meanwhile


def test_a_call_that_waited_too_long_is_dropped_not_said_late():
    governor = Governor()
    governor.offer(call(at=10.0, ttl=6.0))
    assert governor.step(17.0, in_corner=False) is None
    assert governor.dropped[0][1] == "expired"


def test_a_seat_cannot_talk_again_inside_its_cooldown():
    governor = Governor()
    governor.offer(call(at=10.0))
    governor.step(10.0, in_corner=False)
    governor.offer(call(kind="THROTTLE_LIFT", at=14.0))
    assert governor.step(14.0, in_corner=False) is None     # performance cooldown is 5 s
    assert governor.step(15.1, in_corner=False).kind == "THROTTLE_LIFT"


def test_same_decisions_give_the_same_hash():
    hashes = []
    for _ in range(2):
        governor = Governor()
        governor.offer(call(at=1.0))
        governor.step(2.0, in_corner=False)
        hashes.append(governor.decision_hash())
    assert hashes[0] == hashes[1]


def test_budget_stops_llm_calls_at_the_cap():
    budget = Budget(cap_rs=0.001)
    assert budget.allows_llm()
    budget.charge(10_000, 0)            # Rs 0.35 at the derived price
    assert not budget.allows_llm()


class FakeVoice:
    def __init__(self):
        self.said = []

    def render(self, text, voice=None, mood="dry"):
        self.moods = getattr(self, "moods", []) + [mood]
        return text

    def play(self, audio, text):
        import time
        self.said.append(text)
        return time.perf_counter()


class FakePersona:
    def __init__(self, line, tokens=(100, 10)):
        self.line = line
        self.tokens = tokens

    def phrase(self, call):
        return self.line, self.tokens[0], self.tokens[1], 0.5


def run_desk(line, the_call, budget=None):
    voice = FakeVoice()
    desk = RadioDesk(voice, FakePersona(line), budget or Budget(), clean=False)
    desk.latest_sim_time = the_call.sim_time
    desk.submit(the_call)
    desk.stop()
    results = desk.drain()
    spoken = [r for r in results if not r.get("llm_only")]
    costs = [r for r in results if r.get("llm_only")]
    return voice, spoken, costs


def test_a_good_line_is_spoken_and_its_cost_logged():
    the_call = call(facts={"corner": "T11 Parabolica", "speed_kmh": 170})
    voice, spoken, costs = run_desk("Wide at Parabolica. 170. Tidy it.", the_call)
    assert voice.said == ["Wide at Parabolica. 170. Tidy it."]
    assert spoken[0]["status"] == "spoken"
    assert costs[0]["llm"]["tokens_out"] == 10


def test_an_invented_number_is_refused_and_code_words_used_instead():
    the_call = call(template="Ran wide at T11 Parabolica.", facts={"speed_kmh": 170})
    voice, spoken, costs = run_desk("Wide at 185. Idiot.", the_call)
    assert voice.said == ["Ran wide at T11 Parabolica."]
    assert "invented number" in spoken[0]["reason"]


def test_no_template_and_a_refused_line_means_silence():
    voice, spoken, costs = run_desk("You should maybe try braking later.", call())
    assert voice.said == []
    assert spoken[0]["status"] == "no_line"


def test_over_budget_uses_template_without_asking_the_model():
    budget = Budget(cap_rs=0.0)
    voice, spoken, costs = run_desk("never asked", call(template="Ran wide."), budget)
    assert voice.said == ["Ran wide."]
    assert spoken[0]["reason"] == "over budget"
    assert costs == []


def test_cooking_starts_when_the_call_is_raised_not_when_it_goes_on_air():
    import time
    class SlowPersona(FakePersona):
        def phrase(self, call):
            time.sleep(0.3)
            return "Wide at Parabolica. Tidy it.", 100, 10, 0.3
    voice = FakeVoice()
    desk = RadioDesk(voice, SlowPersona(""), Budget(), clean=False)
    the_call = call(facts={"corner": "T11 Parabolica"})
    desk.prepare(the_call)
    time.sleep(0.5)                  # waiting for a straight while the line cooks
    desk.latest_sim_time = the_call.sim_time
    desk.submit(the_call)
    desk.stop()
    spoken = [r for r in desk.drain() if not r.get("llm_only")]
    assert spoken[0]["status"] == "spoken"
    assert spoken[0]["latency_ms"] < 100      # ready the moment it went on air


def test_the_llm_cost_is_logged_even_when_the_call_never_goes_on_air():
    import time
    voice = FakeVoice()
    desk = RadioDesk(voice, FakePersona("Tidy it."), Budget(), clean=False)
    desk.prepare(call())
    time.sleep(0.2)
    desk.stop()
    costs = [r for r in desk.drain() if r.get("llm_only")]
    assert len(costs) == 1


def test_no_coaching_after_the_chequered_flag():
    # 24 Sep replay: a braking tip went out in the same second as "Chequered flag. P13."
    governor = Governor()
    governor.offer(call(seat="performance", kind="CORNER_LOSS", at=10.0, template="Brake later."))
    governor.offer(call(seat="race_engineer", kind="FINISH", at=10.0, template="Chequered flag. P13."))
    assert governor.step(10.0, in_corner=False).kind == "FINISH"
    assert governor.step(20.0, in_corner=False) is None
    assert ("CORNER_LOSS", "chequered") in [(c.kind, r) for c, r in governor.dropped]
    governor.offer(call(seat="spotter", kind="CAR_LEFT", at=21.0, priority=SPOTTER, urgent=True, template="Car left."))
    assert governor.step(21.0, in_corner=False).kind == "CAR_LEFT"
