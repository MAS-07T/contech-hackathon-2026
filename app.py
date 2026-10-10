#!/usr/bin/env python3
"""PROVE IT - local demo app for the Track A verifier.

  python app.py --team TEAM2
  then open http://127.0.0.1:8000 in your browser

Reuses verify.py (no second copy of the verification logic). Only listens on
this laptop (127.0.0.1). Shows the challenge images, so never host it publicly.

What it shows, all from real files:
  - claims.json / scenes.json            -> claim list, evidence paths
  - submissions/<TEAM>/submission.json   -> the submitted verdicts + reasons
  - work/raw/*.json                      -> the model answers behind them (views cited, run time)
  - work/views/*                         -> the views the model actually looked at
  - "Re-run live"                        -> a fresh Claude call, saved to work/live/, never
                                            changes the submission
  - "Run official checker"               -> runs scripts/check_submission.py and shows its output
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import verify as V

ROOT = V.ROOT
WORK = V.WORK
VIEW_NAMES = ["overview"] + [v[0] for v in V.VIEWS]
TEAM = "TEAM2"
LIVE_LOCK = threading.Lock()
LAST_CHECK = {"ran": False}


def jload(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cache_name(scene):
    return scene.replace("/", "__") + ".json"


def load_base():
    claims = jload(V.DATA / "claims.json")["claims"]
    scenes = {s["sceneId"]: s for s in jload(V.DATA / "scenes.json")["scenes"]}
    return claims, scenes


def state():
    claims, scenes = load_base()
    sub_path = ROOT / "submissions" / TEAM / "submission.json"
    sub = jload(sub_path) if sub_path.exists() else None
    answers = {e["claimId"]: e for e in (sub or {}).get("track_a", [])}
    ov_path = ROOT / "overrides.json"
    overrides = jload(ov_path) if ov_path.exists() else {}

    scene_ids = sorted({c["sceneId"] for c in claims})
    scene_info = {}
    for s in scene_ids:
        panos = scenes.get(s, {}).get("panoramas") or []
        raw_p = WORK / "raw" / cache_name(s)
        live_p = WORK / "live" / cache_name(s)
        raw = jload(raw_p) if raw_p.exists() else None
        live = jload(live_p) if live_p.exists() else None
        scene_info[s] = {
            "panoramas": panos,
            "panoFound": bool(panos) and V.pano_file(panos[0]) is not None,
            "views": [n for n in VIEW_NAMES if (WORK / "views" / s / f"{n}.jpg").exists()],
            "modelSeconds": raw.get("seconds") if raw else None,
            "modelItems": raw.get("items") if raw else {},
            "live": live,
        }

    out = []
    for c in claims:
        out.append({**c,
                    "submitted": answers.get(c["claimId"]),
                    "model": scene_info[c["sceneId"]]["modelItems"].get(c["claimId"]),
                    "override": c["claimId"] in overrides})
    counts = {}
    for e in answers.values():
        counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1
    return {"team": TEAM, "submissionExists": sub is not None,
            "submissionPath": f"submissions/{TEAM}/submission.json",
            "counts": counts, "answered": len(answers), "total": len(claims),
            "claims": out, "scenes": scene_info, "lastCheck": LAST_CHECK}


def run_checker():
    sub_path = ROOT / "submissions" / TEAM / "submission.json"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")   # Windows: make the checker print UTF-8
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_submission.py"), str(sub_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=ROOT, env=env)
    LAST_CHECK.update({"ran": True, "ok": r.returncode == 0,
                       "output": (r.stdout + r.stderr).strip(),
                       "at": time.strftime("%H:%M:%S")})
    return LAST_CHECK


def run_live(scene, claim_id=None):
    """Fresh model call for one scene. Uses verify.py's own functions; never touches the submission."""
    claims, scenes = load_base()
    sc = [c for c in claims if c["sceneId"] == scene and (claim_id is None or c["claimId"] == claim_id)]
    if not sc:
        raise ValueError("unknown claim for this scene")
    panos = scenes[scene].get("panoramas") or []
    path = V.pano_file(panos[0]) if panos else None
    if path is None:
        raise FileNotFoundError(f"panorama for {scene} not found")
    views = V.make_views(scene, path)
    prompt = V.PROMPT.format(
        scene=scene,
        files="\n".join(f"- {n}: {p.as_posix()}" for n, p in views),
        claims="\n".join(f"- {c['claimId']} [{c['category']}]: {c['statement']}" for c in sc))
    t0 = time.time()
    reply = V.ask_claude(prompt)
    items = {i.get("claimId"): i for i in V.parse_reply(reply) if isinstance(i, dict)}
    entries = {c["claimId"]: V.to_entry(c, items.get(c["claimId"]), scenes) for c in sc}
    now, secs = time.strftime("%H:%M:%S"), round(time.time() - t0, 1)
    for e in entries.values():
        e["at"], e["seconds"] = now, secs
    live_p = WORK / "live" / cache_name(scene)
    rec = jload(live_p) if live_p.exists() else {"scene": scene, "items": {}, "entries": {}}
    rec["items"].update(items)                    # merge: one-claim checks keep earlier results
    rec["entries"].update(entries)
    rec["at"], rec["seconds"] = now, secs
    (WORK / "live").mkdir(parents=True, exist_ok=True)
    live_p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


IMG_RE = re.compile(r"^/views/(site_\d+)/(scene_\d+)/([a-z_]+)\.jpg$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p == "/":
                return self.send(200, PAGE, "text/html; charset=utf-8")
            if p == "/api/state":
                return self.send(200, state())
            m = IMG_RE.match(p)
            if m and m.group(3) in VIEW_NAMES:
                f = WORK / "views" / m.group(1) / m.group(2) / f"{m.group(3)}.jpg"
                if f.is_file():
                    return self.send(200, f.read_bytes(), "image/jpeg")
            return self.send(404, {"error": "not found"})
        except Exception as ex:
            return self.send(500, {"error": str(ex)})

    def do_POST(self):
        p = self.path.split("?")[0]
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if p == "/api/check":
                return self.send(200, run_checker())
            if p == "/api/verify":
                scene = body.get("scene")
                claims, _ = load_base()
                if scene not in {c["sceneId"] for c in claims}:
                    return self.send(400, {"error": "unknown scene"})
                if not LIVE_LOCK.acquire(blocking=False):
                    return self.send(409, {"error": "a live run is already in progress"})
                try:
                    cid = body.get("claimId")
                    if cid is not None and not isinstance(cid, str):
                        return self.send(400, {"error": "bad claimId"})
                    return self.send(200, run_live(scene, cid))
                finally:
                    LIVE_LOCK.release()
            return self.send(404, {"error": "not found"})
        except Exception as ex:
            return self.send(500, {"error": str(ex)})


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PROVE IT</title>
<style>
:root{--bg:#f2f1ee;--panel:#fff;--ink:#16191d;--muted:#5b636d;--faint:#8a919a;--line:#e3e1dc;--line2:#d0cdc6;
--char:#1b1f24;--char2:#262b31;--orange:#ea5a0c;--orange-soft:#fff2ea;
--sup:#17804a;--sup-bg:#e4f4ea;--ref:#c0281c;--ref-bg:#fdeae8;--ne:#4b5563;--ne-bg:#eceef1;--fs:15px}
body.present{--fs:18px}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--ink);font:var(--fs)/1.5 "Segoe UI",system-ui,-apple-system,sans-serif}
button,select,input{font:inherit;color:inherit}
button{cursor:pointer}
:focus-visible{outline:3px solid var(--orange);outline-offset:2px}
.mono{font-family:ui-monospace,"Cascadia Code",Consolas,monospace}

/* header */
header{background:var(--char);color:#fff}
.hbar{display:flex;flex-wrap:wrap;align-items:center;gap:18px;padding:14px 24px}
.brand{display:flex;align-items:baseline;gap:12px}
.logo{font-weight:800;font-size:1.6em;letter-spacing:.06em}.logo span{color:var(--orange)}
.tagline{color:#aab2bb;font-size:.85em}
.status{display:flex;flex-wrap:wrap;gap:8px;margin-left:auto;align-items:center}
.st{display:flex;align-items:center;gap:7px;font-size:.78em;background:var(--char2);border:1px solid #3a4048;border-radius:999px;padding:5px 11px;color:#d7dce1}
.dot{width:8px;height:8px;border-radius:50%;background:#7b838c}
.dot.ok{background:#3ccf7c}.dot.bad{background:#ff5a4f}
.st button{background:none;border:1px solid #59606a;border-radius:999px;color:#fff;padding:0 9px;font-size:.95em}
.st button:hover{border-color:var(--orange)}
.tgl{background:none;border:1px solid #59606a;border-radius:8px;color:#fff;padding:5px 10px;font-size:.78em}

/* how it works */
.how{display:none;grid-template-columns:repeat(4,1fr);gap:1px;background:#30353c;border-top:1px solid #30353c}
.how div{background:var(--char2);padding:12px 20px;font-size:.8em;color:#c3c9cf}
.how b{display:block;color:#fff;font-size:1.05em;margin-bottom:2px}
.how i{font-style:normal;color:var(--orange);font-weight:800;margin-right:6px}
body.howopen .how{display:grid}
@media(max-width:900px){.how{grid-template-columns:1fr 1fr}}
.tgl.on{border-color:var(--orange);color:#ffb183}

/* layout */
.layout{display:grid;grid-template-columns:300px 1fr;height:calc(100vh - var(--hh,130px))}
@media(max-width:960px){.layout{grid-template-columns:1fr;height:auto}}
aside{background:#22262c;color:#e6e9ec;overflow:auto;border-right:1px solid #111}
.search{position:sticky;top:0;background:#22262c;padding:14px;border-bottom:1px solid #343a42;z-index:1}
.search input{width:100%;padding:9px 12px;border:1px solid #444b54;border-radius:10px;background:#2c3138;color:#fff}
.search input::placeholder{color:#8d959e}
.navbtn{display:flex;align-items:center;gap:10px;width:100%;border:0;background:none;color:#e6e9ec;text-align:left;padding:12px 16px;font-weight:600;border-bottom:1px solid #343a42}
.navbtn:hover{background:#2c3138}.navbtn.sel{background:#33302c;box-shadow:inset 4px 0 var(--orange)}
.site{border-bottom:1px solid #343a42}
.site-h{display:flex;align-items:center;gap:8px;padding:12px 16px 6px;background:#1b1f24;position:sticky;top:61px;z-index:0}
.site-h b{font-size:.78em;letter-spacing:.12em;text-transform:uppercase;color:var(--orange)}
.site-h span{font-size:.72em;color:#8d959e;margin-left:auto}
.scene{display:flex;width:100%;align-items:center;gap:10px;border:0;background:none;color:#e6e9ec;text-align:left;padding:9px 16px 9px 26px}
.scene:hover{background:#2c3138}
.scene.sel{background:#3a2a20;box-shadow:inset 4px 0 var(--orange)}
.scene .nm{flex:1;font-size:.92em}.scene .nm small{display:block;color:#8d959e;font-size:.75em}
.t-none{background:#555}
.tally{display:flex;gap:3px}
.tally i{width:9px;height:9px;border-radius:2px;display:block}
.t-supported{background:var(--sup)}.t-refuted{background:var(--ref)}.t-no_evidence{background:#9aa3ad}.t-none{background:#ddd}

main{overflow:auto;padding:24px 28px 60px}
.wrap{max-width:1180px;margin:0 auto}

/* scene report */
.report-h{display:flex;flex-wrap:wrap;align-items:flex-end;gap:16px;margin-bottom:16px}
.report-h h1{margin:0;font-size:1.7em;letter-spacing:-.01em}
.report-h .q{color:var(--muted);font-size:.9em}
.report-h .act{margin-left:auto;display:flex;gap:8px;align-items:center}
.btn{border:1px solid var(--line2);background:#fff;border-radius:10px;padding:8px 14px;font-weight:600}
.btn.primary{background:var(--orange);border-color:var(--orange);color:#fff}
.btn:disabled{opacity:.6;cursor:wait}
.spin{display:inline-block;width:12px;height:12px;border:2px solid #fff;border-right-color:transparent;border-radius:50%;animation:s 1s linear infinite;vertical-align:-1px;margin-right:6px}
@keyframes s{to{transform:rotate(360deg)}}

.summary{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:18px}
.sum{border-radius:14px;padding:14px 16px;border:1px solid var(--line);background:var(--panel)}
.sum b{font-size:2em;line-height:1;display:block}
.sum span{font-size:.82em;color:var(--muted)}
.sum.supported b{color:var(--sup)}.sum.refuted b{color:var(--ref)}.sum.no_evidence b{color:var(--ne)}
.sum small{display:block;color:var(--faint);font-size:.72em;margin-top:4px}

.grid2{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:18px;align-items:start}
@media(max-width:1200px){.grid2{grid-template-columns:1fr}}

.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.card-h{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--line);font-size:.75em;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.card-h .r{margin-left:auto;text-transform:none;letter-spacing:0;font-weight:500}

/* viewer */
.viewer{position:relative;background:#0d0f12;overflow:auto;max-height:58vh}
.viewer img{display:block;width:100%;cursor:zoom-in;transition:width .15s}
.viewer.zoom img{width:250%;cursor:zoom-out}
.vtag{position:absolute;left:12px;top:12px;background:rgba(0,0,0,.65);color:#fff;font-size:.75em;padding:4px 9px;border-radius:999px;pointer-events:none}
.vtag.cite{background:var(--orange)}
.thumbs{display:grid;grid-template-columns:repeat(9,1fr);gap:6px;padding:10px;background:#f7f6f3;border-top:1px solid var(--line)}
@media(max-width:700px){.thumbs{grid-template-columns:repeat(5,1fr)}}
.th{border:2px solid transparent;border-radius:8px;padding:0;background:none;position:relative;overflow:hidden}
.th img{display:block;width:100%;aspect-ratio:4/3;object-fit:cover}
.th.ov img{object-fit:cover}
.th.on{border-color:var(--char)}
.th.cited::after{content:"";position:absolute;right:4px;top:4px;width:10px;height:10px;border-radius:50%;background:var(--orange);box-shadow:0 0 0 2px #fff}
.th span{display:block;font-size:.62em;color:var(--muted);padding:2px 0;text-align:center}
.vnote{padding:10px 16px;font-size:.78em;color:var(--muted);border-top:1px solid var(--line)}

/* claims */
.legend{display:flex;flex-wrap:wrap;gap:14px;padding:10px 16px;font-size:.75em;color:var(--muted);border-bottom:1px solid var(--line);background:#fbfaf8}
.legend b{font-family:ui-monospace,Consolas,monospace}
.claim{border-bottom:1px solid var(--line);padding:14px 16px;cursor:pointer;transition:background .1s}
.claim:last-child{border-bottom:0}
.claim:hover{background:#faf9f7}
.claim.sel{background:var(--orange-soft);box-shadow:inset 4px 0 var(--orange)}
.c-top{display:flex;align-items:center;gap:8px;font-size:.75em;color:var(--faint)}
.badge{font:700 .8em ui-monospace,Consolas,monospace;padding:3px 8px;border-radius:6px;letter-spacing:.02em}
.badge.supported{background:var(--sup-bg);color:var(--sup)}
.badge.refuted{background:var(--ref-bg);color:var(--ref)}
.badge.no_evidence{background:var(--ne-bg);color:var(--ne)}
.badge.none{background:#eee;color:#999}
.c-st{font-weight:600;font-size:1.02em;margin:6px 0}
.c-why{font-size:.88em;color:#30363d}
.c-src{font-size:.68em;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);margin-top:8px}
.c-src.human{color:var(--orange)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;align-items:center}
.vchip{border:1px solid var(--line2);background:#fff;border-radius:999px;padding:1px 9px;font-size:.72em}
.vchip:hover{border-color:var(--orange);color:var(--orange)}
.ev{font-size:.72em;color:var(--muted)}
.ev code{background:#f1efeb;padding:1px 6px;border-radius:5px}
.copy{border:0;background:none;color:var(--orange);font-size:1em;padding:0 4px}
.live{margin-top:8px;font-size:.78em;padding:6px 9px;border-radius:8px;background:#f5f4f1}
.live .m{color:var(--sup);font-weight:700}.live .d{color:var(--ref);font-weight:700}
details{margin-top:8px;font-size:.78em}
details summary{color:var(--muted);cursor:pointer}
details table{border-collapse:collapse;margin-top:6px;width:100%}
details td{padding:3px 6px;border-top:1px solid var(--line);vertical-align:top}
details td:first-child{color:var(--faint);white-space:nowrap}

/* overview */
.ov-hero{background:var(--char);color:#fff;border-radius:18px;padding:28px 30px;margin-bottom:18px}
.ov-hero h1{margin:0 0 8px;font-size:1.9em;letter-spacing:-.01em}
.ov-hero p{margin:0;color:#c3c9cf;max-width:70ch}
.ov-hero .btn{margin-top:18px}
.ov-sec{font-size:.75em;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:22px 0 10px}
.vcards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
@media(max-width:900px){.vcards{grid-template-columns:1fr}}
.vcard{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;border-top:5px solid}
.vcard.supported{border-top-color:var(--sup)}.vcard.refuted{border-top-color:var(--ref)}.vcard.no_evidence{border-top-color:#9aa3ad}
.vcard h3{margin:6px 0 4px;font-size:1.05em}
.vcard p{margin:0;font-size:.86em;color:#3c434b}
.vcard .eg{margin-top:8px;font-size:.8em;color:var(--muted);font-style:italic}
.vcard .n{float:right;font-size:1.8em;font-weight:800;line-height:1}
.vcard.supported .n{color:var(--sup)}.vcard.refuted .n{color:var(--ref)}.vcard.no_evidence .n{color:var(--ne)}
.sitegrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.sitecard{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px;text-align:left;cursor:pointer}
.sitecard:hover{border-color:var(--orange)}
.sitecard b{display:block;font-size:1.05em}
.sitecard small{color:var(--muted)}
.bar{display:flex;height:8px;border-radius:4px;overflow:hidden;margin-top:10px;background:#eee}
.bar i{display:block;height:100%}
.facts{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
@media(max-width:900px){.facts{grid-template-columns:1fr 1fr}}
.fact{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.fact b{display:block;font-size:1.6em}
.fact span{font-size:.8em;color:var(--muted)}
.steps{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
@media(max-width:1000px){.steps{grid-template-columns:1fr 1fr}}
.step{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;position:relative}
.step .num{width:30px;height:30px;border-radius:9px;background:var(--orange);color:#fff;font-weight:800;display:flex;align-items:center;justify-content:center;margin-bottom:10px}
.step b{display:block;font-size:1em}
.step p{margin:4px 0 0;font-size:.84em;color:#3c434b}
.sitenote{margin:-4px 0 12px;font-size:.82em;color:var(--muted)}
.ask{border:1px solid var(--orange);background:#fff;color:var(--orange);border-radius:999px;padding:2px 11px;font-size:.74em;font-weight:600;margin-left:auto}
.ask:hover{background:var(--orange);color:#fff}
.ask:disabled{opacity:.6;cursor:wait}
.spin.o{border-color:var(--orange);border-right-color:transparent}
.hint{background:#fff8f3;border:1px solid #f6d3bd;border-radius:12px;padding:10px 14px;font-size:.85em;margin-bottom:16px;color:#5a3a26}
.crumb{font-size:.8em;color:var(--muted)}
.crumb button{border:0;background:none;color:var(--orange);padding:0;font-weight:600}
.empty{padding:60px 20px;text-align:center;color:var(--muted)}
.foot{margin-top:18px}
pre{margin:0;background:var(--char);color:#e9ecef;padding:14px 16px;white-space:pre-wrap;font-size:.8em}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--char);color:#fff;padding:10px 16px;border-radius:10px;font-size:.85em;display:none;z-index:10;max-width:90vw}
</style></head><body>
<header id="hdr">
  <div class="hbar">
    <div class="brand"><div class="logo">PROVE IT<span>.</span></div><div class="tagline">Every progress claim checked against the site scan, with the image behind it</div></div>
    <div class="status" id="status"></div>
    <button class="tgl" id="howbtn" aria-expanded="false">How it works ▾</button>
    <button class="tgl" id="pres" title="Larger text for the projector">Presentation size</button>
  </div>
  <div class="how">
    <div><i>1</i><b>Claim sheet</b>Official claims.json: what the contractor says is done.</div>
    <div><i>2</i><b>Look properly</b>Each 360° laser-scan panorama is cut into 8 normal views: 4 walls, 2 ceiling, 2 floor.</div>
    <div><i>3</i><b>Judge with strict rules</b>Claude reads every view. Refute only on visible proof; say no_evidence when the scan cannot tell.</div>
    <div><i>4</i><b>Validate the proof</b>Code attaches the panorama path from scenes.json, rejects invalid answers, runs the official checker.</div>
  </div>
</header>
<div class="layout">
  <aside>
    <div class="search"><input id="q" placeholder="Search claims or scenes" aria-label="Search claims or scenes"></div>
    <button class="navbtn" id="ovbtn">⌂ Home</button>
    <div id="scenes"></div>
  </aside>
  <main><div class="wrap" id="main"><div class="empty">Loading real data from the project folder...</div></div></main>
</div>
<div class="toast" id="toast"></div>
<script>
var S=null, SCENE=null, CLAIM=null, VIEW=null, BUSY=false;
var LABEL={supported:'Supported',refuted:'Refuted',no_evidence:'No evidence'};
var MEAN={supported:'the scan shows the claimed work',refuted:'the scan shows the work is not done',no_evidence:'the scan cannot settle it either way'};
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function badge(v){return '<span class="badge '+(v||'none')+'">'+esc(v||'not answered')+'</span>';}
function toast(t){var e=document.getElementById('toast');e.textContent=t;e.style.display='block';clearTimeout(toast.t);toast.t=setTimeout(function(){e.style.display='none';},4000);}
function fit(){document.querySelector('.layout').style.setProperty('--hh',document.getElementById('hdr').offsetHeight+'px');}
function claimsOf(s){return S.claims.filter(function(c){return c.sceneId===s;});}
function vOf(c){return c.submitted&&c.submitted.verdict;}

function load(keep){fetch('/api/state').then(function(r){return r.json();}).then(function(d){
  if(d.error)throw d.error;
  S=d;
  status(); scenes(); if(SCENE===null)overview(); else report(keep); fit();
}).catch(function(e){document.getElementById('main').innerHTML='<div class="empty">Cannot load data: '+esc(e)+'<br>Is <code>python app.py</code> still running?</div>';});}

function status(){
  var sc=Object.values(S.scenes), pf=sc.filter(function(s){return s.panoFound;}).length, ch=S.lastCheck, c=S.counts;
  var h='<span class="st">Team <b>'+esc(S.team)+'</b></span>';
  h+='<span class="st"><span class="dot '+(S.submissionExists&&S.answered===S.total?'ok':'bad')+'"></span>Submission '+(S.submissionExists?S.answered+'/'+S.total+' claims':'missing')+'</span>';
  h+='<span class="st">'+(c.supported||0)+' supported · '+(c.refuted||0)+' refuted · '+(c.no_evidence||0)+' no evidence</span>';
  h+='<span class="st"><span class="dot '+(pf===sc.length?'ok':'bad')+'"></span>Scans on disk '+pf+'/'+sc.length+'</span>';
  h+='<span class="st"><span class="dot '+(ch.ran?(ch.ok?'ok':'bad'):'')+'"></span>Official checker: '+(ch.ran?(ch.ok?'passed':'FAILED')+' '+esc(ch.at):'not run yet')+' <button id="chk">Run</button></span>';
  document.getElementById('status').innerHTML=h;
  document.getElementById('chk').onclick=runCheck;
}

function sitesMap(){var m={};Object.keys(S.scenes).sort().forEach(function(s){var k=s.split('/')[0];(m[k]=m[k]||[]).push(s);});return m;}
function siteName(k){return k.replace('site_','Site ');}
function sceneName(s){return s.split('/')[1].replace('scene_','Scene ');}
function scenes(){
  var q=document.getElementById('q').value.toLowerCase(), h='', m=sitesMap();
  document.getElementById('ovbtn').classList.toggle('sel',SCENE===null);
  Object.keys(m).forEach(function(site){
    var list=m[site].filter(function(s){var cs=claimsOf(s);return !q||s.toLowerCase().indexOf(q)>=0||cs.some(function(c){return (c.claimId+' '+c.statement).toLowerCase().indexOf(q)>=0;});});
    if(!list.length)return;
    var n=list.reduce(function(a,s){return a+claimsOf(s).length;},0);
    h+='<div class="site"><div class="site-h"><b>'+esc(siteName(site))+'</b><span>'+list.length+' scene'+(list.length>1?'s':'')+' · '+n+' claims</span></div>';
    list.forEach(function(s){var cs=claimsOf(s);
      h+='<button class="scene'+(s===SCENE?' sel':'')+'" data-s="'+s+'"><span class="nm">'+esc(sceneName(s))+'<small>'+cs.length+' claims</small></span><span class="tally">'+cs.map(function(c){return '<i class="t-'+(vOf(c)||'none')+'" title="'+esc(c.claimId+': '+(vOf(c)||'-'))+'"></i>';}).join('')+'</span></button>';});
    h+='</div>';
  });
  var el=document.getElementById('scenes'); el.innerHTML=h||'<div class="empty" style="color:#8d959e">No match.</div>';
  el.querySelectorAll('.scene').forEach(function(b){b.onclick=function(){SCENE=b.dataset.s;CLAIM=null;VIEW=null;scenes();report();document.querySelector('main').scrollTop=0;};});
}
function openSite(site){var m=sitesMap();SCENE=m[site][0];CLAIM=null;VIEW=null;scenes();report();document.querySelector('main').scrollTop=0;}
function overview(){
  var c=S.counts, m=sitesMap(), nS=Object.keys(S.scenes).length, h='';
  h+='<div class="ov-hero"><div class="crumb" style="color:#ffb183">Saudi ConTech Hackathon · Track A</div><h1>Is the contractor\'s progress claim true? Prove it.</h1>';
  h+='<p>Contractors report progress like "walls fully plastered" or "windows installed". PROVE IT checks every claim against a 360° laser scan of the room and answers with one of three verdicts, the exact scan behind it, and a written reason. When the scan cannot tell, it says so instead of guessing.</p>';
  h+='<button class="btn primary" id="start">Open the first scene</button></div>';
  h+='<div class="facts"><div class="fact"><b>'+S.total+'</b><span>claims on the official sheet</span></div><div class="fact"><b>'+S.answered+'</b><span>answered in our submission</span></div><div class="fact"><b>'+Object.keys(m).length+' · '+nS+'</b><span>sites · scenes (one 360° scan each)</span></div><div class="fact"><b>'+(S.lastCheck.ran?(S.lastCheck.ok?'Passed':'Failed'):'Not run')+'</b><span>official submission checker</span></div></div>';
  h+='<div class="ov-sec">How PROVE IT works</div><div class="steps">';
  [['Read the claim','Load the official claim sheet: what the contractor says is finished in each room.'],
   ['Look properly','Each 360° laser scan is cut into 8 normal views (4 walls, 2 ceiling, 2 floor) so small items are visible.'],
   ['Judge with strict rules','Claude inspects every view. It refutes only on visible proof, and answers no_evidence when the scan cannot tell.'],
   ['Prove it','Code attaches the exact scan file from the official scene list, rejects invalid answers, and runs the official checker.']
  ].forEach(function(x,i){h+='<div class="step"><div class="num">'+(i+1)+'</div><b>'+x[0]+'</b><p>'+x[1]+'</p></div>';});
  h+='</div>';
  h+='<div class="ov-sec">The three possible answers · our results</div><div class="vcards">';
  h+='<div class="vcard supported"><span class="n">'+(c.supported||0)+'</span><span class="badge supported">supported</span><h3>The work is visibly done</h3><p>The scan clearly shows what the claim describes.</p><div class="eg">e.g. window frames with sashes and sills are fitted</div></div>';
  h+='<div class="vcard refuted"><span class="n">'+(c.refuted||0)+'</span><span class="badge refuted">refuted</span><h3>The scan shows it is NOT done</h3><p>The opposite is visible. Not seeing something only counts if that area is clearly in view.</p><div class="eg">e.g. bare concrete with formwork marks where plaster is claimed</div></div>';
  h+='<div class="vcard no_evidence"><span class="n">'+(c.no_evidence||0)+'</span><span class="badge no_evidence">no_evidence</span><h3>The scan cannot tell</h3><p>Hidden, out of view, or not visible in a picture at all. This is not the same as false, and an honest answer scores points.</p><div class="eg">e.g. "sprinkler main is pressure-tested"</div></div>';
  h+='</div>';
  h+='<div class="ov-sec">Sites · click one, or use the sidebar, to inspect its scenes</div>';
  h+='<p class="sitenote">The official claim sheet covers '+Object.keys(m).length+' sites: '+Object.keys(m).map(function(k){return k.replace('site_','');}).join(', ')+'. Dataset site numbers skip, so other numbers have no claims to check.</p><div class="sitegrid">';
  Object.keys(m).forEach(function(site){
    var cs=S.claims.filter(function(x){return x.sceneId.split('/')[0]===site;}), k={supported:0,refuted:0,no_evidence:0};
    cs.forEach(function(x){if(k[vOf(x)]!=null)k[vOf(x)]++;});
    var t=cs.length||1;
    h+='<button class="sitecard" data-site="'+site+'"><b>'+esc(siteName(site))+'</b><small>'+m[site].length+' scene'+(m[site].length>1?'s':'')+' · '+cs.length+' claims · '+k.supported+' ✓ '+k.refuted+' ✗ '+k.no_evidence+' ?</small><div class="bar"><i style="width:'+(100*k.supported/t)+'%;background:var(--sup)"></i><i style="width:'+(100*k.refuted/t)+'%;background:var(--ref)"></i><i style="width:'+(100*k.no_evidence/t)+'%;background:#9aa3ad"></i></div></button>';
  });
  h+='</div>';
  h+='<div class="ov-sec">How to read a scene page</div><div class="hint">Left: the scan views (orange dot = the view the AI cited for the selected claim; click to zoom). Right: every claim with its verdict, the reason, and the exact image file cited. <b>Re-run live</b> asks the AI again right now and compares with our submitted answer.</div>';
  var el=document.getElementById('main'); el.innerHTML=h;
  document.getElementById('start').onclick=function(){openSite(Object.keys(m)[0]);};
  el.querySelectorAll('.sitecard').forEach(function(b){b.onclick=function(){openSite(b.dataset.site);};});
}

function report(keep){
  var sc=S.scenes[SCENE], cs=claimsOf(SCENE), cnt={supported:0,refuted:0,no_evidence:0};
  cs.forEach(function(c){if(cnt[vOf(c)]!=null)cnt[vOf(c)]++;});
  var sel=cs.find(function(c){return c.claimId===CLAIM;}), cited=sel&&sel.model&&sel.model.views||[];
  if(!VIEW||!keep){VIEW=(cited.length&&sc.views.indexOf(cited[0])>=0)?cited[0]:(sc.views.indexOf('overview')>=0?'overview':sc.views[0]);}
  var p=SCENE.split('/'), h='';
  h+='<div class="report-h"><div><div class="crumb"><button id="back">Home</button> / '+esc(siteName(p[0]))+' / '+esc(sceneName(SCENE))+'</div><div class="q">"How done is this? Prove it."</div><h1>'+esc(siteName(p[0]))+' · '+esc(sceneName(SCENE))+'</h1></div>';
  h+='<div class="act"><button class="btn primary" id="live"'+(BUSY?' disabled':'')+'>'+(BUSY==='scene'?'<span class="spin"></span>Claude is checking this scene...':'Re-run whole scene live')+'</button></div></div>';
  h+='<div class="summary">';
  ['supported','refuted','no_evidence'].forEach(function(v){h+='<div class="sum '+v+'"><b>'+cnt[v]+'</b><span>'+LABEL[v]+'</span><small>'+MEAN[v]+'</small></div>';});
  h+='</div>';
  if(!CLAIM)h+='<div class="hint">Click any claim on the right: the viewer jumps to the scan view the AI used as proof (orange dot). Click the image to zoom in.</div>';
  if(sc.live)h+='<div class="live" style="margin:-6px 0 16px">Live re-run at '+esc(sc.live.at)+' ('+esc(sc.live.seconds)+' s): '+liveSummary(cs,sc.live)+'. Live runs never change the submitted file.</div>';
  h+='<div class="grid2">';
  // viewer
  h+='<div class="card"><div class="card-h">Site scan evidence<span class="r">'+(sel?'Showing evidence for '+esc(sel.claimId):'Click a claim to jump to the view it cites')+'</span></div>';
  if(!sc.views.length)h+='<div class="empty">No views cut for this scene yet. Run verify.py for it first.</div>';
  else{
    var isCited=cited.indexOf(VIEW)>=0;
    h+='<div class="viewer" id="vw"><img src="/views/'+SCENE+'/'+VIEW+'.jpg" alt="'+esc(VIEW)+' view"><span class="vtag'+(isCited?' cite':'')+'">'+esc(VIEW.replace('_',' '))+(isCited?' · cited by AI for '+esc(sel.claimId):'')+'</span></div>';
    h+='<div class="thumbs">'+sc.views.map(function(n){return '<button class="th'+(n===VIEW?' on':'')+(cited.indexOf(n)>=0?' cited':'')+'" data-v="'+n+'" title="'+n+'"><img loading="lazy" src="/views/'+SCENE+'/'+n+'.jpg" alt=""><span>'+esc(n.replace('_',' '))+'</span></button>';}).join('')+'</div>';
    h+='<div class="vnote">'+(VIEW==='overview'?'Full 360° panorama rendered from a terrestrial laser scan (not a photo), downscaled. Black areas are the scanner\'s blind spots.':'Perspective view our pipeline cut from the 360° laser-scan panorama.')+' Click the image to zoom · <a href="/views/'+SCENE+'/'+VIEW+'.jpg" target="_blank">open full size</a>'+(cited.length?' · <b style="color:var(--orange)">●</b> = view the AI cited':'')+'</div>';
  }
  h+='</div>';
  // claims
  h+='<div class="card"><div class="card-h">Claims for this scene<span class="r">'+cs.length+' claims</span></div>';
  h+='<div class="legend"><span><b style="color:var(--sup)">supported</b> visible proof</span><span><b style="color:var(--ref)">refuted</b> visible opposite</span><span><b style="color:var(--ne)">no_evidence</b> scan can\'t tell</span></div>';
  cs.forEach(function(c){
    var e=c.submitted||{}, v=e.verdict, m=c.model||{}, vs=m.views||[], ev=e.evidence||[], lv=sc.live&&sc.live.entries&&sc.live.entries[c.claimId];
    h+='<div class="claim'+(c.claimId===CLAIM?' sel':'')+'" data-c="'+c.claimId+'" tabindex="0">';
    h+='<div class="c-top">'+badge(v)+'<span class="mono">'+esc(c.claimId)+'</span><span>'+esc(c.category.replace('_',' '))+'</span><button class="ask" data-ask="'+c.claimId+'"'+(BUSY?' disabled':'')+'>'+(BUSY===c.claimId?'<span class="spin o"></span>checking...':'Ask AI to re-check')+'</button></div>';
    h+='<div class="c-st">'+esc(c.statement)+'</div>';
    h+='<div class="c-why">'+esc(e.rationale||'')+'</div>';
    h+='<div class="c-src'+(c.override?' human':'')+'">'+(c.override?'Human review override - team inspected the images':'AI rationale (Claude) - an interpretation of the scan, not ground truth')+'</div>';
    h+='<div class="chips">'+vs.map(function(n){return '<button class="vchip" data-v="'+esc(n)+'" data-c="'+c.claimId+'">view: '+esc(n.replace('_',' '))+'</button>';}).join('');
    h+='<span class="ev">'+(ev.length?'Evidence: '+ev.map(function(x){return '<code>'+esc(x)+'</code><button class="copy" data-copy="'+esc(x)+'" title="Copy path">copy</button>';}).join(' '):'No image cited - allowed for no_evidence')+'</span></div>';
    if(lv)h+='<div class="live">Live AI check'+(lv.at?' at '+esc(lv.at):'')+': '+badge(lv.verdict)+' '+(lv.verdict===v?'<span class="m">agrees with submission</span>':'<span class="d">differs from submission</span>')+'<div style="margin-top:3px;color:var(--muted)">'+esc(lv.rationale)+'</div></div>';
    h+='<details><summary>Trace this answer</summary><table>';
    h+='<tr><td>Claim ID</td><td class="mono">'+esc(c.claimId)+'</td></tr><tr><td>Scene ID</td><td class="mono">'+esc(c.sceneId)+'</td></tr>';
    h+='<tr><td>Panorama (scenes.json)</td><td class="mono">'+esc(sc.panoramas.join(', '))+' - '+(sc.panoFound?'file found on disk':'FILE MISSING')+'</td></tr>';
    h+='<tr><td>Submitted verdict</td><td>'+badge(v)+'</td></tr>';
    h+='<tr><td>Raw AI verdict</td><td>'+(m.verdict?badge(m.verdict):'not recorded')+(c.override?' (replaced by human override)':'')+'</td></tr>';
    h+='<tr><td>Views AI cited</td><td>'+(vs.length?esc(vs.join(', ')):'none recorded')+'</td></tr>';
    h+='<tr><td>AI run time (scene)</td><td>'+(sc.modelSeconds!=null?esc(sc.modelSeconds)+' s':'not recorded')+'</td></tr>';
    h+='<tr><td>Submission file</td><td class="mono">'+esc(S.submissionPath)+'</td></tr></table></details>';
    h+='</div>';
  });
  h+='</div></div>';
  if(S.lastCheck.ran)h+='<div class="card foot"><div class="card-h">Official checker output<span class="r">scripts/check_submission.py · '+esc(S.lastCheck.at)+'</span></div><pre>'+esc(S.lastCheck.output)+'</pre></div>';
  var el=document.getElementById('main'); el.innerHTML=h;
  // wire up
  el.querySelectorAll('.th').forEach(function(b){b.onclick=function(){VIEW=b.dataset.v;report(true);};});
  el.querySelectorAll('.claim').forEach(function(d){
    var pick=function(){CLAIM=d.dataset.c;VIEW=null;report();};
    d.onclick=function(ev){if(ev.target.closest('button,a,details'))return;pick();};
    d.onkeydown=function(ev){if(ev.key==='Enter')pick();};
  });
  el.querySelectorAll('.vchip').forEach(function(b){b.onclick=function(){CLAIM=b.dataset.c;VIEW=b.dataset.v;report(true);document.getElementById('vw')&&document.getElementById('vw').scrollIntoView({behavior:'smooth',block:'nearest'});};});
  el.querySelectorAll('.copy').forEach(function(b){b.onclick=function(){try{navigator.clipboard.writeText(b.dataset.copy);toast('Copied '+b.dataset.copy);}catch(x){}};});
  var vw=document.getElementById('vw');
  if(vw){var im=vw.querySelector('img');im.onclick=function(){vw.classList.toggle('zoom');};
    im.onerror=function(){vw.innerHTML='<div class="empty" style="color:#ccc">This image could not be loaded.</div>';};}
  document.getElementById('live').onclick=function(){runLive(null);};
  el.querySelectorAll('.ask').forEach(function(b){b.onclick=function(ev){ev.stopPropagation();CLAIM=b.dataset.ask;runLive(b.dataset.ask);};});
  document.getElementById('back').onclick=goOverview;
}
function goOverview(){SCENE=null;CLAIM=null;VIEW=null;scenes();overview();document.querySelector('main').scrollTop=0;}

function liveSummary(cs,lv){
  var n=0,m=0; cs.forEach(function(c){var l=lv.entries&&lv.entries[c.claimId];if(l){n++;if(l.verdict===vOf(c))m++;}});
  return '<b>'+m+' of '+n+'</b> verdicts match the submission';
}
function runLive(cid){
  if(BUSY)return; BUSY=cid||'scene'; report(true); var s=SCENE;
  fetch('/api/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cid?{scene:s,claimId:cid}:{scene:s})})
  .then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
  .then(function(x){BUSY=false;if(!x.ok){toast('Live run failed: '+(x.d.error||'unknown')+'. Submitted verdicts unchanged.');report(true);return;}toast(cid?'AI re-checked '+cid:'Live re-run finished for '+s);load(true);})
  .catch(function(e){BUSY=false;toast('Live run failed: '+e);report(true);});
}
function runCheck(){
  var b=document.getElementById('chk');b.disabled=true;b.textContent='running...';
  fetch('/api/check',{method:'POST'}).then(function(r){return r.json();}).then(function(d){toast(d.ok?'Official checker passed':'Official checker FAILED - see output');load(true);})
  .catch(function(e){toast('Checker could not run: '+e);load(true);});
}
document.getElementById('q').oninput=scenes;
document.getElementById('ovbtn').onclick=goOverview;
document.getElementById('howbtn').onclick=function(){var o=document.body.classList.toggle('howopen');this.classList.toggle('on',o);this.setAttribute('aria-expanded',o);this.textContent=o?'How it works ▴':'How it works ▾';fit();};
document.getElementById('pres').onclick=function(){document.body.classList.toggle('present');fit();};
window.onresize=fit;
load();
</script></body></html>"""







def main():
    global TEAM
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="TEAM2")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    TEAM = args.team
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"PROVE IT running for team {TEAM}: open http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
