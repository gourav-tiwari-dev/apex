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

### Push-to-talk

Hold R1, ask, let go. Apex matches the question to a fixed list and answers from the live
race in code, no model in the loop: gap ahead / behind, lap time to catch the car ahead,
fuel, laps left, where you are losing time, position, lap times, "quiet for N laps" (urgent
calls and the spotter stay on) and "radio back on". Speech-to-text is Whisper base.en on the
GPU: 51-179 ms per question, measured.

```
pip install faster-whisper sounddevice
python ptt.py --learn            # once, controller plugged in: press R1
python ptt.py --test             # hold R1 and talk: what it heard, how fast, which question
```

Anything longer or off the list goes to the **race agent** (`agent.py`): "Copy. Stand by.",
then a model with six read-only tools (race picture, any driver, his habits, a corner, the car,
the corners ahead) looks at a still picture of the race taken the moment he asked, makes one
call and gives the reason. Measured at lap 4 of a real race: 2.1-3.4 s and Rs 0.14-0.26 a
question, thinking off. Its answers pass their own gate: numbers only from the data or his
question, no speeds, no he/she for other drivers, one rewrite, then an honest "no clean answer".
Asked "he's 2 seconds faster, defend or let him go?", it checked the data first: "1 second a
lap quicker, 2 laps left... give it a clean exit."

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
