# Apex glossary

One word per idea. When code or a comment names one of these, it means exactly this.

## Racing words

| Word | Means | Not to be confused with |
|---|---|---|
| session | one LMU session: practice, qualifying or a race (`session_type` is the game's mSession) | a race night |
| race night | one run of `python apex.py`: the brief, every session he drives, the debrief | a session |
| formation lap | the slow lap behind the grid before the start (game phase 3) | |
| green flag | racing (game phase 5) | |
| safety car | game phase 6, the header's "full course yellow / safety car"; not seen in his LMU races | a yellow |
| yellow | a local yellow in one sector (sector flag 1); the only yellow LMU shows him | a safety car |
| session over | game phase 8: the leader has taken the flag (he may still be on his last lap) | his own finish |
| blue flag | a faster car is lapping him: let it by on the exit | |
| class | GT3, LMP2, Hypercar...; a multiclass race has more than one | |
| class place | his place among his class ("P18 in class") | overall place |
| gap | seconds between two cars at the same point on the road | the game's gap (time behind the leader, subtracted), used only when the road has no answer |
| pace | how much quicker one car is than another over a lap, measured on the road | a lap time |
| trend | how fast a gap is closing or growing, per lap | pace |
| catch | a closing car getting within 0.3 s; said as "within N laps" | |
| fight | two cars within a second of each other; a **battle** is a fight held 8 s | |
| tow | the slipstream on a straight | |
| lift and coast | off the throttle before the braking point, to save fuel or energy | |
| virtual energy | LMU's energy allowance for a race, in percent; runs out like fuel | |
| laps to go | laps left to the flag, with the leader timed to the line on the road | race laps |
| the line | the start/finish line; "at the line" is the frame he crosses it | |
| lap distance | metres from the line along the lap | |
| track limits steps | the game's count of his cuts; enough steps make a penalty | |

## Apex's own words

| Word | Means | Where |
|---|---|---|
| frame | one reading of his car (`CarState`), about 60 a second | game/car_frame.py |
| race snapshot | the whole race at one scoring update (`RaceSnapshot`), about 5 a second | game/race_snapshot.py |
| near cars | the cars close to him this frame, with positions, for the spotter | game/race_snapshot.py |
| tape | a recorded session: frames, race snapshots and near cars, one JSON line each | game/tape.py |
| event | something his car did: a lock-up, a spin, contact, an off... (`Event`) | driving/detectors.py |
| detector | watches frames for one kind of event | driving/detectors.py |
| corner map | where the corners of this track are (`CornerMap`) | driving/track_map.py |
| corner stat | what he did in one corner on one lap | driving/corner_stats.py |
| trail | one car's path on the road: distance and time, point by point | race/gaps.py |
| race model | the one picture of the race every seat reads: trails, gaps, pace, battles, pit stops | race/race_model.py |
| seat | one member of the team; looks at the Moment and returns Calls | seats/ |
| Moment | what every seat sees each frame: the frame, the race, the corner, the events, the race model | seats/__init__.py |
| settle | the start: until it settles, only the spotter, flags and his answers speak | seats/settle.py |
| Call | one thing a seat wants said: its kind, priority, how long it may wait (ttl), its facts and words | radio/calls.py |
| line | the words a Call ends up as, on air | |
| urgent | a Call that goes out at once, over anything (the spotter, flags): a pre-recorded clip | radio/calls.py |
| governor | decides which Call goes on air and when | radio/governor.py |
| desk | cooks a line (its words, then its sound) while its Call waits | radio/desk.py |
| voice | turns a line into sound and plays it; the only owner of the speaker in a session | radio/voice.py |
| bank | the urgent lines, recorded once; the **phrase bank** is the sentences of the instant lines | radio/voice.py, radio/phrasebook.py |
| Max | the engineer's persona: Max Verstappen's radio style | radio/persona.py |
| closer | Max's short tag after a line ("Keep pushing."), taking turns | radio/lines.py |
| clean | no swearing (clean mode, the clean line of a pair); nothing else | |
| gate | the check a line must pass before it goes on air | radio/persona.py, coach/answer_checks.py |
| push-to-talk | he holds the button and speaks; Whisper writes it down | talk/ptt.py |
| intent | which of the code's known questions he asked | talk/hearing.py |
| garbled | too unsure to answer: "say again" | talk/hearing.py |
| mark | "mark" as his first or last word: a note for later, saved and acknowledged ("Marked.") | talk/hearing.py |
| standing order | something he told the team for the rest of the race ("no coaching") | talk/orders.py |
| the coach | the language model with tools, for questions the code cannot answer | coach/agent.py |
| the coach's snapshot | the race held still when he asks the coach (`Snapshot`) | coach/snapshot.py |
| team call | DEFEND, LET BY, ATTACK or FOLLOW, stated to the coach, which may override it with a reason | coach/fight_maths.py |
| model | the race model. The language model is called the coach's model (`LIVE_MODEL`) | |
| focus contract | the one job for the next race: a corner, a target, and how it is graded | memory/contracts.py |
| team memory | what all his races say: habits and rivals, each with its evidence | memory/team_memory.py |
| brief / debrief | said before he drives / after a race | between_sessions/ |
| behaviour lock | replays his tapes and proves a change did not change anything Apex writes | tools/behaviour_lock.py |
| golden | the lock's baseline (golden/, on this laptop) | |
