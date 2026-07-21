from sharedmemory import MMapControl
from lmu_data import LMUObjectOut, LMUConstants

info = MMapControl(LMUConstants.LMU_SHARED_MEMORY_FILE, LMUObjectOut)
info.create(0)

info.update()   # one fresh snapshot

scoring   = info.data.scoring.vehScoringInfo       # timing / position
telemetry = info.data.telemetry.telemInfo          # physics

# The arrays are fixed-size and padded with empty slots, so use the real
# vehicle count instead of printing all 100+ rows. If these field names are
# different in your structs, tweak them — the fallback just dumps everything.
score_n = getattr(info.data.scoring,   "mNumVehicles", len(scoring))
tele_n  = getattr(info.data.telemetry, "mNumVehicles", len(telemetry))

print(f"scoring vehicles:   {score_n}")
print(f"telemetry vehicles: {tele_n}")
print()

print("SCORING (timing / position):")
for i in range(score_n):
    veh = scoring[i]
    name = getattr(veh, "mDriverName", "")
    print(f"  idx {i:2d}  mID={veh.mID}  player={veh.mIsPlayer}  lapDist={veh.mLapDist:.1f}  {name}")

print()
print("TELEMETRY (physics):")
for i in range(tele_n):
    veh = telemetry[i]
    print(f"  idx {i:2d}  mID={veh.mID}")

def match_opponents(scoring, telemetry):
    # index the physics rows by mID, so we can look them up instantly
    telemetry_by_id = {t.mID: t for t in telemetry if t.mID != 0}

    opponents = []
    for s in scoring:
        if s.mID != 0:                            # a real opponent
            physics = telemetry_by_id.get(s.mID)  # find its physics by matching mID
            opponents.append((s, physics))
    for s, t in opponents:
        print(f"mID {s.mID}=={t.mID}   {s.mDriverName}   lapDist={s.mLapDist:.0f}")

match_opponents(scoring,telemetry)