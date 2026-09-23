# Apex

An AI race engineer for Le Mans Ultimate. A full team on the radio while you race, and one
memory of how you drive.

## v2: the team

| Seat | What it does during a race |
|---|---|
| Race engineer | the start (formation lap, lights out), flags and safety car, penalties, track limits, damage, and where you are every few laps |
| Strategist | fuel or virtual energy to the flag (measured from your own laps), tyres too hot, rain, last lap |
| Spotter | car left, car right, three wide, clear: pre-recorded, starts in 0.01 ms |
| Performance engineer | the one corner that cost you most this lap, the fastest car in your class through each corner, lock-ups, offs, rear snaps under braking, earned praise |
| Racecraft coach | where to pass and where not to ("faster out of Ascari, pass into Parabolica, not before"), how to defend, calm resets after contact |
| Setup engineer | between sessions: brake bias, TC and ABS, only from repeated evidence |
| Team memory | what past races say: your lap-1 habit, the corners that keep biting, every rival you've raced |

The voice is modelled on Max Verstappen's radio style with his engineer's precision, and it is
"aggressive but timed": it never tells you to back down, it tells you where the move works.

## How it decides what to say

```
game (shared memory) -> race state -> 7 seats raise calls -> radio governor -> voice
                                           ^                     |
                                      team memory          urgent: pre-recorded clip
                                                           the rest: LLM phrases it,
                                                           a gate checks it, then TTS
```

- **Code decides what is said, the model only decides how it sounds.** Each call carries the
  message and the facts it rests on.
- **The gate** refuses any line with a number that isn't in the facts (digits or words), banned
  hedging words, labels, questions, or a guessed gender for another driver. A refused line falls
  back to code's own words, or silence.
- **The governor** runs on game time only: urgent calls go at once, everything else waits for a
  straight, one line at a time. A replay at any speed makes the same decisions (tested).
- **Team memory** keeps only facts with evidence: every fact links to the events it came from,
  and a habit needs 3+ occurrences across 2+ separate drives.
- **Budget:** LLM calls are capped at Rs 5 a race; past it, template lines only.

## Run it

```
pip install openai python-dotenv edge-tts pygame pytest
python build_voice_bank.py       # once: renders the urgent lines
```

Put your API key in `.env` as `AICREDITS_API_KEY=...`.

```
python apex.py                   # a race night: brief, every session you drive, debrief after the race
python apex.py --clean           # the same, no swearing (for recordings)
python apex.py --replay          # replay the test tape
python record_race.py            # record a session to a tape, nothing else (no voice, no LLM)
python audit_tape.py TAPE        # which game fields were actually live on a tape
python done_check.py             # is v2 done? checks the latest race
python team_memory.py            # rebuild and print what the team remembers
python -m pytest                 # the test suite, no LLM calls, no sound
```

## How this was built

- **v1 (July - September 2026, tag `v1.0`) was hand-built by me.** I learned Python on it and
  typed every line: the telemetry reader, detectors, replay tape, determinism hash, SQLite memory,
  corner stats, focus contracts and the evaluator.
- **v2 was built with AI assistance (Claude).** I defined what it is (the seven-seat team, the
  DONE test, the persona, the build order) and made the design calls; the AI implemented it.
  Every decision, who made it and why, is in [DECISIONS.md](DECISIONS.md).
- What was measured, not assumed: the LLM's thinking was 96% of the voice's token cost, so it is
  switched off (about 8x cheaper per line); a pre-recorded urgent line starts in 0.01 ms against
  1381 ms rendered live; corners are learned on any track and reproduce the hand-measured Monza
  map from 3 laps; the detectors on v1's test tape produce exactly v1's 48 events.
