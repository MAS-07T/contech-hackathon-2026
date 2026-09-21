# Prove It — Construction Progress Verification

**Saudi ConTech October Hackathon · Dammam · Saturday 10 October 2026**

On every project, someone claims progress and someone else has to check it.
Today that check is a person clicking through site imagery, hundreds of times
a week. Your job is to automate the check — and prove every answer.

Teams build AI tools that read real construction imagery and output an
**evidence log**: a list of verdicts, each with the exact images that prove it.
If the evidence isn't there, the tool says so. Guessing confidently is how
you lose.

## The two tracks

Every team competes in both. One tool or two is your call.

### Track A — Verify the claim sheet (Rohbau3D)

Real indoor construction scenes from **8 German construction sites** — 360°
panoramas rendered from terrestrial laser scans, from bare shell to fitted-out.
Half the scenes carry a **claim sheet** (`data/track_a/claims.json`; the
rest are context): statements a contractor might
file ("internal walls in this room are fully plastered", "windows installed
throughout", "MEP rough-in complete on the ceiling"). Some are true. Some are
not. For each claim your tool answers:

`supported` · `refuted` · `no_evidence` — plus the panorama(s) that prove it.

### Track B — What changed here? (ConSLAM)

One real London residential project, **walked four times between April and
August 2022** — the same two zones photographed across the visits, with poses.
For each zone and consecutive visit pair, your tool reports **what work
happened** between the visits, as structured changes (facade, partitions,
MEP/services, finishes, materials & logistics, structure), each backed by
frames from *both* visits.

## The finale

Judges pick a scene or a zone-pair and query your tool live: *"How done is
this — prove it."* The authored answers go up on screen right after yours.
Scoring is half objective (accuracy + citation validity against held-out
answers), half panel (business impact, engineering mindset, clarity). Details
in [SUBMISSION.md](SUBMISSION.md).

## Get started

1. Clone this repo. Read [DATA.md](DATA.md) — ten minutes there saves an hour.
2. Download the image packs (**before event day** — hard prerequisite):

   ```bash
   python3 scripts/download_data.py --dest data/
   ```

3. Build. Any approach is legal — VLMs, classical CV, hybrids. There is no
   starter model on purpose: finding the approach is the exercise.
4. Validate your output:

   ```bash
   python3 scripts/check_submission.py my_submission.json
   ```

5. Submit by PR: `submissions/<TEAM_NAME>/submission.json` + your code, by
   **code freeze, 16:30 on event day**.

## Rules

- Teams of 5. Build on the day; exploring the data beforehand is expected.
- Every verdict needs cited images. Right answer, no evidence = zero.
- `no_evidence` is a legal, scoreable answer — honesty beats bravado.
- Both source datasets are public; nothing stops you reading their papers.
  The claim sheets and change keys are authored for this event and are what
  you're scored on.

## Out of scope

Dashboards, earned value, payments, forecasting, viewers. WakeCap already
builds these — we're judging the evidence, not the dashboard.

## Data credits and use

- **Rohbau3D** — Rauch et al., TU Munich / UniBw München, MIT license.
  Cite: *Rohbau3D: A Shell Construction Site 3D Point Cloud Dataset*,
  Scientific Data (2025).
- **ConSLAM** — Trzeciak et al., University of Cambridge & Inria. Used and
  redistributed to registered teams with written permission of the authors
  (Prof. Ioannis Brilakis, Sept 2026). Cite the ECCV 2022 Workshops and
  J. Comput. Civ. Eng. papers.

Frames may show site personnel. Use all imagery for this challenge only;
do not redistribute or post it.
