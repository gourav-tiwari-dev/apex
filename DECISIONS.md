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
