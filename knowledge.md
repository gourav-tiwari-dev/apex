# Race engineer knowledge

The `knowledge` tool of the push-to-talk agent reads this file: each `##` section is one topic,
found by the words in its title and its `keys:` line. Every fact has its source. Anything
nobody has published or measured is marked UNVERIFIED, and the agent must say so.

## Yellow flag
keys: yellow, caution, overtake under yellow, pass under yellow, flag
- Single yellow: caution, slow down, NO OVERTAKING until past the incident. (LMU wiki, Rules)
- The penalty for passing under yellow in LMU: UNVERIFIED (not published). Give the place back if unsure.
- Apex reads yellows from the game's sector flags and the local yellow flag; "Yellow flag. Yellow." is said the moment one is shown.

## Blue flag
keys: blue, lapping, lapped, faster class, hypercar, let by, multiclass
- Blue flag: a faster car is coming; the slower car must let it pass at the next safe opportunity. (LMU wiki, Rules)
- Standard practice: hold a predictable line, do not move under braking, let it by on a straight or a corner exit. Never lift in its path.
- A GT3 is the slowest class at Le Mans in LMU (Hypercar, LMP2 and GT3 run together): Hypercars and LMP2s will lap him. (WEC classes 2024-25)

## Track limits
keys: track limits, cut, cuts, off track, warning, steps, points, penalty limit
- Leaving the track past the white lines counts against track limits; the game detects it itself. (LMU wiki, Rules)
- Race: every cut adds points. More than 3 points in ONE cut gives an instant drive-through; otherwise points add up, and at the limit a drive-through is given. A very big cut gives a stop-go. Serving the penalty resets the count to 0. (LMU guide, Understanding track limits)
- The limit for this race is in the game data ("penalty_at_steps" in the car tool). Measured live on 24 Sep at Le Mans: 12.
- Cut laps also count against Safety Rank. (LMU guide, Safety Rank tips)

## Penalties
keys: penalty, drive through, drive-through, stop go, stop-go, black flag, serve, time penalty, disqualified
- Kinds: warning, drive-through (through the pit lane without stopping), stop-go (stop in the box for a set time), time penalty (added after the race), disqualification. (LMU wiki, Rules)
- Black flag: come into the pits within a limited number of laps to serve a penalty. How many laps: UNVERIFIED. (LMU wiki, Rules)
- Common reasons: track limits, unsafe re-entry, causing a collision, pit lane speeding, jump start. (LMU wiki, Rules)
- Pit lane speed limit at Le Mans in LMU: UNVERIFIED (the game uses a pit limiter; the number is not in Apex's data).

## Safety rank and driver rank
keys: safety, safety rating, safety rank, sr, driver rank, driver rating, dr, rating, contact, incident
- Safety Rank (SR) is hurt by contact with cars and barriers (harder hits cost more) and by cut laps. LMU uses NO FAULT: both drivers lose the same. (LMU guide / LMU wiki, Rating System)
- So avoiding contact matters even when the other car is to blame: let a much faster car go rather than fight it.
- Driver Rank (DR) is Elo-like: points won and lost against every other driver by finishing position. (LMU guide, Driver Rank)

## Race start
keys: start, formation, lights, rolling start, jump start, lap one, lap 1, first lap
- Formation lap: warm the tyres and brakes, keep your place. A jump start is a penalty. (LMU wiki, Rules; LMU guide, Race starts)
- His own record (team memory): lap 1 is where he has lost the most; the rule is survive lap 1.

## Full course yellow and safety car
keys: full course yellow, fcy, safety car, sc, slow zone, red flag, restart
- Full course yellow: everyone slows to a set speed and keeps the gaps. Safety car: queue behind it until the track is clear. Red flag: the race is stopped, return to the pits slowly. (LMU wiki, Rules)
- The FCY speed: UNVERIFIED.

## Chequered flag
keys: chequered, checkered, finish, flag, cool down, last lap
- Finish the lap and go to the cool-down lap. (LMU wiki, Rules)
- Apex keeps going until HIS flag, not the leader's.

## Tyres
keys: tyre, tyres, tires, temperature, temps, overheating, pressure, wear, grip, cold tyres
- Apex calls tyres "cooking" above 105 C (the strategist's line; the car tool shows the temperatures). The ideal window for LMU GT3 tyres: UNVERIFIED (not published).
- Overheating comes from sliding: smoother steering, less wheelspin on exit, less slide into the corner.
- Cold tyres on the out-lap and after a yellow: less grip, brake earlier for a lap.

## Car balance
keys: understeer, oversteer, balance, rear, loose, snap, push, nose, handling
- Entry understeer: brake a touch earlier and straighter, trail the brake to keep the nose loaded, less steering.
- Mid-corner: be patient, wait for the car to turn before the throttle.
- Exit oversteer or wheelspin: straighten the wheel before full throttle; more traction control if it keeps happening.
- Entry oversteer (rear loose on the brakes): less trail braking, smoother release, brake bias forward.
- Apex measures balance per corner and phase (balance.py, yaw rate against speed and steering) once it has 3 laps there; the thresholds are guesses tuned on his own feel.

## In-car settings
keys: brake bias, bias, tc, traction control, abs, motor map, engine map, arb, anti roll, setup, fixed setup
- Ranked races run fixed setups, but brake bias, TC and ABS can be changed from the cockpit. (setup engineer)
- The direction of the numbers (a higher TC or ABS = more help; brake_bias_rear = share on the rear): UNVERIFIED in the game.

## Tow and slipstream
keys: tow, slipstream, draft, drafting, slip
- Measured on his own tapes (25 Sep): within 0.5 s of the car ahead he gains 1 to 4 km/h over clean air; from 0.5 to 1.0 s, about nothing. So the tow in LMU only works close.

## What Apex can and cannot see
keys: can you see, mirrors, intentions, data, missing, know, cameras, what do you know
- Sees: every car's position on the track, speed, gaps, lap times, pit state, impacts; his own car in full (tyres, brakes, fuel, energy, damage, settings); flags and weather.
- Cannot see: mirrors, racing lines, what another driver intends, other cars' tyre wear or damage (online; UNVERIFIED whether the game sends them).
