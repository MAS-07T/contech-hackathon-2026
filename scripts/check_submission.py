#!/usr/bin/env python3
"""Validate an evidence-log submission against the published task files.

Usage: python3 scripts/check_submission.py my_submission.json [--data data/]
Structure only — it cannot tell you whether your verdicts are right.
"""
import argparse
import json
import sys
from pathlib import Path

VERDICTS = {"supported", "refuted", "no_evidence"}
CATEGORIES = {"facade", "partitions", "mep_services", "finishes",
              "materials_logistics", "structure"}
DIRECTIONS = {"progressed", "added", "removed", "no_change"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("submission")
    ap.add_argument("--data", default=None)
    args = ap.parse_args()
    data = Path(args.data) if args.data else \
        Path(__file__).resolve().parent.parent / "data"
    errors = []
    E = errors.append

    try:
        sub = json.load(open(args.submission))
    except Exception as e:
        sys.exit(f"FAIL: cannot parse {args.submission}: {e}")
    if not isinstance(sub.get("team"), str) or not sub["team"].strip():
        E("missing team name")

    # ---- track A
    claims = {c["claimId"]: c for c in
              json.load(open(data / "track_a" / "claims.json"))["claims"]}
    scenes = {s["sceneId"]: s for s in
              json.load(open(data / "track_a" / "scenes.json"))["scenes"]}
    seen = set()
    for i, a in enumerate(sub.get("track_a") or []):
        w = f"track_a[{i}]"
        c = claims.get(a.get("claimId"))
        if c is None:
            E(f"{w}: unknown claimId {a.get('claimId')!r}")
            continue
        if a["claimId"] in seen:
            E(f"{w}: duplicate claimId")
        seen.add(a["claimId"])
        v = a.get("verdict")
        if v not in VERDICTS:
            E(f"{w}: verdict must be one of {sorted(VERDICTS)}")
        if v in ("supported", "refuted"):
            ev = a.get("evidence") or []
            if not ev:
                E(f"{w}: verdict {v!r} needs cited panorama(s)")
            panos = set(scenes.get(c["sceneId"], {}).get("panoramas") or [])
            for p in ev:
                if p not in panos:
                    E(f"{w}: {p!r} is not a panorama of {c['sceneId']}")
    missing = set(claims) - seen
    if missing:
        E(f"track_a: no verdict for {len(missing)} claims "
          f"(e.g. {sorted(missing)[:3]})")

    # ---- track B
    tasks = {t["taskId"]: t for t in
             json.load(open(data / "track_b" / "tasks.json"))["tasks"]}
    seen = set()
    for i, b in enumerate(sub.get("track_b") or []):
        w = f"track_b[{i}]"
        t = tasks.get(b.get("taskId"))
        if t is None:
            E(f"{w}: unknown taskId {b.get('taskId')!r}")
            continue
        if b["taskId"] in seen:
            E(f"{w}: duplicate taskId")
        seen.add(b["taskId"])
        before = set(t["frames"]["before"])
        after = set(t["frames"]["after"])
        cats = set()
        for j, ch in enumerate(b.get("changes") or []):
            w2 = f"{w}.changes[{j}]"
            if ch.get("category") not in CATEGORIES:
                E(f"{w2}: category must be one of {sorted(CATEGORIES)}")
            if ch.get("direction") not in DIRECTIONS:
                E(f"{w2}: direction must be one of {sorted(DIRECTIONS)}")
            if ch.get("category") in cats:
                E(f"{w2}: duplicate category for this task")
            cats.add(ch.get("category"))
            if ch.get("direction") != "no_change":
                ev = ch.get("evidence") or {}
                bf, af = ev.get("before") or [], ev.get("after") or []
                if not bf or not af:
                    E(f"{w2}: needs evidence frames from BOTH visits")
                for p in bf:
                    if p not in before:
                        E(f"{w2}: {p!r} not in this task's before-frames")
                for p in af:
                    if p not in after:
                        E(f"{w2}: {p!r} not in this task's after-frames")
    missing = set(tasks) - seen
    if missing:
        E(f"track_b: no answer for tasks {sorted(missing)[:5]}")

    if errors:
        print(f"FAIL — {len(errors)} problem(s):")
        for e in errors[:50]:
            print(" -", e)
        sys.exit(1)
    print(f"OK — team {sub['team']!r}: {len(claims)} claims, "
          f"{len(tasks)} zone tasks answered. Ready to submit.")


if __name__ == "__main__":
    main()
