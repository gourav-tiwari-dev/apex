"""Understeer and oversteer, measured from his own laps (24 Sep 2026).

The idea is standard vehicle dynamics: a car turned by the steering should rotate (yaw) in
step with it. The bicycle model's target yaw rate is  speed x steer angle / wheelbase, scaled
down with speed by the stability factor. When the car rotates LESS than the steering asks,
the front is washing out: understeer. When it rotates MORE, or he is countersteering, the
rear is going: oversteer.

What makes it his: on a controller LMU filters the stick and cuts the lock with speed, so the
stick is not the wheel angle. So "normal" is measured, not assumed: the median of
    rotation per unit steering = |yaw rate| / (speed x |steering|)
over his own cornering, per 20 km/h speed band AND per phase of the corner. Measured on the
23 Sep race: that normal halves from 50 to 250 km/h (0.106 -> 0.050), and middles read low
and exits high at every corner (the front saturates at the limit; yaw lags the wheel as it
unwinds), which is why each phase gets its own normal.

balance 1.00 = his normal for that speed and phase, 0.70 = rotating 30 % less (understeer),
1.40 = 40 % more (oversteer). Countersteer = steering and rotation in opposite directions.

GUESSED thresholds, to be confirmed against his feel: understeer below 0.75, oversteer above
1.30 or countersteer on 2 % of the cornering samples, on the median of at least 3 laps.
"""
import statistics

BAND_KMH = 20
TURNING_MIN_KMH = 40.0
TURNING_MIN_STEER = 0.03       # below this the stick is centred: no balance to read
TURNING_MIN_LAT = 2.0          # m/s2: actually cornering
PHASE_SPLIT_KMH = 5.0          # within this of the slowest point is the middle
UNDERSTEER_BELOW = 0.75
OVERSTEER_ABOVE = 1.30
COUNTERSTEER_SHARE = 0.02
LAPS_TO_JUDGE = 3
MIN_SAMPLES = 5
PHASES = ("entry", "mid", "exit")

# the standard driver fix, per phase (the words a coach would use)
FIX = {
    ("entry", "understeer"): "Understeer on entry. Brake a touch earlier and straighter, trail it in, less steering.",
    ("mid", "understeer"): "Understeer in the middle. Less steering, be patient, let it turn before the throttle.",
    ("exit", "understeer"): "Understeer on exit. Wait on the throttle, squeeze it on as you unwind.",
    ("entry", "oversteer"): "Rear's loose on entry. Come off the brake smoother, less trail.",
    ("mid", "oversteer"): "Rear's loose in the middle. Smooth hands, no sudden lift.",
    ("exit", "oversteer"): "Oversteer on exit. Straighten the wheel first, then the throttle, smoother.",
}


def turning(frame):
    # yaw exactly 0 means no yaw was recorded (LMU's own telemetry files): it would read as
    # the car never rotating, which is not understeer, it is no data
    return (frame.speed_kmh > TURNING_MIN_KMH and abs(frame.steering_filtered) > TURNING_MIN_STEER
            and abs(frame.accel_lat) > TURNING_MIN_LAT and frame.yaw_rate != 0.0)


def rotation_per_steer(frame):
    return abs(frame.yaw_rate) / (frame.speed_kmh / 3.6 * abs(frame.steering_filtered))


def band_of(speed_kmh):
    return int(speed_kmh // BAND_KMH)


class BalanceMeter:
    """Fed every frame. Keeps his normal per speed band and phase, and every corner pass's
    balance per phase. Needs steering and yaw: old tapes and LMU's own files have no yaw, so
    it stays silent on them."""

    def __init__(self):
        self.samples = {}      # (band, phase) -> rotation per steer, every lap so far
        self.passes = {}       # corner -> list of {"entry": [..], "mid": [..], "exit": [..], "countersteer": share}
        self.corner = None
        self.frames = []
        self.normals = None    # (band, phase) -> median, rebuilt after each corner pass

    def update(self, frame, corner):
        if frame.steering_filtered is None or frame.yaw_rate is None:
            return None
        finished = None
        if corner != self.corner:
            if self.corner is not None:
                finished = self.finish(self.corner, self.frames)
            self.corner = corner
            self.frames = []
        if corner is not None:
            self.frames.append(frame)
        return finished

    def finish(self, corner, frames):
        if len(frames) < 20:
            return None
        slowest = 0
        for index, frame in enumerate(frames):
            if frame.speed_kmh < frames[slowest].speed_kmh:
                slowest = index
        low = frames[slowest].speed_kmh + PHASE_SPLIT_KMH
        raw = {phase: [] for phase in PHASES}
        cornering = 0
        against = 0
        for index, frame in enumerate(frames):
            if not turning(frame):
                continue
            if abs(frame.yaw_rate) > 0.1:
                cornering += 1
                if (frame.steering_filtered > 0) != (frame.yaw_rate > 0):
                    against += 1
                    continue              # countersteer is counted, not averaged in
            if frame.speed_kmh <= low:
                phase = "mid"
            elif index < slowest:
                phase = "entry"
            else:
                phase = "exit"
            value = rotation_per_steer(frame)
            raw[phase].append((band_of(frame.speed_kmh), value))
            self.samples.setdefault((band_of(frame.speed_kmh), phase), []).append(value)
        self.normals = None
        one_pass = {"raw": raw, "countersteer": against / cornering if cornering else 0.0}
        self.passes.setdefault(corner, []).append(one_pass)
        return corner

    def normal(self, band, phase):
        # medians of thousands of samples: worked out once per corner pass, not per sample
        if self.normals is None:
            self.normals = {}
            for key, values in self.samples.items():
                if len(values) >= 50:
                    self.normals[key] = statistics.median(values)
        return self.normals.get((band, phase))

    def pass_balance(self, one_pass, phase):
        """This pass's balance in one phase against his normal, or None."""
        ratios = []
        for band, value in one_pass["raw"][phase]:
            normal = self.normal(band, phase)
            if normal:
                ratios.append(value / normal)
        if len(ratios) < MIN_SAMPLES:
            return None
        return statistics.median(ratios)

    def corner_balance(self, corner):
        """{"entry": 0.55, "mid": 0.9, "exit": 1.6, "countersteer": 0.01, "laps": 5} over his laps
        there, or None before LAPS_TO_JUDGE laps."""
        passes = self.passes.get(corner, [])
        if len(passes) < LAPS_TO_JUDGE:
            return None
        result = {"laps": len(passes),
                  "countersteer": round(statistics.median(p["countersteer"] for p in passes), 3)}
        for phase in PHASES:
            values = [b for b in (self.pass_balance(p, phase) for p in passes) if b is not None]
            result[phase] = round(statistics.median(values), 2) if len(values) >= LAPS_TO_JUDGE else None
        return result

    def problems(self, corner):
        """[(phase, "understeer" | "oversteer", how far off)] for this corner, worst first."""
        balance = self.corner_balance(corner)
        if balance is None:
            return []
        found = []
        for phase in PHASES:
            value = balance[phase]
            if value is None:
                continue
            if value < UNDERSTEER_BELOW:
                found.append((phase, "understeer", round(1 - value, 2)))
            elif value > OVERSTEER_ABOVE:
                found.append((phase, "oversteer", round(value - 1, 2)))
        if balance["countersteer"] >= COUNTERSTEER_SHARE and not any(k == "oversteer" for _, k, _ in found):
            found.append(("exit" if balance["exit"] and balance["exit"] > 1 else "mid", "oversteer", 0.3))
        found.sort(key=lambda item: item[2], reverse=True)
        return found


def describe(balance):
    """Words for the agent: never a bare number to misread."""
    words = {"laps": balance["laps"]}
    for phase in PHASES:
        value = balance[phase]
        if value is None:
            words[phase] = "not enough data"
        elif value < UNDERSTEER_BELOW:
            words[phase] = f"understeer: rotating {round((1 - value) * 100)}% less than your normal {phase}"
        elif value > OVERSTEER_ABOVE:
            words[phase] = f"oversteer: rotating {round((value - 1) * 100)}% more than your normal {phase}"
        else:
            words[phase] = "normal"
    if balance["countersteer"] >= COUNTERSTEER_SHARE:
        words["countersteer"] = f"countersteering on {round(balance['countersteer'] * 100)}% of the corner: the rear is stepping out"
    return words
