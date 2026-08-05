# Graph Report - .  (2026-08-04)

## Corpus Check
- Corpus is ~4,721 words - fits in a single context window. You may not need a graph.

## Summary
- 116 nodes · 201 edges · 9 communities
- Extraction: 81% EXTRACTED · 19% INFERRED · 0% AMBIGUOUS · INFERRED: 38 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- LMU Data Structs
- Threshold Detectors
- Telemetry Sources & Recorder
- Mmap Control Layer
- Coaching & Voice Output
- Cross-Platform Mmap
- SimInfo Lifecycle
- Lock-Up / Slip Detection

## God Nodes (most connected - your core abstractions)
1. `MMapControl` - 22 edges
2. `LMUConstants` - 16 edges
3. `LMUObjectOut` - 16 edges
4. `Detector` - 14 edges
5. `LiveSource` - 8 edges
6. `HardBrakingDetector` - 8 edges
7. `LockUpDetector` - 8 edges
8. `ThrottleLift` - 8 edges
9. `OffTrackDetector` - 8 edges
10. `SpinDetector` - 8 edges

## Surprising Connections (you probably didn't know these)
- `CarState` --uses--> `MMapControl`  [INFERRED]
  live_telemetry.py → sharedmemory.py
- `LiveSource` --uses--> `MMapControl`  [INFERRED]
  live_telemetry.py → sharedmemory.py
- `ReplaySource` --uses--> `MMapControl`  [INFERRED]
  live_telemetry.py → sharedmemory.py
- `Event` --uses--> `MMapControl`  [INFERRED]
  live_telemetry.py → sharedmemory.py
- `Detector` --uses--> `LMUConstants`  [INFERRED]
  live_telemetry.py → lmu_data.py

## Import Cycles
- None detected.

## Communities (9 total, 0 thin omitted)

### Community 0 - "LMU Data Structs"
Cohesion: 0.08
Nodes (25): LMUApplicationState, LMUEvent, LMUGeneric, LMULayout, LMUPathData, LMUScoringData, LMUScoringInfo, LMUTelemetryData (+17 more)

### Community 1 - "Threshold Detectors"
Cohesion: 0.13
Nodes (5): Detector, HardBrakingDetector, OffTrackDetector, SpinDetector, ThrottleLift

### Community 2 - "Telemetry Sources & Recorder"
Cohesion: 0.16
Nodes (9): CarState, CornerEntryDetection, Event, LiveSource, Recorder, ReplaySource, LMUConstants, LMUObjectOut (+1 more)

### Community 3 - "Mmap Control Layer"
Cohesion: 0.16
Nodes (8): MMapControl, Close memory mapping          Create a final accessible mmap data copy before, Share buffer access, may result data desync, Copy buffer access, helps avoid data desync, Initialize memory map setting          Args:             mmap_name: mmap file, Create mmap instance & initial accessible copy          Args:             acc, test_api(), Structure

### Community 4 - "Coaching & Voice Output"
Cohesion: 0.36
Nodes (6): Event, phrase_event(), radio_check(), worker_function(), speak(), _tts_to_memory()

### Community 5 - "Cross-Platform Mmap"
Cohesion: 0.36
Nodes (7): mmap, linux_mmap(), platform_mmap(), LMU Memory Map Control  Inherit Python mapping of LMU Shared Memory Interface, Platform memory mapping, Linux mmap - read data from '/dev/shm/filename' if available, windows_mmap()

### Community 6 - "SimInfo Lifecycle"
Cohesion: 0.33
Nodes (3): Simulation info from shared memory, Save buffer data to file, SimInfo

### Community 7 - "Lock-Up / Slip Detection"
Cohesion: 0.33
Nodes (3): LockUpDetector, How much a wheel is slipping against the road.         0.0  -> rolling perfect, slip_ratio()

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MMapControl` connect `Mmap Control Layer` to `Threshold Detectors`, `Telemetry Sources & Recorder`, `Coaching & Voice Output`, `Cross-Platform Mmap`, `Lock-Up / Slip Detection`?**
  _High betweenness centrality (0.236) - this node is a cross-community bridge._
- **Why does `LMUObjectOut` connect `Telemetry Sources & Recorder` to `LMU Data Structs`, `Threshold Detectors`, `Coaching & Voice Output`, `SimInfo Lifecycle`, `Lock-Up / Slip Detection`?**
  _High betweenness centrality (0.133) - this node is a cross-community bridge._
- **Why does `LMUConstants` connect `Telemetry Sources & Recorder` to `LMU Data Structs`, `Threshold Detectors`, `Mmap Control Layer`, `Coaching & Voice Output`, `Cross-Platform Mmap`, `Lock-Up / Slip Detection`?**
  _High betweenness centrality (0.128) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `MMapControl` (e.g. with `CarState` and `CornerEntryDetection`) actually correct?**
  _`MMapControl` has 13 INFERRED edges - model-reasoned connections that need verification._
- **Are the 13 inferred relationships involving `LMUConstants` (e.g. with `CarState` and `CornerEntryDetection`) actually correct?**
  _`LMUConstants` has 13 INFERRED edges - model-reasoned connections that need verification._
- **Are the 13 inferred relationships involving `LMUObjectOut` (e.g. with `CarState` and `CornerEntryDetection`) actually correct?**
  _`LMUObjectOut` has 13 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `Detector` (e.g. with `LMUConstants` and `LMUObjectOut`) actually correct?**
  _`Detector` has 3 INFERRED edges - model-reasoned connections that need verification._