from radio.governor import Governor
from radio.calls import Call, SPOTTER, RACECRAFT, PERFORMANCE
from radio.desk import RadioDesk


def call(
    seat="performance",
    kind="OFF_TRACK",
    at=10.0,
    priority=PERFORMANCE,
    ttl=6.0,
    urgent=False,
    template=None,
    facts=None,
):
    return Call(
        seat=seat,
        kind=kind,
        sim_time=at,
        priority=priority,
        ttl=ttl,
        conclusion="ran wide at T11 Parabolica",
        facts=facts or {},
        urgent=urgent,
        template=template,
    )


def test_nothing_is_said_mid_corner_but_waits_for_the_straight():
    governor = Governor()
    governor.offer(call(at=10.0))
    assert governor.step(10.0, in_corner=True) is None
    assert governor.step(11.0, in_corner=False).kind == "OFF_TRACK"


def test_urgent_calls_go_out_even_mid_corner_and_over_another_line():
    governor = Governor()
    governor.offer(call(at=10.0))
    assert governor.step(10.0, in_corner=False) is not None  # radio now busy
    governor.offer(
        call(
            seat="spotter",
            kind="CAR_LEFT",
            at=10.5,
            priority=SPOTTER,
            urgent=True,
            template="Car left.",
        )
    )
    assert governor.step(10.5, in_corner=True).kind == "CAR_LEFT"


def test_one_line_at_a_time():
    governor = Governor()
    governor.offer(call(seat="performance", at=10.0))
    governor.offer(call(seat="racecraft", kind="ATTACK", at=10.0, priority=RACECRAFT))
    first = governor.step(10.0, in_corner=False)
    assert first.kind == "ATTACK"  # more important goes first
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
    assert governor.step(14.0, in_corner=False) is None  # performance cooldown is 5 s
    assert governor.step(15.1, in_corner=False).kind == "THROTTLE_LIFT"


def test_a_spotter_hazard_is_not_dropped_for_words_another_car_had():
    # replay of the 58-car race (27 Sep): the second and third "LMP2 behind..." calls, for other
    # cars, were dropped as "said_recently". The spotter's hazards are kept apart per car already
    governor = Governor()
    first = Call(
        seat="spotter",
        kind="FASTER_CLASS_BEHIND",
        sim_time=100.0,
        priority=SPOTTER,
        ttl=8.0,
        conclusion="LMP2 behind, closing.",
        template="LMP2 behind, closing.",
        immediate=True,
    )
    assert governor.offer(first) is True
    assert governor.step(100.0, in_corner=False) is first
    second = Call(
        seat="spotter",
        kind="FASTER_CLASS_BEHIND",
        sim_time=160.0,
        priority=SPOTTER,
        ttl=8.0,
        conclusion="LMP2 behind, closing.",
        template="LMP2 behind, closing.",
        immediate=True,
    )
    assert governor.offer(second) is True


def test_same_decisions_give_the_same_hash():
    hashes = []
    for _ in range(2):
        governor = Governor()
        governor.offer(call(at=1.0))
        governor.step(2.0, in_corner=False)
        hashes.append(governor.decision_hash())
    assert hashes[0] == hashes[1]


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


def run_desk(the_call):
    voice = FakeVoice()
    desk = RadioDesk(voice, clean=False)
    desk.latest_sim_time = the_call.sim_time
    desk.submit(the_call)
    desk.stop()
    return voice, desk.drain()


def test_a_fast_replay_desk_says_the_line_on_the_spot_on_sim_time():
    # 27 Sep: two identical replays of one tape disagreed before any order (a SLIDE_CAUGHT spoken
    # in one, not in the other): the desk cooked on a thread in wall time while the replay raced
    # through sim time, so a line could go "stale" or find the desk "full" by chance
    voice = FakeVoice()
    desk = RadioDesk(voice, clean=False, synchronous=True)
    first = call(facts={"corner": "Arnage"}, template="Wide at Arnage.")
    second = call(facts={"corner": "Indianapolis"}, template="Wide at Indianapolis.")
    desk.latest_sim_time = (
        first.sim_time + 1000.0
    )  # sim time long gone by the time a thread looks
    assert desk.submit(first) is True
    assert desk.submit(second) is True  # never "queue full": nothing is playing
    statuses = [r["status"] for r in desk.drain()]
    assert statuses == ["spoken", "spoken"]
    desk.stop()


def test_a_seats_call_is_said_in_the_codes_own_words():
    # since 24 Sep no model rewords a line; since 1 Oct the path that could is gone
    voice, results = run_desk(call(template="Ran wide at T11 Parabolica."))
    assert voice.said[0].startswith("Ran wide at T11 Parabolica.")
    assert results[0]["status"] == "spoken"


def test_a_call_with_no_template_says_its_conclusion():
    voice, results = run_desk(call())
    assert voice.said[0].startswith("ran wide at T11 Parabolica")


def test_cooking_starts_when_the_call_is_raised_not_when_it_goes_on_air():
    import time

    class SlowVoice(FakeVoice):
        def render(self, text, voice=None, mood="dry"):
            time.sleep(0.3)  # a live render
            return super().render(text, voice, mood)

    voice = SlowVoice()
    desk = RadioDesk(voice, clean=False)
    the_call = call(facts={"corner": "T11 Parabolica"})
    desk.prepare(the_call)
    time.sleep(0.5)  # waiting for a straight while the line cooks
    desk.latest_sim_time = the_call.sim_time
    desk.submit(the_call)
    desk.stop()
    spoken = desk.drain()
    assert spoken[0]["status"] == "spoken"
    assert spoken[0]["latency_ms"] < 100  # ready the moment it went on air


def test_no_coaching_after_the_chequered_flag():
    # 24 Sep replay: a braking tip went out in the same second as "Chequered flag. P13."
    governor = Governor()
    governor.offer(
        call(seat="performance", kind="CORNER_LOSS", at=10.0, template="Brake later.")
    )
    governor.offer(
        call(
            seat="race_engineer",
            kind="FINISH",
            at=10.0,
            template="Chequered flag. P13.",
        )
    )
    assert governor.step(10.0, in_corner=False).kind == "FINISH"
    assert governor.step(20.0, in_corner=False) is None
    assert ("CORNER_LOSS", "chequered") in [(c.kind, r) for c, r in governor.dropped]
    governor.offer(
        call(
            seat="spotter",
            kind="CAR_LEFT",
            at=21.0,
            priority=SPOTTER,
            urgent=True,
            template="Car left.",
        )
    )
    assert governor.step(21.0, in_corner=False).kind == "CAR_LEFT"
