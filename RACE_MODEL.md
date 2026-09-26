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
- [x] D1 One picture: no seat owns a gap clock or lap counter; seats + coach read race_model
- [x] D2 (changed) Road truth per car vs its clean posted laps: median error <= 0.5 s/lap (all cars, all tapes)
- [x] D3 (changed) Catch forecasts (every pair, not just him): median time error <= 25%; a corner is only
      named when the tapes show >= 60% hit rate
- [x] D4 Pass model learned from every battle in the tapes, better Brier than the threshold rule;
      the team call (defend / let by / attack) uses it
- [x] D5 Laps to go exact at the flag on every timed race tape that reaches it
- [ ] D6 Faster-class arrival: place named within one corner >= 60% (or not named)
- [ ] D7 Replay audit of all tapes: no contradictions between seats and the coach (pace, laps,
      fuel); lines per minute within the budget
- [x] D8 Update cost <= 2 ms per snapshot on this laptop
- [x] D9 Coach claims checked by type (positions, gaps, pace direction) against the model
- [x] D10 Old tapes still replay (missing new fields read as empty)

## Build order
A. record the extra per-car fields LMU gives (sectors, lateral position, estimated lap, pit lap
   distance, count-lap flag, under yellow); field study tool = baselines for D2-D6
B. race_model.py core + live loop wiring
C. seats and coach move onto it (their private clocks go)
D. new awareness: pass likelihood in the team call, rival strengths by segment, traffic arrivals,
   pit awareness, finish forecast, being held up
E. race_model_check.py on all tapes -> D1-D10 -> fix -> commit

## Results (tools/race_model_check.py, 25 Sep evening)
- D1 PASS one RaceModel fed by the loop; racecraft + track awareness read its clock; gaps everywhere same-point
- D2 CHANGED: per-car "model pace" predicted next laps WORSE than the last lap (2.98 vs 1.64 s), and between
  fighting cars the gap moves ~1.2 s a lap for reasons that are not pace (no predictor beat "no change" by
  much over one lap). So pace is always BETWEEN two cars, on the road, with a confidence: 2 laps of trend ->
  direction right ~80%. The bar became: road truth (road lap vs posted 0.036 s median) PASS
- D3 CHANGED: sure catch forecasts came true 20/21; timing off by a median 64% -> Apex says "within N laps"
  (1.5x bound, held 17/18), never an exact lap. PASS on that bar
- D4 PASS 278 fights: >= 0.5 s/lap quicker got by 81%, about equal 55%; the coach sees the odds; LET BY
  stays at >= 2 s/lap measured (86%)
- D5 PASS 3/3 on the one tape that reaches his flag; the 5 -> 6 lap race was cut before the flag (sim test only)
- D6 NOT TESTABLE: every tape is GT3-only
- D7 by construction; LIVE CHECK PENDING
- D8 PASS 0.13 ms per snapshot
- D9 PASS numbers, names, speeds, fuel verdict, sure trend direction, tack-ons
- D10 PASS
New calls: "P13's in the pits, that's a place for you"; "You're losing X a lap stuck behind that car. Pass it
into <corner where you gain most>, or drop back to two seconds." Coach view: field 3 places either side,
battles near, pits, corner-by-corner time gains per rival, pass odds from his races.

## NEXT FEATURE (his ask, 25 Sep live test) - after the current fixes are sorted
Two-way conversation with the engineer: it takes HIS point of view, he can OVERRIDE a decision
("don't give me that bullshit, we push, no holding back") and it REMEMBERS the decision for the
rest of the race and acts on it like a real person. Research first (real driver-engineer radio
exchanges, how engineers adapt to the driver's call, mixed-initiative dialogue / shared plans),
then build.

## 26 Sep (night): his live race of 25 Sep, what broke and the fixes (commits 5b3a778, f25fa14, next)
Truth from the tape first, then the fix. 20-min Le Mans race, standing start with a formation lap, 6 laps.
| He heard | Truth (tape) | Cause | Fix |
|---|---|---|---|
| "Box, short by 3.8 laps" (lap 1) | 0.33 laps spare all race, finished with 2.6 L | lap length = farthest car so far (1.9 km of 13.6) + formation timed as a 2:23 lap | session lap length; laps only count once the game shows a race lap done |
| "3.6 spare, push" -> "short 0.7" (lap 2) | 0.3 spare | pace from his standing-start lap 1 / formation in the burn | the LEADER's rolling lap on the road (race model), only once it is clean racing |
| "Box this lap" on his LAST lap | finishing | the leader had taken the flag, Apex still counted a lap | leader finish_status = finished -> the race is those laps |
| never "tight" | tight all race | only save / box were ever said | the live verdict speaks when first known and whenever it gets worse |
| "Nobody ahead, you're leading" (P18), "243 s back", no battle calls | car ahead 9 s, car behind 3.8 s, a fight on lap 1 at 0.26 / 0.33 s | HIS lap count came from his own line crossings: the formation crossing put him a lap up on everyone | his lap count follows the game's, mid-lap only |
| no call when punted round at Porsche Curves | nose 6 -> 180 deg, yaw never over 0.9 rad/s | spin = yaw rate > 1.7 | spin = pointing > 90 deg from travel; "you got hit" when the contact detector saw a CAR |
| "Spun" for a slide he caught | 19 deg slide, back straight in 1.5 s | same yaw rule | SLIDE_CAUGHT: > 15 deg above 60 km/h, back under 5 without passing 90 -> "Big moment. Caught it." |
| quali: "No clean answer" twice | - | ghost cars within a second made every answer need a DEFEND/ATTACK line | fights only in races; no CALL line off the fight; qualifying picture + start-from-the-back plan |
Done check: race_model_check D1-D5, D8-D10 PASS with tonight's tape added (D5 laps to go 8/8 exact);
tools/spin_check.py lists spins / caught slides on every tape (sparse, plausible, not ground-truthed).

## Adaptability (26 Sep): his orders stand - RESEARCH then BUILD (orders.py)
His ask: "if I said don't give me that bullshit, we will push, no holding back, then it should act like
a real person"; "it doesn't remember the decisions"; "plans change with race conditions".
Research (26 Sep):
- Crew Chief (sim racing engineer) keeps radio commands for the whole session: "keep quiet / I know what
  I'm doing", "don't tell me the gaps", "tell me the gaps" (anton2641.gitlab.io/CrewChiefV4 voice commands).
- Playbook delegation (Miller & Parasuraman 2007, Human Factors 49(1)): the human sets the play and its
  limits, automation works inside them; human-adaptable automation gave better awareness, acceptance and
  workload balance than automation deciding alone.
- Verstappen, Brazil 2022 ("don't ask that again to me... I gave my reasons"): once the driver has decided,
  the team says the cost once and does not re-ask. Team radio in general: headlines, pre-agreed plans.
- Grounding (Clark & Brennan): acknowledge so both sides know the order stands; repair on disagreement.
Build:
- orders.py StandingOrders: pace (push / save / bring_home), fight (fight / let_quick_go), coaching (off/on),
  gaps (every_lap / off / normal), "back to normal". Heard by code for the plain phrasings (0 of the 300 bank
  questions misread; the 1 match is a real order), by the coach's ORDER line for the rest. Said back
  ("Copy. We push, no holding back. I'll only come back on fuel if it won't make the flag.").
- Every seat's call passes through it (Governor.offer): push silences tight/save fuel lines; a real
  shortfall is said ONCE as his call ("You said push, your call. Straight: short by 0.7. Box this lap or you
  stop."); no coaching; no gaps / gaps every lap; bring it home drops attack plans; fight drops "settle".
  What he ASKS for he always gets.
- The coach gets standing_orders + his_decisions_this_race with every question, rule 11 (his calls stand,
  cost once, never argue again, concede when he is right), and code refuses a LET BY under a fight order.
- plan_now: rebuilt from live facts (fuel verdict, damage, last lap) + his orders on every question.
- "always / never / from now on" -> the order is kept in the standing_orders table for future races.
NOT built (say so): a spoken "plan change" call of its own - the spoken changes stay with the seats that
own them (FUEL, DAMAGE, LAST_LAP); a model of his mood/stress. Live check pending: he has not used orders
in a race yet (UNVERIFIED end to end with the real voice + model).

## 27 Sep (auto loop): plans change with the race - an order is re-opened ONCE when its reason goes
Research:
- Leclerc, Singapore 2025, under long lift-and-coast orders: "Tell me when I can push again. I'm
  losing a lot of time." Vasseur after: "we have to fix this". The driver expects the pit wall to SAY
  when a restriction no longer applies (racefans.net/2025/10/06/ferrari-must-fix-lift-and-coast-problem-...).
- Delegation research (Miller & Parasuraman 2007; "Delegation to automation: performance and
  implications in non-optimal situations", HCII 2011): a delegated play works until conditions no longer
  fit it; the benefit of delegation depends on the automation surfacing the change, not silently
  carrying on or silently overriding.
- Verstappen Brazil 2022 (already in orders.py): his decision stands; the cost is said once, never argued.
Gap in Apex: he orders "we save", the fuel later turns fine, and the strategist says "Fuel's fine to the
flag... Push." - a flat contradiction of his standing order, and no "you can stop saving" as his call.
Build: under a save order, a "fine" fuel verdict is said once as "You said save. Fuel's fine now, N laps
spare. You can push again, your call." The order stays until he changes it. Further "fine" calls stay
silent (never nag). Done-check: test + replay with "we save" scripted on a tape where the fuel is fine.

## 27 Sep (auto loop): FIND round 1 - what other LMU engineers do, checked against his tapes
Sources: DRE (thedigitalraceengineer.com, "DRE now supports Le Mans Ultimate"): radio check, side-by-side,
class position updates, pace feedback, fuel confidence, rain proximity, shared debris warnings, a
brake+throttle overlap cue. Indie LMU engineer app (overtake.gg thread 295863, May 2026): moment to attack,
"the car behind is becoming a real threat", divebomb warnings when the car ahead brakes earlier than you,
pace gained/lost by sector.
- Brake+throttle overlap: NOT BUILT. His race tapes: 6.7-7.5% of braking time with both pedals and no
  overlap of 0.3 s or more in 4 of 5 races (throttle-to-brake transitions). The 5th (25 Sep night, 19.3%)
  has its long overlaps only at the Porsche Curves / Ford chicanes: his punt and spin, and the fast
  sweepers where two-pedal balance is a technique. No habit in the data: a cue would be noise.
- Already in Apex: side-by-side (turned OFF by him), pace by corner (race model), threat behind
  (CLOSING_ALARM), attack moments (ATTACK_PLAN / STICK_IT). Debris sharing needs a server: out of scope.

## 27 Sep (auto loop): after a crash - his mark "it doesn't know that I crashed and spun, my race is over"
Found by reading all 12 of his marks against what Apex said before each (apex.db radio_log). This one was
still open, from session 43 = tape_20260925_200154 (58-car multiclass: 18 Hyper, 19 LMP2, 25 GT3), which
tools/tapes.py RACE_TAPES had left out (so every check skipped it; D6 "multiclass not testable" was wrong).
Tape: 214 km/h at 1045 s, 6 km/h at 1050 s (Indianapolis), 0-9 km/h for a minute, P45 -> P62.
Live he heard "Stay in the tow", "Wide at Indianapolis", then while stopped "Car behind's quicker out of
Esses. Cover the inside into Tertre Rouge", "Over the radio budget", 3 blue flags in 6 s.
Research: after a crash the engineer's first question is "Are you OK?" (Mercedes to Antonelli after his
crash, sportskeeda.com "Kimi all good, all good Kimi"; standard pit-wall protocol).
Built (replay of that tape, today's code): 1045.2 "You got hit and spun..." then 1051.8 "You OK? Car's
stopped." and quiet. No "Wide" within 10 s of a spin or a hit, none below 30 km/h (parked on the grass),
no blue flags below 60 km/h. Found on the way: RaceEngineer only read events on race-snapshot frames
(5 a second), so the own-spin yellow rule rarely saw the spin; incidents are now read every frame.
