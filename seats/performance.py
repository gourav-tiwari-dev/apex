"""Performance engineer, live cues: v1's detector lines, now raised as radio calls.

A lock-up, an off-track, a spin or a long coast becomes a Call. The radio decides when
(or whether) it is said: in v1 the detector loop decided that itself.
"""
from radio import Call, ENGINEER, PERFORMANCE

# the kinds v1 spoke; HARD_BRAKING and CORNER_ENTRY are recorded, never said
SPOKEN_KINDS = {"SPIN", "OFF_TRACK", "LOCKUP", "THROTTLE_LIFT"}
INCIDENTS = {"SPIN", "OFF_TRACK", "LOCKUP"}
STALE_AFTER_S = 6.0      # v1's STALE_THRESHOLD: advice about a corner 6 s ago is useless


def call_from_event(event, event_id):
    if event.kind not in SPOKEN_KINDS:
        return None
    if event.kind in INCIDENTS:
        priority = ENGINEER
    else:
        priority = PERFORMANCE
    return Call(
        seat="performance",
        kind=event.kind,
        sim_time=event.sim_time,
        priority=priority,
        ttl=STALE_AFTER_S,
        conclusion=event.conclusion,
        facts={"corner": event.corner, "speed_kmh": round(event.speed_kmh)},
        template=event.conclusion,
        evidence={"event_id": event_id},
    )
