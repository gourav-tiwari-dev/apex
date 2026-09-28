"""Understeer / oversteer from his own laps: rotation per unit steering against his normal
for that speed and phase of the corner."""

from types import SimpleNamespace

from driving.balance import BalanceMeter, describe

NORMAL_GAIN = 0.07  # yaw / (speed x steering) on an ordinary corner


def frame(speed, gain, steer=0.1, lat=5.0):
    return SimpleNamespace(
        speed_kmh=speed,
        steering_filtered=steer,
        accel_lat=lat,
        yaw_rate=gain * speed / 3.6 * steer,
    )


def drive_corner(
    meter, name, entry_gain=NORMAL_GAIN, exit_gain=NORMAL_GAIN, countersteer=False
):
    speeds_in = [150 - i for i in range(0, 50)]  # braking in
    speeds_mid = [100] * 30
    speeds_out = [101 + i for i in range(0, 50)]  # back on the power
    for speed in speeds_in:
        meter.update(frame(speed, entry_gain), name)
    for speed in speeds_mid:
        meter.update(frame(speed, NORMAL_GAIN), name)
    for index, speed in enumerate(speeds_out):
        if countersteer and index % 10 == 0:
            meter.update(
                frame(speed, -NORMAL_GAIN), name
            )  # steering against the rotation
        else:
            meter.update(frame(speed, exit_gain), name)
    meter.update(
        frame(200, NORMAL_GAIN, steer=0.0, lat=0.0), None
    )  # the straight after


def lap(meter, **odd_corner):
    for name in ("A", "B", "C", "D"):
        drive_corner(meter, name)
    drive_corner(meter, "Odd", **odd_corner)


def test_ordinary_corners_read_normal():
    meter = BalanceMeter()
    for _ in range(3):
        lap(meter)
    balance = meter.corner_balance("A")
    assert balance["entry"] == 1.0 and balance["mid"] == 1.0 and balance["exit"] == 1.0
    assert meter.problems("A") == []


def test_a_front_that_will_not_turn_in_is_understeer_on_entry():
    meter = BalanceMeter()
    for _ in range(3):
        lap(meter, entry_gain=NORMAL_GAIN * 0.5)
    phase, kind, how_far = meter.problems("Odd")[0]
    assert (phase, kind) == ("entry", "understeer")
    assert describe(meter.corner_balance("Odd"))["entry"].startswith(
        "understeer: rotating"
    )


def test_a_rear_that_steps_out_on_the_power_is_oversteer_on_exit():
    meter = BalanceMeter()
    for _ in range(3):
        lap(meter, exit_gain=NORMAL_GAIN * 1.8)
    assert meter.problems("Odd")[0][:2] == ("exit", "oversteer")


def test_countersteer_is_oversteer_even_when_the_average_looks_fine():
    meter = BalanceMeter()
    for _ in range(3):
        lap(meter, countersteer=True)
    assert meter.problems("Odd")[0][1] == "oversteer"
    assert "countersteering" in describe(meter.corner_balance("Odd"))["countersteer"]


def test_nothing_is_judged_before_three_laps_or_without_yaw():
    meter = BalanceMeter()
    for _ in range(2):
        lap(meter, entry_gain=NORMAL_GAIN * 0.5)
    assert meter.corner_balance("Odd") is None
    no_yaw = BalanceMeter()  # LMU's own files carry yaw 0
    for _ in range(3):
        for name in ("A", "B", "C", "D", "Odd"):
            for speed in range(150, 100, -1):
                no_yaw.update(frame(speed, 0.0), name)
            no_yaw.update(frame(200, 0.0, steer=0.0, lat=0.0), None)
    assert no_yaw.problems("Odd") == []
