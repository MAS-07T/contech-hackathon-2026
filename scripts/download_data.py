#!/usr/bin/env python3
"""Download and unpack the image packs for both tracks.

Packs live in a Google Drive folder shared with registered teams (link comes
with your acceptance email). Do this BEFORE event day — venue wifi will not
save you.

Usage: python3 scripts/download_data.py --dest data/
If automated download fails, download the packs in your browser from the
folder link, drop them next to this script, and rerun.
"""
import argparse
import subprocess
import sys
import zipfile
from pathlib import Path

FOLDER_URL = "https://drive.google.com/drive/folders/1T3W9n0vRo1DmCBntxJqQdoW1USr1lHuy"
PACKS = {
    # name: (drive_file_id, expected_bytes)
    "track_a_panoramas.zip": ("1nJ0pQ_wEpZv2dES9bjdulMl_foirVqyR", 309880401),
    "track_b_frames.zip": ("14HlJhqf6PMJStdn4gHo7FEzHW4pfMXv_", 1987560419),
}
ALLOWED_PREFIXES = ("track_a/", "track_b/")


def gdown_fetch(file_id, out):
    try:
        subprocess.run([sys.executable, "-m", "gdown", "--id", file_id,
                        "-O", str(out)], check=True)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="data")
    args = ap.parse_args()
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve().parent

    ok = True
    for name, (file_id, want_size) in PACKS.items():
        local = next((p for p in (here / name, Path(name), dest / name)
                      if p.exists()), None)
        if local is None:
            print(f"fetching {name} ...")
            target = dest / name
            if "REPLACE" in file_id or not gdown_fetch(file_id, target):
                print(f"  could not fetch {name} automatically.")
                print(f"  Get it in your browser: {FOLDER_URL}")
                ok = False
                continue
            local = target
        got = local.stat().st_size
        if got != want_size:
            print(f"  {name} is incomplete ({got} of {want_size} bytes) — "
                  f"delete it and re-run, or download it in your browser: "
                  f"{FOLDER_URL}")
            ok = False
            continue
        print(f"unpacking {local} ...")
        with zipfile.ZipFile(local) as z:
            bad = [n for n in z.namelist()
                   if not n.startswith(ALLOWED_PREFIXES)]
            if bad:
                print(f"  unexpected entries, refusing: {bad[:5]}")
                ok = False
                continue
            z.extractall(dest)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
