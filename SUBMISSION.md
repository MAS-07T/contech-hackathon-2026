# Submission format — the evidence log

One JSON file, two sections. Validate with `scripts/check_submission.py`
before the deadline — format errors cost build time, not judge sympathy.

```json
{
  "team": "TEAM_NAME",
  "track_a": [
    {
      "claimId": "A-0031",
      "verdict": "refuted",
      "evidence": ["site_05/scene_05012/color.png"],
      "rationale": "South wall exposed brick, no plaster line visible."
    },
    {
      "claimId": "A-0032",
      "verdict": "no_evidence",
      "rationale": "Ceiling zone occluded by scaffold in the only panorama."
    }
  ],
  "track_b": [
    {
      "taskId": "B-zone_02-v3v4",
      "changes": [
        {
          "category": "facade",
          "direction": "progressed",
          "note": "Glazing line closed along east elevation.",
          "evidence": {
            "before": ["sequence3/1654763780493035.png"],
            "after":  ["sequence4/1656498120014011.png"]
          }
        },
        {"category": "structure", "direction": "no_change"}
      ]
    }
  ]
}
```

## Rules

**Track A (required)** — one entry per claim in `data/track_a/claims.json`.
Verdicts: `supported` · `refuted` · `no_evidence`. Any verdict except
`no_evidence` needs at least one cited panorama from that claim's scene.
`rationale` is optional but helps the panel (and you, at the interrogation).
The checker requires every Track A claim to be answered.

**Track B (bonus — optional)** — attempt only after Track A works. Add one
entry per task in `data/track_b/tasks.json` for the ones you reach; leaving
`track_b` empty is fine and passes the checker. Report every category you have
a finding for; a category you omit counts as `no_change`. Every change except
`no_change` needs evidence frames from **both** visits, drawn from that task's
frame lists.

## Scoring

Core is 100 points; Track B adds bonus on top.

| component | weight | how |
|---|---|---|
| Track A verdict accuracy | 50 pts | vs. the authored key |
| Citation validity | 10 pts | cited files exist, belong to the scene; spot-checked live: does the image show what you say? |
| Panel rubric | 40 pts | business impact · engineering mindset · clarity |
| **Track B change detection** | **+15 bonus** | per-category F1 vs. the authored key — only for teams who cleared Track A |

Honesty is scored: a correct `no_evidence` earns full marks for that claim.
A verdict with invalid citations scores zero even if the verdict is right.

**The interrogation:** at the pitches, judges pick a claim or a zone-pair and
run it through your tool live. The authored answer is revealed right after.

## How to submit

PR adding `submissions/<TEAM_NAME>/submission.json` (required) and your code
(required for the winner, encouraged for all). The checker must pass.
Deadline: **code freeze, 16:30 on event day**.
