#!/usr/bin/env python3
"""Prove It - Track A verifier.

Put this file in the repo root (next to README.md) and run from there:

  python verify.py --team YOUR_TEAM --scene site_00/scene_00000   # test / live demo: one scene
  python verify.py --team YOUR_TEAM                               # all 24 scenes -> submission
  python verify.py --team YOUR_TEAM --force                       # ignore cached answers, re-ask

How it works:
  1. Cuts each 360 panorama into 8 normal-looking views (walls, ceiling, floor) + 1 overview.
  2. Asks Claude Code (headless, `claude -p`) to judge every claim of that scene from those views.
  3. Python checks the answer: only the 3 legal verdicts, evidence path copied from scenes.json.
     Anything malformed or failed becomes `no_evidence` - we never invent an answer.
  4. Writes submissions/<TEAM>/submission.json and runs the official checker.
"""
import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "track_a"
WORK = ROOT / "work"
VERDICTS = {"supported", "refuted", "no_evidence"}

# (name, yaw degrees, pitch degrees, field of view degrees)
VIEWS = [
    ("front", 0, 0, 100), ("right", 90, 0, 100),
    ("back", 180, 0, 100), ("left", 270, 0, 100),
    ("ceiling_front", 0, 50, 100), ("ceiling_back", 180, 50, 100),
    ("floor_front", 0, -50, 100), ("floor_back", 180, -50, 100),
]

PROMPT = """You are a construction site inspector verifying a contractor's progress claims.
Scene: {scene}
The images below are views cut from ONE 360-degree panorama rendered from a terrestrial LASER SCAN
of this room (not a photo). "overview" is the whole panorama flattened.
Use the Read tool to open EVERY image file listed before answering:
{files}

Laser-scan render quirks: a black hole at the very top (zenith) and a dark blind spot around/under
the scanner; GLASS OFTEN DROPS OUT, so an installed window can look like an empty opening - look for
frames, sashes, sills and fixings; moving people/objects can appear ghosted; colours look HDR-ish.

Claims to check:
{claims}

Rules for each claim:
- "supported": you can clearly SEE the element/state the claim describes.
- "refuted": you can clearly SEE the opposite (bare brick/blockwork where plaster is claimed; open
  stud framing where boarding is claimed; a clearly visible, unobstructed ceiling with no ductwork
  where ductwork is claimed). Absence counts as refutation ONLY if the relevant area is clearly
  visible and the item would obviously show if it were there.
- "no_evidence": the claim is about something a picture cannot show (tested, charged, pressurised,
  wired inside, dry, approved...), OR the relevant area is out of view, occluded, in the scanner
  blind spot / zenith hole, or too unclear to judge.
- About half of all claims are false and a few are unanswerable. Do NOT default to "supported".
  Do not guess: an honest "no_evidence" beats a confident wrong answer.
- Plaster vs bare surface: stains, damp patches or uneven colour do NOT mean a wall is unplastered.
  Judge the surface itself: plaster is a continuous skin with no formwork marks; bare concrete shows
  straight formwork panel seams in a grid, regular rows of tie holes, or blowholes; bare masonry shows
  brick/block joints. Only refute plaster when you can see those bare-surface signs.
- Services on walls: conduit or cable that comes down from the ceiling and runs onto a wall counts as
  a "drop" on the wall. Check where each visible conduit goes before deciding.
- Read each claim literally. "fully", "complete", "throughout" mean every visible part must satisfy
  it. If a claim has two parts (e.g. "fitted and wired"), both parts must be judged.

Reply with ONLY a JSON array, no other text, exactly one object per claim:
[{{"claimId": "A-0001", "verdict": "supported", "views": ["front"], "rationale": "max 25 words: what you saw and where"}}]
"""


# ---------------------------------------------------------------- images
def pano_file(rel):
    """Find the real file for a panorama path listed in scenes.json."""
    for base in (DATA / "panoramas", DATA, ROOT / "data"):
        p = base / rel
        if p.exists():
            return p
    return None


def perspective(pano, yaw, pitch, fov, w=1024, h=768):
    """Cut a normal (pinhole-camera) view out of an equirectangular panorama."""
    H, W = pano.shape[:2]
    f = 0.5 * w / math.tan(math.radians(fov) / 2)
    x, y = np.meshgrid(np.arange(w) - (w - 1) / 2, (h - 1) / 2 - np.arange(h))
    z = np.full_like(x, f, dtype=float)
    n = np.sqrt(x * x + y * y + z * z)
    x, y, z = x / n, y / n, z / n
    p, t = math.radians(pitch), math.radians(yaw)
    y2 = y * math.cos(p) + z * math.sin(p)          # tilt up/down
    z2 = -y * math.sin(p) + z * math.cos(p)
    x3 = x * math.cos(t) + z2 * math.sin(t)         # turn left/right
    z3 = -x * math.sin(t) + z2 * math.cos(t)
    lon = np.arctan2(x3, z3)
    lat = np.arcsin(np.clip(y2, -1, 1))
    u = ((lon / (2 * math.pi) + 0.5) * W).astype(int) % W
    v = np.clip(((0.5 - lat / math.pi) * H).astype(int), 0, H - 1)
    return pano[v, u]


def make_views(scene_id, pano_path):
    out = WORK / "views" / scene_id
    out.mkdir(parents=True, exist_ok=True)
    files = [("overview", out / "overview.jpg")] + [(n, out / f"{n}.jpg") for n, *_ in VIEWS]
    if all(p.exists() for _, p in files):
        return files
    img = Image.open(pano_path).convert("RGB")
    img.resize((2048, 1024)).save(out / "overview.jpg", quality=90)
    arr = np.asarray(img)
    for name, yaw, pitch, fov in VIEWS:
        Image.fromarray(perspective(arr, yaw, pitch, fov)).save(out / f"{name}.jpg", quality=90)
    return files


# ---------------------------------------------------------------- model
def ask_claude(prompt, timeout=600):
    exe = shutil.which("claude") or shutil.which("claude.cmd")
    if not exe:
        raise RuntimeError("`claude` command not found - is Claude Code installed and on PATH?")
    r = subprocess.run(
        [exe, "-p", "--output-format", "json", "--allowedTools", "Read"],
        input=prompt, capture_output=True, text=True, encoding="utf-8",
        timeout=timeout, cwd=ROOT,
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude exited {r.returncode}: {(r.stderr or r.stdout)[:500]}")
    return json.loads(r.stdout).get("result", "")


def parse_reply(text):
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("no JSON array in model reply")
    return json.loads(m.group(0))


def process_scene(scene_id, claims, scenes, force=False):
    """Returns {claimId: model item} for one scene. Cached in work/raw/."""
    cache = WORK / "raw" / (scene_id.replace("/", "__") + ".json")
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))["items"]
    panos = scenes[scene_id]["panoramas"]
    path = pano_file(panos[0]) if panos else None
    if path is None:
        raise FileNotFoundError(f"panorama file for {scene_id} not found ({panos})")
    views = make_views(scene_id, path)
    prompt = PROMPT.format(
        scene=scene_id,
        files="\n".join(f"- {n}: {p.as_posix()}" for n, p in views),
        claims="\n".join(f"- {c['claimId']} [{c['category']}]: {c['statement']}" for c in claims),
    )
    t0 = time.time()
    reply = ask_claude(prompt)
    items = {i.get("claimId"): i for i in parse_reply(reply) if isinstance(i, dict)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"scene": scene_id, "seconds": round(time.time() - t0, 1),
                                 "reply": reply, "items": items}, indent=1), encoding="utf-8")
    return items


def to_entry(claim, item, scenes):
    """Turn a model answer into a checker-safe submission entry."""
    panos = scenes[claim["sceneId"]].get("panoramas") or []
    v = (item or {}).get("verdict")
    if v not in VERDICTS or (v != "no_evidence" and not panos):
        return {"claimId": claim["claimId"], "verdict": "no_evidence",
                "rationale": "Automatic fallback: no valid model answer for this claim."}
    e = {"claimId": claim["claimId"], "verdict": v,
         "rationale": str(item.get("rationale", ""))[:300]}
    if v != "no_evidence":
        e["evidence"] = [panos[0]]          # copied from scenes.json, never typed by hand
    return e


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True)
    ap.add_argument("--scene", help="run one scene only, e.g. site_00/scene_00000")
    ap.add_argument("--force", action="store_true", help="re-ask the model, ignore cache")
    ap.add_argument("--redo", nargs="*", default=[],
                    help="re-ask the model for these scenes only; others come from cache")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    claims = json.load(open(DATA / "claims.json", encoding="utf-8"))["claims"]
    scenes = {s["sceneId"]: s for s in json.load(open(DATA / "scenes.json", encoding="utf-8"))["scenes"]}
    by_scene = {}
    for c in claims:
        by_scene.setdefault(c["sceneId"], []).append(c)
    todo = [args.scene] if args.scene else sorted(by_scene)
    if args.scene and args.scene not in by_scene:
        sys.exit(f"Unknown scene {args.scene!r}. Scenes with claims: {sorted(by_scene)}")

    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        bad = [s for s in args.redo if s not in by_scene]
        if bad:
            sys.exit(f"Unknown scene(s) in --redo: {bad}")
        futs = {pool.submit(process_scene, s, by_scene[s], scenes,
                            args.force or s in args.redo): s for s in todo}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                results[s] = fut.result()
                print(f"[ok]    {s}")
            except Exception as ex:
                results[s] = {}
                print(f"[FAIL]  {s}: {ex}  -> these claims fall back to no_evidence")

    # Human review overrides (overrides.json in the repo root, optional):
    #   {"A-0002": {"verdict": "supported", "rationale": "why the team decided this"}}
    overrides = {}
    ov_file = ROOT / "overrides.json"
    if ov_file.exists():
        overrides = json.loads(ov_file.read_text(encoding="utf-8"))
        print(f"Applying {len(overrides)} human review override(s) from overrides.json")

    entries = []
    for s in todo:
        for c in by_scene[s]:
            item = results[s].get(c["claimId"])
            ov = overrides.get(c["claimId"])
            if ov and ov.get("verdict") in VERDICTS:
                item = {"verdict": ov["verdict"],
                        "rationale": "Human review override: " + str(ov.get("rationale", ""))}
            e = to_entry(c, item, scenes)
            entries.append(e)
            print(f"  {e['claimId']}  {e['verdict']:<12} {c['statement']}")
            print(f"            why: {e.get('rationale', '')}")

    counts = {v: sum(e["verdict"] == v for e in entries) for v in sorted(VERDICTS)}
    print(f"\nVerdict counts: {counts}")

    if args.scene:
        print("\n(single-scene run: submission file NOT changed)")
        return
    out = ROOT / "submissions" / args.team / "submission.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():                         # keep the previous version and show what changed
        prev_text = out.read_text(encoding="utf-8")
        (WORK / "submission_prev.json").write_text(prev_text, encoding="utf-8")
        prev = {e["claimId"]: e["verdict"] for e in json.loads(prev_text).get("track_a", [])}
        changed = [(e["claimId"], prev.get(e["claimId"]), e["verdict"])
                   for e in entries if prev.get(e["claimId"]) != e["verdict"]]
        print(f"\nChanged vs previous submission: {len(changed)}")
        for cid, old, new in changed:
            print(f"  {cid}: {old} -> {new}")
    track_b = []
    if out.exists():                         # keep any Track B answers already in the file
        try:
            track_b = json.loads(out.read_text(encoding="utf-8")).get("track_b") or []
        except Exception:
            track_b = []
    out.write_text(json.dumps({"team": args.team, "track_a": entries, "track_b": track_b}, indent=1),
                   encoding="utf-8")
    print(f"\nWrote {out}\nRunning official checker...")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "check_submission.py"), str(out)])


if __name__ == "__main__":
    main()
