# Data guide

Two real datasets, one per track. All imagery arrives via
`scripts/download_data.py`; this repo carries the task definitions and
metadata.

## Layout

```
data/
  track_a/
    claims.json               the claim sheets you must verdict (24 of the 48 scenes)
    scenes.json               scene index: site, scene id, panorama files
    panoramas/<site>/<scene>/color.png     (from image packs, 4096x2048)
  track_b/
    tasks.json                zone × visit-pair tasks you must answer
    zones.json                zone centers and frame manifests
    frames/<sequence>/<name>.png           (from image packs, 2064x1544)
    poses/<sequence>/<ts>.txt              4x4 pose per lidar scan
```

## Track A — Rohbau3D scenes

- 360° **equirectangular panoramas** rendered from colorized terrestrial laser
  scans (not photos — expect a scanner shadow, a black zenith hole, and
  HDR-ish color; construction state is clearly legible).
- 8 sites spanning bare reinforced-concrete shell, brick apartment blocks,
  school buildings, a fitted-out office and renovation work.
- `claims.json` entries: `claimId`, `sceneId`, `statement`, `category`.
  Statements are about **completion state** (plastered / installed / staged /
  poured), authored for this event. Roughly half are false, and a few are
  deliberately unanswerable from the panorama — honesty is scored.
- Multiple scenes per site; a scene = one scan position = one panorama.

## Track B — ConSLAM zones

- One London residential project, four walkthroughs: **2022-04-26, 06-09,
  06-29, 08-03** (sequences 2–5). Perspective frames, 2064×1544, machine-vision
  color (dark; normalize before feeding a model).
- **2 zones** selected by registered pose (all visits share one coordinate
  frame): a corridor zone with 60 frames per visit, and a second zone whose
  tasks compare only visits with matching view directions — one task pair is
  deliberately non-consecutive (Apr 26 → Jun 29).
- `tasks.json` entries: `taskId`, `zone`, `fromVisit`, `toVisit`, and the
  frame lists for both visits.
- `poses/` holds the 4×4 pose matrices (position in metres in the shared
  frame); frame filenames are microsecond timestamps — match frames to poses
  by nearest timestamp.
- Change vocabulary for answers: `facade`, `partitions`, `mep_services`,
  `finishes`, `materials_logistics`, `structure`; directions `progressed`,
  `added`, `removed`, `no_change`.

## Caveats

1. **Track A panoramas are renders of laser scans.** Moving objects can ghost;
   glass and shiny surfaces can drop out. If a claim can't be judged from the
   panorama, `no_evidence` is the honest — and scoreable — answer.
2. **Track B frames are dark.** The camera is a machine-vision unit without
   tone mapping. Simple exposure normalization goes a long way.
3. **A zone is a place, not a viewpoint.** Frames within a zone look in
   different directions; use poses and overlap to reason about what you see.
4. **Both datasets are public.** You can pull more Rohbau3D scenes or more
   ConSLAM frames if you want context — but the scored artifacts are the
   authored claim sheets and change keys, which are not public.
5. Rohbau3D's public release includes semantic labels. They label object
   categories, not completion states — they will not answer the claims for
   you, but you're welcome to use them as features.

## Citations

See README "Data credits". Cite both datasets in anything you publish about
your solution.
