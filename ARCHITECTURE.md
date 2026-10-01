# Apex: where everything is

Start here. This page is the map: what Apex does, where each part lives, the rules the
code keeps, and the order to read it in. Words in *italics* are in GLOSSARY.md.

## What Apex does, in one paragraph

Apex reads Le Mans Ultimate about 60 times a second (or plays back a *tape* of a race).
Every *frame*, the *detectors* watch his car, the *race model* keeps every car on the
road in one picture, and eight *seats* (spotter, race engineer, strategist, performance,
racecraft, track awareness, qualifying, memory recall) each look at the same *Moment*
and may raise a *Call*. The *governor* puts one call at a time on air, the *desk* turns
it into words and sound, and the *voice* plays it. When he holds push-to-talk, his words
are *heard*: a question the code knows is answered at once (talk/answers.py), anything
else goes to the *coach*, a language model with tools. Everything is written to apex.db,
and before and after he drives the *brief* and *debrief* read it back.

## One frame, left to right

```
 game/                 driving/                race/               seats/            radio/
 +-------------+       +----------------+      +--------------+    +-------------+   +-----------+   +-----------+   +---------+
 | LiveSource  | ----> | LapCounter     | ---> | RaceModel    | -> | 8 seats     | ->| Governor  | ->| RadioDesk | ->| Voice   |
 | or tape     | frame | CornerStats    |      | (gaps, pace, |    | update(     |   | one call  |   | words and |   | bank,   |
 | (CarState + |       | detectors ->   |      |  battles)    |    |  Moment)    |   | on air    |   | sound     |   | Azure,  |
 | snapshot)   |       |   events       |      | race/facts   |    |  -> Calls   |   +-----------+   +-----------+   | edge,   |
 +-------------+       +----------------+      +--------------+    +-------------+         ^                         | offline |
                                                                                            |                         +---------+
 talk/   push-to-talk -> hearing -> answers (code)  --------------------------------------+|
 coach/                           -> the coach (a model with tools) ----------------------+
 memory/ every event, lap, corner stat and radio line -> apex.db
```

session.py runs this. `Session.one_frame` is the list of steps a frame goes through, in
order; each step is a short method below it.

## Codemap: where is the thing that does X?

| Folder | Its job | Read first |
|---|---|---|
| apex.py | the command: a race night (brief, every session, debrief) | `race_night` |
| session.py | one session, frame by frame | `Session.one_frame` |
| game/ | reading the game: its numbers, its shared memory, frames of his car, the race snapshot, tapes | constants.py, car_frame.py |
| driving/ | his own car: laps, corners and the corner map, balance, the detectors | detectors.py |
| race/ | the whole field: gaps on the road, the race model, facts (his place, laps to go) | race_model.py |
| seats/ | the team: each seat looks at the Moment and returns Calls | spotter.py (the smallest) |
| radio/ | a Call's life: the governor, the desk, the voice, the phrase bank, how things are worded | governor.py |
| talk/ | his side: push-to-talk, hearing him, the code's answers, his standing orders | hearing.py |
| coach/ | the language model for the questions the code cannot answer | agent.py |
| memory/ | the database, the corner report, the focus contracts, team memory | db.py |
| between_sessions/ | the brief, the debrief, the setup engineer | brief.py |
| dev/ | scripts run by hand: record or audit a tape, build the voice bank, ask the coach offline | |
| tools/ | study and check tools on his tapes (the truths, the behaviour lock) | behaviour_lock.py |
| tests/ | pytest | |

Every file, by its job:

| File | What it does |
|---|---|
| game/constants.py | the game's numbers by name: session types, phases, flags, wheel order |
| game/car_frame.py | `CarState`: one frame of his car |
| game/live_source.py | `LiveSource`: frames from the game's shared memory |
| game/race_snapshot.py | `RaceSnapshot`, `Me`, `Opponent`, `NearCars`, and reading them |
| game/tape.py | `ReplaySource` plays a tape back, `Recorder` writes one |
| game/lmu_data.py, sharedmemory.py | the game's shared-memory header, struct for struct, and its reader (vendored) |
| driving/laps.py | his line crossings and the race lap he is on |
| driving/corner_stats.py | what he did in each corner, each lap |
| driving/detectors.py | the moments worth a word: lock-ups, spins, contact, offs, wheelspin... |
| driving/track_map.py | where the corners are: `CornerMap`, and learning a new track |
| driving/balance.py | understeer and oversteer, per corner phase |
| race/gaps.py | every car's trail on the road; same-point gaps |
| race/race_model.py | the one picture of the race every seat reads |
| race/facts.py | same lap, class neighbours, his place as said, laps to go |
| seats/__init__.py | `Moment`: what every seat sees each frame |
| seats/spotter.py | car left, car right, clear |
| seats/race_engineer.py | the start, flags, penalties, damage, gap reports |
| seats/strategist.py | fuel or energy to the flag, tyres, weather, last lap |
| seats/performance.py | where the time goes, corner by corner |
| seats/racecraft.py | fights: plans, closing cars, passes, praise |
| seats/track_awareness.py | the road ahead and the mirrors: slow cars, faster classes, fights |
| seats/qualifying.py | qualifying laps |
| seats/memory_recall.py | team memory on the radio, at the corner it is about |
| seats/settle.py | the start: only the spotter, flags and his answers until it settles |
| radio/calls.py | `Call` and the priority ladder |
| radio/governor.py | which call goes on air, and when (its rules are its docstring) |
| radio/desk.py | `RadioDesk`: cooks a line while its call waits |
| radio/voice.py | `Voice`: the only owner of the speaker |
| radio/phrasebook.py | the sentences rendered ahead of time, joined in under a millisecond |
| radio/words.py | lap times, gaps in tenths, Max's lines in turn, speakable shorthand, numbers said in words |
| radio/lines.py | Max's closers for the seats' own lines |
| radio/azure_voice.py, offline_voice.py, tts.py | the voices: Azure with emotion, Windows offline, the brief's speaker |
| talk/ptt.py | the button, the microphone, Whisper |
| talk/hearing.py | which question it was, or an order, a mark, a "copy" |
| talk/answers.py | every question the code answers by itself |
| talk/orders.py | his standing orders: heard, kept, said back |
| talk/scripted_talk.py | his voice on a replay (orders tested on real tapes) |
| coach/agent.py | `RaceAgent`: asks the model, hands the answer back |
| coach/prompt.py | what the coach is told, and its tool list |
| coach/snapshot.py | the race held still when he asks, and the tools |
| coach/fight_maths.py | the team call: DEFEND, LET BY, ATTACK, FOLLOW |
| coach/answer_checks.py | the gate every coach answer passes, and the words it never says |
| coach/race_tools.py | what the wider tools return (standings, laps, the database...) |
| coach/llm.py | the model's provider, names, client and cost |
| memory/db.py | the tables and every writer a session uses |
| memory/corner_report.py | the per-corner report after a session |
| memory/contracts.py | the one job for the next race, and grading it |
| memory/team_memory.py | his habits and rivals across all races |
| between_sessions/brief.py, debrief.py, setup_engineer.py | before and after driving |

## The rules the code keeps

- **Sim time only.** Every decision uses the game's clock, never the wall clock, so a
  replay at any speed makes the same decisions. The governor's decision hash proves it.
- **Seats only return Calls.** A seat never speaks and never writes the database; the
  session saves what they found. (seats/ imports nothing from memory/.)
- **One speaker in a session.** Everything said during a session goes through
  `radio/voice.py` `Voice`, so nothing talks over anything else by accident. (The brief and
  the debrief, outside a session, speak through radio/tts.py.)
- **One picture of the race.** The session feeds the race model before any seat; seats and
  the coach only read it.
- **Numbers on air come from facts.** A coach answer with a number no tool gave is refused
  (`coach/answer_checks.py` `check_answer`).
- **Layers.** game/ imports nothing from Apex. driving/, race/ and memory/ build on game/.
  seats/ build on those. The radio, talk and the coach sit above the seats, session.py
  above everything, apex.py on top. `radio/calls.py` and `radio/words.py` are shared
  vocabulary: everyone uses them. The imports that go the other way, on purpose:
  - radio/phrasebook.py reads the seats' sentences (to render every one ahead of time)
  - coach/race_tools.py asks the setup engineer (the coach's setup tool)
  - between_sessions/debrief.py opens the model

## Reading order: follow one frame

1. apex.py, `race_night`: a night is a brief, sessions, a debrief.
2. session.py, `run_session` and `Session.one_frame`: the steps of a frame.
3. game/tape.py `ReplaySource` (or game/live_source.py) and game/car_frame.py `CarState`:
   where a frame comes from.
4. driving/laps.py, then driving/detectors.py (`Detector`, then `SpinDetector`).
5. race/gaps.py (`Trail`), race/race_model.py, race/facts.py.
6. seats/__init__.py (`Moment`), seats/spotter.py, then seats/race_engineer.py.
7. radio/calls.py (`Call`), radio/governor.py (`offer`, `step`), radio/desk.py,
   radio/voice.py.
8. talk/hearing.py, talk/answers.py, then coach/agent.py.
9. memory/db.py: what a session leaves behind.

## How to check a change

- `python -m pytest`: 332 tests.
- `python tools/behaviour_lock.py check`: replays 9 of his race tapes, and 2 of them with
  his voice scripted in, and compares every row Apex writes (radio lines, events, laps,
  corner stats, the session...) with the baseline in golden/ (on this laptop, not in git;
  recorded from the code before the refactor). A change that should not change behaviour
  must print LOCK HOLDS. A behaviour fix shows its differences; then `record`, and say so
  in the commit.
- The tape checks in tools/ (fuel_truth, gap_truth, flag_truth, praise_truth...) measure
  whether what Apex said was true.

## Still open

- Some ideas are still written twice where the copies behave differently (laps to go and
  its fallbacks, the fuel words, "same lap", lap length): changing them changes what Apex
  says, so they wait for their own fix with tape results. RESUME_V3.md lists them.
- A few functions are still long: `RaceEngineer.update`, `Racecraft.update` and
  `closing_calls`, `Snapshot.run_tool`, `RaceAgent.think`.
