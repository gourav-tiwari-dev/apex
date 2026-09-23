# Apex decisions

Every design call, who made it, and why. v1 was hand-built by Gourav. From v2 on, Gourav is the
architect and decides, and Claude (AI) implements. This file is the map for learning v2 after it's built.

| Date | Decision | Who | Why |
|---|---|---|---|
| 2026-09-23 | v2 = a race engineer on the radio DURING the race: 7 seats (race engineer, strategist, spotter, performance engineer, racecraft coach, setup engineer, team memory) | Gourav | "like racing IRL, only on sim". One system with one memory finds gaps that separate tools can't |
| 2026-09-23 | DONE = one full ranked race, never switched off, every seat makes a real call | Gourav | a check that can pass, instead of "covers every aspect" |
| 2026-09-23 | Build it, don't buy Crew Chief for the basics | Gourav | tried it: static scripted spotter, no strategy, no memory of the driver |
| 2026-09-23 | Persona = Verstappen voice, swearing kept, "aggressive but TIMED" | Gourav | the original "never back down" feeds his real weakness (hasty moves in close racing cost safety rating) |
| 2026-09-23 | Team memory first. Part A (recorder sees the race) before part B (profile) | Gourav (memory first), Claude (A before B) | the v1 tape holds only his own car, so a profile would be blind to close racing |
| 2026-09-23 | Any track, not only Monza | Gourav | ranked races rotate tracks; it's for daily use |
| 2026-09-23 | Push-to-talk questions included | Gourav | real engineers answer the driver |
| 2026-09-23 | LLM cap Rs 5 per race, template lines when the cap is hit | Gourav | measured: v1 = Rs 0.62 per 3 laps; Rs 100 must last |
| 2026-09-23 | Radio lines use DeepSeek v4-flash with thinking OFF | Claude (measured) | thinking was 96% of the live-line tokens; off = 14 tokens/line, 1.8 s, about 8x cheaper |
| 2026-09-23 | Jev (decision model) deferred | Claude | waitlist only; a seat's "decide" step stays swappable for later |
| 2026-09-23 | v1 kept as a standalone copy (apex_v1_clip) for clip #1; v2 grows on main; v1.0 tag = commit b6fc35e | Gourav | record the clip without blocking the build |
| 2026-09-23 | Build all of v2 first, learn it afterwards in the v1 style | Gourav | a finished coach to race with sooner |
| 2026-09-23 | apex.db and new tapes are no longer tracked in git | Claude | the db is a cache and conflicts on merge; race tapes are tens of MB |
| 2026-09-23 | Extras E1-E19 and DONE upgrades D1-D5 added to the plan | Claude proposed, Gourav approved | he asked for anything a real team has that the 7-seat list missed |
| 2026-09-23 | Tape v2 = one file: 60 Hz car frames with a "race" line (every scoring update, ~5/s) and a "near" line (cars within 60 m, every frame) written just before the car frame they belong to | Claude | the spotter needs positions far faster than scoring gives them; recording far cars at 60 Hz would make tapes huge |
| 2026-09-23 | New CarState fields default to None | Claude | Gourav's rule: old tapes must keep working |
| 2026-09-23 | Opponents matched by mID over the whole telemetry array, never by position | Claude (measured 19 Aug) | the telemetry order differs from the scoring order; matching by position pairs the wrong cars silently |
| 2026-09-23 | audit_tape.py marks every new field LIVE / CONSTANT / DEAD before any seat relies on it | Claude | several LMU fields are known dead (forces, load); find out from data, not in a race |
| 2026-09-23 | record_race.py: tape-only recording (no voice, no LLM, no db), stops itself at session end | Claude | a 15-lap dev tape through apex.py would spend about Rs 3 on v1 coach lines nobody needs |
| 2026-09-23 | Corners on any track are learned from his laps: 5 m slices, a slice counts when braking or lateral >= 0.6 g on at least half the laps, widened to a fifth of the laps, +25 m | Claude (measured) | reproduced all 7 hand-measured Monza corners from only 3 laps, brake-point starts within 0-6 m on 11 laps |
| 2026-09-23 | A throttle lift on its own does NOT count as corner evidence | Claude (measured) | with lifts counted, warm-up/traffic lifts and the Serraglio kink became fake corners until ~11 laps |
| 2026-09-23 | Monza keeps the hand-measured map and names; learned maps are saved to track_maps/<track>.json, corners named "Turn N" and renameable by hand | Claude | his hand map is validated; a readable file lets him add real names |
| 2026-09-23 | One corner lookup (track_map.corner_at) replaces the three copies in live_telemetry | Claude | v1 defect #5: "one fact, one place decides it" (his own rule). v1 hash unchanged |
