# Apex

An AI race engineer for Le Mans Ultimate.

Apex watches you drive, tells you after the session where you lose the most time,
gives you **one** thing to fix, and then checks next session whether you actually fixed it.

## The loop

```
drive  ->  debrief  ->  one contract  ->  drive again  ->  verdict
```

1. **Drive.** `live_telemetry.py` reads the game's shared memory at 60 Hz. It records
   every frame to a tape file, calls out events out loud (lock-ups, off-tracks, spins),
   and saves laps and per-corner stats to SQLite.
2. **Debrief.** `debrief.py` compares your corners to a reference lap, finds the corner
   costing you the most time, and asks an LLM to explain it using only the measured numbers.
3. **Contract.** The debrief ends with one job, for example:
   *T1 Rettifilo, minimum speed 52.2 -> 56.6 km/h, over at least 8 clean laps.*
4. **Verdict.** Next session's debrief grades that job first:

| verdict | meaning |
|---|---|
| `hit` | median minimum speed reached the target |
| `moved` | not at the target, but at least 1.5 km/h better than before |
| `flat` | no real change |
| `insufficient` | not enough clean laps at that corner to judge |

## Design rules

- **The LLM never drives the 60 Hz loop.** Everything live is plain code; the model only
  phrases sentences and writes the debrief.
- **Numbers strict, conclusions loose.** The debrief may reason past the data, but it may not
  state a number that was not measured. Tested: 0 invented numbers across 14 debriefs.
- **If the data can't explain it, say so.** The debrief names the missing measurement instead
  of guessing a cause.
- **No "did you execute it?" check yet.** There is no steering channel on the tape, so Apex
  cannot tell *what you did*, only *what speed came out*. It comes back in phase 2.

## Run it

```
pip install openai python-dotenv edge-tts pygame
```

Put your API key in `.env` as `AICREDITS_API_KEY=...`.

```
python live_telemetry.py     # drive (set REPLAY = False at the top to read the live game)
python debrief.py            # debrief the latest session
python debrief.py 11         # debrief a specific session
python q.py "SELECT ..."     # quick look at the database
```

Every session is recorded to a `.jsonl.gz` tape, and replaying a tape rebuilds the same
database, so the database is only a cache of the tapes.

## Files

| file | what it does |
|---|---|
| `live_telemetry.py` | reads the game, detects events, speaks, records the tape |
| `memory.py` | the SQLite database: sessions, laps, corner stats, contracts, verdicts |
| `debrief.py` | the post-session debrief, the verdict and the next contract |
| `coach.py` | turns live events into short spoken lines |
| `tts.py` | text to speech |
| `reference_hymo.json` | the reference lap Apex compares you against |
