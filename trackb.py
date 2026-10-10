#!/usr/bin/env python3
"""Prove It - Track B (bonus): what changed between two visits?

  python trackb.py --team TEAM2

For each task in data/track_b/tasks.json:
  1. Picks 5 evenly spaced frames from each visit's frame list.
  2. Brightens them (the camera is dark) into work/track_b/<task>/.
  3. Asks Claude Code which change categories clearly changed.
  4. Maps Claude's picks (B1..B5 / A1..A5) back to the real frame names from the
     task list, so every cited frame is valid. Unclear categories are left out
     (= no_change), because false alarms cost points.
  5. Writes track_b into submissions/<TEAM>/submission.json and runs the checker.
     If the checker fails, the previous submission is restored.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image

import verify as V

ROOT = V.ROOT
DATA_B = ROOT / "data" / "track_b"
WORK_B = V.WORK / "track_b"
CATEGORIES = ["facade", "partitions", "mep_services", "finishes", "materials_logistics", "structure"]
DIRECTIONS = {"progressed", "added", "removed", "no_change"}
N = 5

PROMPT = """You are a construction progress inspector comparing two site visits of the SAME zone of a
London residential building project.
BEFORE visit ({fv}) frames:
{before}
AFTER visit ({tv}) frames:
{after}
Use the Read tool to open EVERY image file listed before answering. Frames inside one visit look in
different directions; the same place is not always seen from the same angle in both visits.

Report what construction work happened between the visits, using ONLY these categories:
- facade: external walls, windows, glazing, cladding, weatherproofing
- partitions: internal walls, studs, blockwork, drywall boarding
- mep_services: pipes, ducts, cable trays, conduits, electrical/plumbing/ventilation
- finishes: plaster, paint, screed, floor coverings, ceilings, tiling
- materials_logistics: stored materials, pallets, skips, scaffolding, temporary works
- structure: slabs, columns, beams, frames, stairs
Directions: "progressed" (existing work advanced), "added" (new element appeared),
"removed" (element gone), "no_change".

Rules:
- Only report a category when the change is CLEAR in the images. Differences caused by camera
  angle, lighting or a different viewpoint are NOT changes. If unsure, leave the category out.
- Every reported change needs at least one BEFORE label and one AFTER label that show it.

Reply with ONLY a JSON array, no other text, for example:
[{{"category": "partitions", "direction": "progressed", "note": "max 20 words", "before": ["B2"], "after": ["A3"]}}]
Reply [] if nothing clearly changed.
"""


def frame_file(rel):
    for base in (DATA_B / "frames", DATA_B, ROOT / "data"):
        p = base / rel
        if p.exists():
            return p
    return None


def pick(frames, n=N):
    if len(frames) <= n:
        return list(frames)
    idx = np.linspace(0, len(frames) - 1, n).round().astype(int)
    return [frames[i] for i in idx]


def brighten(src, dst):
    """Simple exposure normalisation: stretch each channel between its 1st and 99th percentile."""
    a = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
    lo = np.percentile(a, 1, axis=(0, 1))
    hi = np.percentile(a, 99, axis=(0, 1))
    a = np.clip((a - lo) / np.maximum(hi - lo, 1) * 255, 0, 255).astype(np.uint8)
    Image.fromarray(a).resize((1032, 772)).save(dst, quality=88)


def run_task(t, force=False):
    tid = t["taskId"]
    out = WORK_B / re.sub(r"[^A-Za-z0-9_.-]", "_", tid)
    cache = out / "answer.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    labels = {}
    lines = {"before": [], "after": []}
    for side, letter in (("before", "B"), ("after", "A")):
        for i, rel in enumerate(pick(t["frames"][side]), 1):
            src = frame_file(rel)
            if src is None:
                continue
            lab = f"{letter}{i}"
            dst = out / f"{lab}.jpg"
            if not dst.exists():
                brighten(src, dst)
            labels[lab] = (side, rel)
            lines[side].append(f"- {lab}: {dst.as_posix()}")
    if not lines["before"] or not lines["after"]:
        raise FileNotFoundError(f"{tid}: frame files not found under data/track_b/frames")
    prompt = PROMPT.format(fv=t.get("fromVisit", "earlier"), tv=t.get("toVisit", "later"),
                           before="\n".join(lines["before"]), after="\n".join(lines["after"]))
    t0 = time.time()
    reply = V.ask_claude(prompt)
    items = V.parse_reply(reply)
    changes, seen = [], set()
    for it in items:
        if not isinstance(it, dict):
            continue
        cat, d = it.get("category"), it.get("direction")
        if cat not in CATEGORIES or d not in DIRECTIONS or d == "no_change" or cat in seen:
            continue
        bf = [labels[l][1] for l in it.get("before") or [] if l in labels and labels[l][0] == "before"]
        af = [labels[l][1] for l in it.get("after") or [] if l in labels and labels[l][0] == "after"]
        if not bf or not af:
            continue                                   # no proof from both visits -> drop it
        seen.add(cat)
        changes.append({"category": cat, "direction": d, "note": str(it.get("note", ""))[:200],
                        "evidence": {"before": bf, "after": af}})
    rec = {"taskId": tid, "changes": changes, "seconds": round(time.time() - t0, 1), "reply": reply}
    cache.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    tasks = json.loads((DATA_B / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    print(f"{len(tasks)} Track B tasks")
    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(run_task, t, args.force): t["taskId"] for t in tasks}
        for f in as_completed(futs):
            tid = futs[f]
            try:
                results[tid] = f.result()
                print(f"[ok]   {tid}: " + (", ".join(f"{c['category']}={c['direction']}" for c in results[tid]["changes"]) or "no clear changes"))
            except Exception as ex:
                print(f"[FAIL] {tid}: {ex}  -> left out (allowed for the bonus track)")

    track_b = [{"taskId": t["taskId"], "changes": results[t["taskId"]]["changes"]}
               for t in tasks if t["taskId"] in results]

    sub_path = ROOT / "submissions" / args.team / "submission.json"
    if not sub_path.exists():
        sys.exit("No Track A submission found - run verify.py first.")
    backup = V.WORK / "submission_before_trackb.json"
    shutil.copyfile(sub_path, backup)
    sub = json.loads(sub_path.read_text(encoding="utf-8"))
    sub["track_b"] = track_b
    sub_path.write_text(json.dumps(sub, indent=1), encoding="utf-8")
    print(f"\nAdded {len(track_b)} Track B task(s). Running official checker...")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_submission.py"), str(sub_path)])
    if r.returncode != 0:
        shutil.copyfile(backup, sub_path)
        print("Checker FAILED with Track B -> previous submission restored (Track A unchanged).")


if __name__ == "__main__":
    main()
