"""What Apex knows about the whole race, not just my car.

Two kinds of record go onto the tape next to the 60 Hz car frames:

  RaceSnapshot  the session, my standing and every other car. Taken each time the game
                updates its scoring, which is about 5 times a second.
  NearCars      the cars close to me, taken every frame. The spotter needs their positions
                far more often than 5 times a second.

Opponents are matched to their telemetry by mID, never by array position: the telemetry
array is ordered differently from the scoring array (measured 19 Aug 2026).
"""
import math
from dataclasses import dataclass, field

# Cars inside this distance are recorded every frame for the spotter.
NEAR_RADIUS_M = 60.0


def text(raw):
    return raw.decode(errors="replace").strip("\x00").strip()


def signed_char(raw):
    if not raw:
        return 0
    return int.from_bytes(raw, "little", signed=True)


def speed_kmh_of(velocity):
    return math.sqrt(velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2) * 3.6


def kelvin_to_celsius(kelvin):
    return round(kelvin - 273.15, 1)


@dataclass
class Session:
    track: str
    session: int            # 0 testday, 1-4 practice, 5-8 qualifying, 9 warmup, 10-13 race
    game_phase: int         # 0 before, 3 formation, 4 lights, 5 green, 6 yellow/safety car, 7 stopped, 8 over
    time_remaining: float   # seconds left in the session
    end_time: float
    max_laps: int
    yellow_flag_state: int
    sector_flags: list
    start_light: int
    red_lights: int
    raining: float          # 0.0 - 1.0
    ambient_temp: float     # Celsius
    track_temp: float       # Celsius
    wetness: float          # average wetness on the racing line, 0.0 - 1.0
    grip_level: int         # 0 green .. 4 saturated rubber
    fixed_setup: bool
    limit_steps_per_penalty: int
    in_realtime: bool


@dataclass
class Me:
    driver: str
    car_class: str
    place: int
    grid: int
    laps: int
    time_behind_next: float
    time_behind_leader: float
    laps_behind_leader: int
    best_lap: float
    last_lap: float
    sector: int             # 0 = sector 3, 1 = sector 1, 2 = sector 2 (the game's own numbering)
    cur_sector1: float
    cur_sector2: float      # sector 1 + sector 2
    pitstops: int
    penalties: int
    in_pits: bool
    pit_state: int          # 0 none, 1 request, 2 entering, 3 stopped, 4 exiting
    finish_status: int      # 0 none, 1 finished, 2 dnf, 3 dq
    flag: int               # 0 green, 6 blue
    under_yellow: bool
    count_lap_flag: int
    fuel: float             # litres
    fuel_capacity: float
    virtual_energy: float   # fraction
    battery: float          # fraction
    lift_and_coast: int
    track_limit_steps: int
    gap_car_ahead: float
    gap_car_behind: float
    gap_place_ahead: float
    gap_place_behind: float
    tyre_temps: list        # 4 wheels x (left, centre, right), Celsius
    tyre_pressures: list    # 4 wheels, kPa
    tyre_wear: list         # 4 wheels, fraction of maximum
    brake_temps: list       # 4 wheels, Celsius
    compound: str
    dents: list             # 8 places around the car, 0 none / 1 some / 2 more
    detached: bool
    overheating: bool
    brake_bias_rear: float
    tc: int
    abs: int
    motor_map: int
    arb_front: int
    arb_rear: int
    car_model: str


@dataclass
class Opponent:
    id: int
    driver: str
    steam_id: int
    car_class: str
    car_name: str
    place: int
    laps: int
    lap_dist: float
    time_behind_next: float
    time_behind_leader: float
    laps_behind_leader: int
    best_lap: float
    last_lap: float
    in_pits: bool
    pit_state: int
    pitstops: int
    finish_status: int
    control: int            # 0 local player, 1 AI, 2 remote human
    flag: int
    x: float | None = None
    y: float | None = None
    z: float | None = None
    speed_kmh: float | None = None
    brake: float | None = None
    throttle: float | None = None
    steering: float | None = None
    gear: int | None = None
    fuel: float | None = None
    last_impact_time: float | None = None
    last_impact_magnitude: float | None = None
    car_model: str | None = None     # "BMW M4 LMGT3"; tapes before 24 Sep 2026 have none


@dataclass
class RaceSnapshot:
    sim_time: float
    session: Session
    me: Me | None
    opponents: list = field(default_factory=list)
    t: str = "race"


@dataclass
class NearCar:
    id: int
    x: float
    y: float
    z: float
    speed_kmh: float
    brake: float
    throttle: float
    steering: float


@dataclass
class NearCars:
    sim_time: float
    cars: list
    t: str = "near"


def identity(opponent):
    """Who a rival is, across races. Online, LMU gives every Steam ID as 0 (measured 23 Sep
    2026), so the driver's name stands in: names can change, but 0 would make all 19 cars
    one rival."""
    if opponent.steam_id not in (0, "0", None, ""):
        return str(opponent.steam_id)
    return "name:" + opponent.driver


def telemetry_by_id(data):
    rows = {}
    for row in data.telemetry.telemInfo:
        if row.mID != 0:
            rows[row.mID] = row
    return rows


def read_session(info):
    return Session(
        track=text(info.mTrackName),
        session=info.mSession,
        game_phase=info.mGamePhase,
        time_remaining=round(info.mSessionTimeRemaining, 2),
        end_time=round(info.mEndET, 2),
        max_laps=info.mMaxLaps,
        yellow_flag_state=signed_char(info.mYellowFlagState),
        sector_flags=list(info.mSectorFlag),
        start_light=info.mStartLight,
        red_lights=info.mNumRedLights,
        raining=round(info.mRaining, 3),
        ambient_temp=round(info.mAmbientTemp, 1),
        track_temp=round(info.mTrackTemp, 1),
        wetness=round(info.mAvgPathWetness, 3),
        grip_level=info.mTrackGripLevel,
        fixed_setup=bool(info.mIsFixedSetup),
        limit_steps_per_penalty=info.mTrackLimitsStepsPerPenalty,
        in_realtime=bool(info.mInRealtime),
    )


def read_me(scoring, car):
    tyre_temps = []
    tyre_pressures = []
    tyre_wear = []
    brake_temps = []
    for wheel in car.mWheels:
        tyre_temps.append([kelvin_to_celsius(t) for t in wheel.mTemperature])
        tyre_pressures.append(round(wheel.mPressure, 1))
        tyre_wear.append(round(wheel.mWear, 4))
        brake_temps.append(round(wheel.mBrakeTemp, 1))

    return Me(
        driver=text(scoring.mDriverName),
        car_class=text(scoring.mVehicleClass),
        place=scoring.mPlace,
        grid=scoring.mQualification,
        laps=scoring.mTotalLaps,
        time_behind_next=round(scoring.mTimeBehindNext, 3),
        time_behind_leader=round(scoring.mTimeBehindLeader, 3),
        laps_behind_leader=scoring.mLapsBehindLeader,
        best_lap=round(scoring.mBestLapTime, 3),
        last_lap=round(scoring.mLastLapTime, 3),
        sector=scoring.mSector,
        cur_sector1=round(scoring.mCurSector1, 3),
        cur_sector2=round(scoring.mCurSector2, 3),
        pitstops=scoring.mNumPitstops,
        penalties=scoring.mNumPenalties,
        in_pits=bool(scoring.mInPits),
        pit_state=scoring.mPitState,
        finish_status=scoring.mFinishStatus,
        flag=scoring.mFlag,
        under_yellow=bool(scoring.mUnderYellow),
        count_lap_flag=scoring.mCountLapFlag,
        fuel=round(car.mFuel, 3),
        fuel_capacity=round(car.mFuelCapacity, 1),
        virtual_energy=round(car.mVirtualEnergy, 4),
        battery=round(car.mBatteryChargeFraction, 4),
        lift_and_coast=car.mLiftAndCoastProgress,
        track_limit_steps=car.mTrackLimitsSteps,
        gap_car_ahead=round(car.mTimeGapCarAhead, 3),
        gap_car_behind=round(car.mTimeGapCarBehind, 3),
        gap_place_ahead=round(car.mTimeGapPlaceAhead, 3),
        gap_place_behind=round(car.mTimeGapPlaceBehind, 3),
        tyre_temps=tyre_temps,
        tyre_pressures=tyre_pressures,
        tyre_wear=tyre_wear,
        brake_temps=brake_temps,
        compound=text(car.mFrontTireCompoundName),
        dents=list(car.mDentSeverity),
        detached=bool(car.mDetached),
        overheating=bool(car.mOverheating),
        brake_bias_rear=round(car.mRearBrakeBias, 4),
        tc=car.mTC,
        abs=car.mABS,
        motor_map=car.mMotorMap,
        arb_front=car.mFrontAntiSway,
        arb_rear=car.mRearAntiSway,
        car_model=text(car.mVehicleModel),
    )


def read_opponent(scoring, car):
    opponent = Opponent(
        id=scoring.mID,
        driver=text(scoring.mDriverName),
        steam_id=scoring.mSteamID,
        car_class=text(scoring.mVehicleClass),
        car_name=text(scoring.mVehicleName),
        place=scoring.mPlace,
        laps=scoring.mTotalLaps,
        lap_dist=round(scoring.mLapDist, 2),
        time_behind_next=round(scoring.mTimeBehindNext, 3),
        time_behind_leader=round(scoring.mTimeBehindLeader, 3),
        laps_behind_leader=scoring.mLapsBehindLeader,
        best_lap=round(scoring.mBestLapTime, 3),
        last_lap=round(scoring.mLastLapTime, 3),
        in_pits=bool(scoring.mInPits),
        pit_state=scoring.mPitState,
        pitstops=scoring.mNumPitstops,
        finish_status=scoring.mFinishStatus,
        control=scoring.mControl,
        flag=scoring.mFlag,
    )
    # A car can be in scoring without a telemetry row (seen in the monitor view).
    # Its standing is still worth keeping, so the telemetry part just stays empty.
    if car is not None:
        opponent.x = round(car.mPos.x, 3)
        opponent.y = round(car.mPos.y, 3)
        opponent.z = round(car.mPos.z, 3)
        opponent.speed_kmh = round(speed_kmh_of(car.mLocalVel), 2)
        opponent.brake = round(car.mUnfilteredBrake, 4)
        opponent.throttle = round(car.mUnfilteredThrottle, 4)
        opponent.steering = round(car.mUnfilteredSteering, 4)
        opponent.gear = car.mGear
        opponent.fuel = round(car.mFuel, 3)
        opponent.last_impact_time = round(car.mLastImpactET, 3)
        opponent.last_impact_magnitude = round(car.mLastImpactMagnitude, 2)
        opponent.car_model = text(car.mVehicleModel)
    return opponent


def same_class_neighbours(race):
    """The same-class cars just ahead of and behind me in the race, and the time gaps to them.
    Only cars on my lap: a lapped car is not a fight."""
    me = race.me
    ahead = None
    behind = None
    for opponent in race.opponents:
        if opponent.car_class != me.car_class or opponent.laps_behind_leader != me.laps_behind_leader:
            continue
        if opponent.place < me.place and (ahead is None or opponent.place > ahead.place):
            ahead = opponent
        if opponent.place > me.place and (behind is None or opponent.place < behind.place):
            behind = opponent
    gap_ahead = None
    gap_behind = None
    if ahead is not None:
        gap_ahead = round(me.time_behind_leader - ahead.time_behind_leader, 2)
    if behind is not None:
        gap_behind = round(behind.time_behind_leader - me.time_behind_leader, 2)
    return ahead, gap_ahead, behind, gap_behind


def laps_to_go(race, lap_time):
    """Laps still to drive, counted at the line. A lap race: max laps minus laps done.
    A timed race: the flag drops when the LEADER first crosses the line after the clock runs
    out, and I finish when I next cross after that, so the count is
    ceil((time left + my gap to the leader) / lap time).
    (23 Sep: the old count added one more lap on top, told him fuel was tight with 1.4 laps
    of energy spare, and he was told to lift and coast for nothing.)"""
    me = race.me
    session = race.session
    if 0 < session.max_laps < 1000:
        return max(0, session.max_laps - me.laps)
    if lap_time is None or lap_time <= 0:
        return None
    return math.ceil((session.time_remaining + max(0.0, me.time_behind_leader)) / lap_time)


def read_race_snapshot(data):
    info = data.scoring.scoringInfo
    my_car = data.telemetry.telemInfo[data.telemetry.playerVehicleIdx]
    cars_by_id = telemetry_by_id(data)

    me = None
    opponents = []
    for scoring in data.scoring.vehScoringInfo[:info.mNumVehicles]:
        if scoring.mIsPlayer:
            me = read_me(scoring, my_car)
        else:
            opponents.append(read_opponent(scoring, cars_by_id.get(scoring.mID)))

    return RaceSnapshot(
        sim_time=round(info.mCurrentET, 3),
        session=read_session(info),
        me=me,
        opponents=opponents,
    )


def my_car_time(data):
    return data.telemetry.telemInfo[data.telemetry.playerVehicleIdx].mElapsedTime


def read_near_cars(data, my_pos):
    info = data.scoring.scoringInfo
    cars_by_id = telemetry_by_id(data)
    near = []
    for scoring in data.scoring.vehScoringInfo[:info.mNumVehicles]:
        if scoring.mIsPlayer:
            continue
        car = cars_by_id.get(scoring.mID)
        if car is None:
            continue
        dx = car.mPos.x - my_pos[0]
        dy = car.mPos.y - my_pos[1]
        dz = car.mPos.z - my_pos[2]
        if math.sqrt(dx * dx + dy * dy + dz * dz) > NEAR_RADIUS_M:
            continue
        near.append(NearCar(
            id=scoring.mID,
            x=round(car.mPos.x, 3),
            y=round(car.mPos.y, 3),
            z=round(car.mPos.z, 3),
            speed_kmh=round(speed_kmh_of(car.mLocalVel), 2),
            brake=round(car.mUnfilteredBrake, 4),
            throttle=round(car.mUnfilteredThrottle, 4),
            steering=round(car.mUnfilteredSteering, 4),
        ))
    if not near:
        return None
    return NearCars(sim_time=round(my_car_time(data), 3), cars=near)


def race_snapshot_from_dict(d):
    me = None
    if d["me"] is not None:
        me = Me(**d["me"])
    opponents = []
    for o in d["opponents"]:
        opponents.append(Opponent(**o))
    return RaceSnapshot(
        sim_time=d["sim_time"],
        session=Session(**d["session"]),
        me=me,
        opponents=opponents,
    )


def near_cars_from_dict(d):
    cars = []
    for c in d["cars"]:
        cars.append(NearCar(**c))
    return NearCars(sim_time=d["sim_time"], cars=cars)
