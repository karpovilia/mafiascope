# Screencast script — MafiaScope demo (target ≤ 2:30)

Draft for the EMNLP demo video. English voiceover, recorded over the live
viewer with the English game `36594b66` (7 DeepSeek agents, Mafia wins).
Serve locally with `cd src && python serve_viewer.py` (the Branch segment needs the
fork API; everything else also works on the static `site/` build).

Narration is ~340 words ≈ 2:25 at a calm pace, leaving ~5 s of slack for
transitions. Deep-links are given per segment so every take starts from a
reproducible URL; screenshots of each target screen are in `docs/img/`.
The segment-6 claim is the pivotal-utterance result on Finley's R1.8
pile-on, verified on the full run (n=5 per arm: P(elim=Gray) 0.4→0.8, see
`replay_experiment.md` and `replay_full_36594b66.json`).

| # | Time | Screen / deep-link | On-screen action | Voiceover (EN) |
|---|------|--------------------|------------------|----------------|
| 1 | 0:00–0:15 | `viewer.html#step=16` (overview) | Cursor sweeps the layout: ground-truth graph center, per-agent belief panels around it, timeline on top | "This is MafiaScope: seven LLM agents playing Mafia. The center shows ground truth; every surrounding panel is one agent's private beliefs, elicited by structured probes at every step of the game." |
| 2 | 0:15–0:40 | same, press `→` a few steps | Step through the timeline; hover a node to show the Big Five tooltip; point at the "hid 75% / hid 100%" deception rings on the two mafiosi | "The timeline replays the game step by step, and beliefs update live. Hovering a player shows their assigned Big Five personality. On the ground-truth graph, each mafioso carries a deception ring — here Finley stays fully hidden from the informed crowd, while Logan is starting to leak." |
| 3 | 0:40–1:05 | `viewer.html#metrics&step=26` | Metrics panel slides in; trace the three charts left to right; end on the parse-rate badge (probes 99%) | "The metrics panel turns the viewer into a measuring instrument. First-order accuracy: each agent's beliefs scored against ground truth. Crowd mafia recall versus per-mafioso deception success. And second-order consistency — does an agent know what others think of it? The badge on top reports probe parse rate, ninety-nine percent here." |
| 4 | 1:05–1:25 | `viewer.html#calibration` | Calibration modal; point at the 60–79 bin (hatched) vs the diagonal; switch the agent dropdown once | "Calibration compares stated confidence with actual accuracy. These agents are systematically overconfident: at sixty-to-eighty percent confidence, they are right only about half the time. Wilson intervals and per-agent filters come built in." |
| 5 | 1:25–1:55 | `viewer.html#step=20&imp=Logan` | Impersonate mode: Logan's world in the center; point at BELIEVED "How I think they see me" vs ACTUAL panel and the match score | "Clicking a player impersonates them — this is Logan, secretly Mafia. The right side is pure theory of mind: what Logan believes others think of him, against what they actually think. The match score makes the gap quantitative — Logan gets only two of four right." |
| 6 | 1:55–2:20 | `viewer.html#step=18`, click `⑂ Branch` | Fork dialog opens at R1.11 (day vote); set replays to 5, click Fork; show a finished branch batch in the sidebar / outcome strip | "Every step is a serialized snapshot, so we can ask counterfactuals: branch the game and replay it five times. With Finley's pile-on accusation in context, the innocent Gray is lynched in four branches of five. Resample that one message — and the vote scatters: twice, the village lynches the mafioso instead. Pivotality becomes a measurable distribution." |
| 7 | 2:20–2:30 | `dashboard.html` | Cross-game dashboard with aggregate curves; fade out on the repo/demo URL | "The cross-game dashboard aggregates these metrics over dozens of games and models. MafiaScope is open source — the full demo runs in your browser." |

## Recording notes

- Window 1680×940, dark theme (viewer default); hide bookmarks bar.
- Record segment 6 against `serve_viewer.py` with a pre-warmed fork batch
  (run one 5-replay fork beforehand so a finished branch is already in the
  sidebar; the freshly clicked fork only needs to show the dialog + toast).
- Keep the mouse still while speaking; move only on the cued actions.
- If the take runs long, segment 2 is the flex section — cut the Big Five
  tooltip sentence (saves ~8 s).
- Segment-6 numbers verified against the full run 2026-07-10 (n=5 per arm):
  Finley R1.8 — P(elim=Gray) 0.4→0.8, Logan lynched in 2/5 PRE branches.
  The earlier pilot claim (Logan R1.7 flips the vote) did NOT replicate at
  n=5 — do not use it.

## Matching screenshots (docs/img/)

| Segment | File |
|---------|------|
| 1–2 | `overview.png` (deception rings visible) |
| 3 | `metrics.png` |
| 4 | `calibration.png` |
| 5 | `impersonate_believed.png` |
| 6 | `branch_dialog.png` |
