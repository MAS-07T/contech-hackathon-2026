#!/usr/bin/env python3
"""Build a LOCAL review page: each scene's views next to the tool's verdicts.

  python review.py --team TEAM2
  start work\\review.html

Pick your own verdict for each claim, then click "Copy disagreements" and paste
the result to the team. The page stays on your laptop (work/ is git-ignored) -
do not share or upload it: it contains the challenge images.
"""
import argparse
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "track_a"
WORK = ROOT / "work"
VIEW_ORDER = ["overview", "front", "right", "back", "left",
              "ceiling_front", "ceiling_back", "floor_front", "floor_back"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True)
    args = ap.parse_args()
    claims = json.load(open(DATA / "claims.json", encoding="utf-8"))["claims"]
    sub = json.load(open(ROOT / "submissions" / args.team / "submission.json", encoding="utf-8"))
    answers = {e["claimId"]: e for e in sub["track_a"]}
    by_scene = {}
    for c in claims:
        by_scene.setdefault(c["sceneId"], []).append(c)

    parts = []
    for scene in sorted(by_scene):
        imgs = "".join(
            f'<figure><a href="views/{scene}/{v}.jpg" target="_blank">'
            f'<img loading="lazy" src="views/{scene}/{v}.jpg"></a><figcaption>{v}</figcaption></figure>'
            for v in VIEW_ORDER if (WORK / "views" / scene / f"{v}.jpg").exists())
        rows = ""
        for c in by_scene[scene]:
            a = answers.get(c["claimId"], {})
            v = a.get("verdict", "?")
            rows += (
                f'<tr data-id="{c["claimId"]}" data-tool="{v}">'
                f'<td class="id">{c["claimId"]}</td>'
                f'<td>{html.escape(c["statement"])}<div class="why">{html.escape(a.get("rationale", ""))}</div></td>'
                f'<td><span class="v {v}">{v}</span></td>'
                f'<td><select><option value="">agree?</option><option>supported</option>'
                f'<option>refuted</option><option>no_evidence</option></select>'
                f'<input placeholder="why (short)"></td></tr>')
        parts.append(f'<section id="{scene}"><h2>{scene}</h2><div class="grid">{imgs}</div>'
                     f'<table>{rows}</table></section>')

    toc = " ".join(f'<a href="#{s}">{s.split("/")[1]}</a>' for s in sorted(by_scene))
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Track A review</title>
<style>
body{{font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px;background:#f4f5f7;color:#15181d}}
header{{position:sticky;top:0;background:#f4f5f7;padding:8px 0;border-bottom:1px solid #ccc;z-index:2}}
header a{{margin-right:6px;font-size:12px}}
button{{padding:6px 12px;font-weight:600;cursor:pointer}}
section{{background:#fff;border-radius:12px;padding:16px;margin:16px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px}}
figure{{margin:0}} img{{width:100%;border-radius:6px;display:block}}
figcaption{{font-size:11px;color:#666}}
table{{width:100%;border-collapse:collapse;margin-top:12px}}
td{{border-top:1px solid #eee;padding:8px;vertical-align:top}}
.id{{font-family:monospace;white-space:nowrap}} .why{{color:#666;font-size:12px;margin-top:4px}}
.v{{font-family:monospace;font-weight:700;padding:2px 6px;border-radius:6px}}
.supported{{background:#e5f4ec;color:#1a7f45}} .refuted{{background:#fde8e8;color:#b42318}}
.no_evidence{{background:#eee;color:#444}}
tr.disagree{{background:#fff5e6}} input{{width:140px;margin-left:4px}}
</style></head><body>
<header><b>Track A review - {html.escape(args.team)}</b>
<button onclick="copyOut()">Copy disagreements</button> <span id="msg"></span><br>{toc}</header>
{''.join(parts)}
<script>
document.querySelectorAll('tr[data-id]').forEach(function(tr){{
  tr.querySelector('select').addEventListener('change',function(e){{
    tr.classList.toggle('disagree', e.target.value && e.target.value!==tr.dataset.tool);
  }});
}});
function copyOut(){{
  var out=[];
  document.querySelectorAll('tr[data-id]').forEach(function(tr){{
    var mine=tr.querySelector('select').value, why=tr.querySelector('input').value;
    if(mine && mine!==tr.dataset.tool) out.push(tr.dataset.id+': tool='+tr.dataset.tool+' human='+mine+(why?' ('+why+')':''));
  }});
  var txt=out.join('\\n')||'No disagreements marked.';
  try{{navigator.clipboard.writeText(txt);document.getElementById('msg').textContent='Copied '+out.length+' line(s)';}}
  catch(e){{prompt('Copy this:',txt);}}
}}
</script></body></html>"""
    (WORK / "review.html").write_text(page, encoding="utf-8")
    print(f"Wrote {WORK / 'review.html'}  -  open it with:  start work\\review.html")


if __name__ == "__main__":
    main()
