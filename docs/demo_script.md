# Screencast script — MafiaScope demo (2026-07-18, target ≤ 2:30)

Reshoot for the EMNLP demo resubmission. Captions ship as a **separate SRT**
(`mafiascope_demo_2026-07-18.srt`), not burned into the frame; the cue texts
below are the `CAPS` list in `src/record_screencast.py` (single source of
truth — the SRT is built from `marks.json` by `src/build_srt.py`; the silent
closing card produces no cue).

Recorded in one continuous headless take by `src/record_screencast.py`
(1680×940, virtual cursor, onboarding tour suppressed, node hover disabled so
no Big Five tooltip ever appears — that line was dropped from the paper).
Serve locally with a completed-games farm:
`cd src && python serve_viewer.py --no-open --port 8899 -d <farm>`; the farm
holds the corpora from `docs/corpora.json` (en_demo, ru_clean, ablation_demand,
corpus32, gpt4omini28, game_family_showcase) plus the bifurcation parent
`3845221c` and its 20 pre-computed forks (`../../mafia2/data/bifurcation`).

**Pacing**: every scene is cut to its narration. The optional TTS track (piper
`en_US-lessac-medium`, length_scale 1.08 — slightly slower than neutral, never
sped up) starts at each scene mark; scene lengths leave a ≤2 s tail, so there
are no dead pauses and no rushed speech. Narration ≈ 350 words; total runtime
2:22 (8 narrated cues + ~5 s silent URL card).

| # | Time | Screen / deep-link | On-screen action | Caption (EN) |
|---|------|--------------------|------------------|--------------|
| 1 | 0:00–0:17 | `viewer.html#g=36594b66&s=16` | Cursor sweeps the layout: ground-truth graph center, per-agent belief panels around it, timeline on top | "This is MafiaScope: seven language-model agents playing Mafia. The center shows ground truth. Every side panel is one agent's private beliefs, collected by structured probes after every public message, outside the game context, so the game itself is never disturbed." |
| 2 | 0:17–0:34 | same, steps 17→19 | Step through the timeline; point at the "hid 75% / hid 100%" deception rings on the two mafiosi (no tooltips) | "The timeline replays the game step by step, and beliefs update live. On the ground-truth graph every mafioso carries a deception ring: the share of informed observers who still fail to name it Mafia. Here Finley stays fully hidden, while Logan is already partly exposed." |
| 3 | 0:34–0:50 | `#g=36594b66&s=26&m=metrics` | Metrics strip: trace the three charts left to right (parse-rate badge visible but not narrated) | "The Metrics panel turns the viewer into a measuring instrument: first-order accuracy against ground truth, crowd Mafia recall versus per-mafioso deception success, and second-order consistency: does an agent know what others think of it?" |
| 4 | 0:50–1:04 | `#m=calib` | Calibration modal, **corpus view only — the agent filter is NOT touched** (on one game a single agent fills only two bins and the chart degenerates); slow pass over the 60–79 and 80–100 bins against the diagonal | "Calibration compares stated confidence with actual accuracy. These agents are overconfident: across the corpus, answers given at about eighty-five percent confidence are right only about fifty-five percent of the time." |
| 5 | 1:04–1:23 | `#g=36594b66&s=20&a=Logan&m=imp` | Impersonate Logan: BELIEVED "How I think they see me" vs ACTUAL panel, match score 2/4; then one click on 🔗 copies the deep link (toast "Link copied") | "Click a player to impersonate them: this is Logan, secretly Mafia. The right side is pure theory of mind: what Logan believes others think of him, against what they actually think. The match score makes the gap quantitative: Logan gets only two of four right. And one click copies a deep link to this exact view." |
| 6 | 1:23–1:54 | `#g=3845221c&s=17`, then `#bif=poli_3845221c_r1_Alex` | THE NEW CORE. Quadrant badge on the timeline (◆ policy gap · Alex → Finley); click it: the fork fan opens — lock-in 232/500 → Harper, flip share 4/20; scroll the 20 variants; click flip variant #2 → fork `eae8e206` opens: outcome strip "branch of 3845221c … intervention", jump to the end — Villagers win | "The core feature: bifurcation points. A colored badge marks a decisive vote; this agent read the board correctly and still voted wrong. That vote was resampled five hundred times, and twenty diverse variants were replayed to the end. The panel shows lock-in and flip share: four of twenty forks flip the game to a Villagers win. Flips concentrate where the agent had read the game right; wrongly-assessed votes are locked in. One click opens a flipped fork: here the village survives, and each analysed loss gets a classified cause." |
| 7 | 1:54–2:06 | sidebar → game `4a9a8dc9` | Open the games sidebar, click the Resistance game (type badge in the card and header, "transcript only — no probe data" chip), step to the mission votes | "The same engine and viewer cover a whole game family: werewolf skins and Resistance. Transcript-only games keep the timeline and the replay, without belief panels." |
| 8 | 2:06–2:17 | `dashboard.html` | Cross-game dashboard: linger on the language / models facets, scroll to the aggregate curves and matchup table, scroll back and flip the lights on (💡) | "The cross-game dashboard aggregates these metrics over the corpus, filtered by language and model family. MafiaScope is open source, and the full demo runs in your browser." |
| 9 | 2:17–2:22 | same, URL card overlay | **Silent closing card** (no narration, no cue): centered dark card over the light dashboard — "MafiaScope — open source", `github.com/karpovilia/mafiascope`, `karpovilia.github.io/mafiascope`, held ~5 s. The URLs appear ON SCREEN only, they are never spoken | — |

## Fact checks (verified against the recorded data, 2026-07-18)

- Seg 2: at steps 16–19 of `36594b66` the rings read `hid 75%` (Logan) and
  `hid 100%` (Finley) — Finley fully hidden, Logan leaking.
- Seg 4: paper claim (EACL/EMNLP draft §Dynamics): accuracy 43–46% at stated
  confidence 40–79, 54.6% at 80–99 (bin mean ≈ 85) — "state ~85, right ~55".
  In-game chart (36594b66, all agents): 71/54/52/69 per bin, 60–100 hatched
  overconfident. The caption states the corpus-level number. The agent filter
  is left alone: per-agent views on one game degenerate to two bins.
- Seg 5: Logan at step 20 scores match 50% (2/4) — computed by the same
  conf≥50 attitude rule as the viewer.
- Seg 6: point `poli_3845221c_r1_Alex` (policy gap, R1.11, Alex → Finley):
  lock-in 232/500 on modal target Harper (factual vote is NOT modal),
  flip share 4/20; variant #2 = fork `eae8e206…` = Villagers in 3R.
  Do NOT reuse the old segment-6 claim (Finley R1.8, P(elim=Gray) 0.4→0.8) —
  that scene is gone from the script.
- Seg 7: `4a9a8dc9` is `resistance` (Spy wins, 35 steps, transcript-only);
  werewolf skins `07c3255e` (seer) / `10a9ec09` (skin) are in the farm and the
  dashboard matchup table.

## Recording notes

- `src/record_screencast.py <out_dir>` → webm + `marks.json`; convert with
  `ffmpeg -ss <trim> -i in.webm -c:v libx264 -crf 20 -r 30 -pix_fmt yuv420p
  -movflags +faststart mafiascope_demo.mp4`; SRT via
  `build_srt.py <out_dir>/marks.json mafiascope_demo.srt` (timings already
  shifted by the trim point; empty-text segments are skipped).
- Voice track: synthesize each cue with piper (`length_scale 1.08`), place at
  the cue start (`adelay`), `amix` over the video. Verified: per-scene tail
  0.6–1.9 s, no silence >2 s inside a scene, speech never sped up.
- No Big Five anywhere: node hover is disabled in the inject script and the
  narration never mentions personalities.
- The parse-rate badge stays on screen (it is part of the header) but is not
  narrated.
