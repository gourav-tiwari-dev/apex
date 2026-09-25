# Race awareness: one race model (started 25 Sep 2026, CV mode, his ask)

His words: "everything just doesn't connect together... very weak in race awareness and track
awareness... it should work like a full-fledged real-life team aiding the driver, adapting to any
situation... not just a wrapper, something that makes the difference." "Don't stop until you sort
everything out."

## The root cause (from his 25 Sep races)
Every seat keeps its own partial picture: racecraft its own gap clock, track awareness another,
the strategist its own laps and fuel, the coach read lap times while racecraft read the road.
So they contradicted each other: "17 s a lap quicker, let it go" (start lap vs flying laps),
"last lap" one lap early (his pace, not the leader's), "no need to pit" with 0.6 laps of energy
(no burn measured after a restart).

## Research (sources in DECISIONS.md, 25 Sep rows)
- Pitwall (arXiv 2607.06495): ONE canonical race state; a live Kalman pace filter per car; overtake
  probability from 131k adjacent-car battles + per-circuit difficulty; every sentence split into
  typed claims and checked against the state; sparse state (early laps, restarts) is where language
  models invent things -> template fallback; every prediction scored (Brier).
- Mastering Nordschleife (arXiv 2306.16088): multiclass; overtake probability PER SECTOR from the
  mean time delta of successful passes; traffic time loss per sector.
- f1metrics race simulator: identical cars need ~1.2 s/lap raw pace to pass (0.8 in clean air).
- Spotters (Rolex 24): predict battles half a lap ahead, know which drivers are dangerous in
  traffic, filter, know when to say nothing.
- iRacing crew chief: gaps 1-10 s with closing rate, nearby cars pitting, lead changes, lapping
  traffic, leader lap times, time remaining.
- The LMU AI race engineer on GitHub (imranaskem/race-engineer): fuel/tyre/alerts only, no
  multi-car awareness. That is where Apex wins.

## Design
`race_model.py` = the one picture, updated once per scoring snapshot (and my position every frame),
BEFORE any seat runs. Seats and the coach only READ it.
- track: lap length, corners, 100 m segments, normal speed per segment per class
- every car: distance raced (unwrapped), road trail, status (racing / pit lane / stopped / off /
  finished), class + class place, road pace (per-segment times vs the class reference, filtered)
  and its trend, sector times, pit stops, incidents, reputation
- any pair: same-point gap, closing rate, catch forecast (time + segment, from segment pace),
  segment-by-segment where each is quicker, battle state, passes (road order swaps, where)
- groups: battles, trains, three wide, slower traffic ahead, faster class arriving behind
- race: leader-based laps to go + clock margin, flags, settled, pit events around him
- me: fuel/energy burn, laps remaining, damage, tyres
- forecasts are LOGGED and scored against what happened (calibration, per kind)

## Done = all of these pass on his tapes (tools/race_model_check.py)
- [ ] D1 One picture: no seat owns a gap clock or lap counter; seats + coach read race_model
- [ ] D2 Road pace per car vs its clean posted laps: median error <= 0.5 s/lap (all cars, all tapes)
- [ ] D3 Catch forecasts (every pair, not just him): median time error <= 25%; a corner is only
      named when the tapes show >= 60% hit rate
- [ ] D4 Pass model learned from every battle in the tapes, better Brier than the threshold rule;
      the team call (defend / let by / attack) uses it
- [ ] D5 Laps to go exact at the flag on every timed race tape that reaches it
- [ ] D6 Faster-class arrival: place named within one corner >= 60% (or not named)
- [ ] D7 Replay audit of all tapes: no contradictions between seats and the coach (pace, laps,
      fuel); lines per minute within the budget
- [ ] D8 Update cost <= 2 ms per snapshot on this laptop
- [ ] D9 Coach claims checked by type (positions, gaps, pace direction) against the model
- [ ] D10 Old tapes still replay (missing new fields read as empty)

## Build order
A. record the extra per-car fields LMU gives (sectors, lateral position, estimated lap, pit lap
   distance, count-lap flag, under yellow); field study tool = baselines for D2-D6
B. race_model.py core + live loop wiring
C. seats and coach move onto it (their private clocks go)
D. new awareness: pass likelihood in the team call, rival strengths by segment, traffic arrivals,
   pit awareness, finish forecast, being held up
E. race_model_check.py on all tapes -> D1-D10 -> fix -> commit
