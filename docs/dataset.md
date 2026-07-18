# MafiaScope dataset

Every completed game ships in this repository as a directory
`logs/<game_id>/` with `game.jsonl` (public transcript + ground truth),
`introspection.jsonl` (probe records), and — for post-revision runs —
`state.jsonl` (per-message snapshots for counterfactual replay). Schemas:
[architecture.md](architecture.md). Machine-readable corpus registry:
[corpora.json](corpora.json); the executable single source of truth is
`metrics_lib.select_corpus` in `src/metrics_lib.py`.

## Instrument versions

- **pre-revision** — original probe battery: demand-characteristic
  `social_map` wording ("who suspects you?" phrasing that presupposes
  suspicion) and no probe chaining.
- **revised** — post-review battery (2026-07-10): clean `social_map`
  wording and probe chaining (each probe sees the agent's own previous
  answer via `{last_*}` placeholders), JSON-repair on, snapshots enabled.

## Corpora

| corpus | config | instrument | lang | games | probe records | wins (Mafia / Villagers) | snapshots |
|---|---|---|---|---|---|---|---|
| `main30` | `configs/config_deepseek.yaml` (pre-revision) | pre-revision | RU | 30 | 23,520 | 17 / 13 | no |
| `en_demo` | `configs/config_en_demo.yaml` | revised | EN | 5 | 3,208 | 5 / 0 | yes |
| `ablation_demand` | `configs/config_ablation_demand.yaml` | revised chaining + **old** demand `social_map` wording | RU | 5 | 7,547 | 2 / 3 | yes |
| `ru_clean` | `configs/config_deepseek.yaml` (post-fix) | revised | RU | 5 | 4,377 | 4 / 1 | yes |
| `corpus_v2` | scaled deepseek EN generation (2026-07-11/12) | revised, transcript-only (no probe battery) | EN | 147 | — | — | omitted (~300 MB) |
| replay forks | parent configs | revised | EN/RU | 32 | 3,043 | 28 / 4 | yes |

This table lists the founding batches. The canonical, machine-readable registry of
every released corpus (including `corpus32`, `gpt4omini28` / `gpt4omini100`, the
randomized A/B invasiveness arms `ab_probed_2026_07_18` / `ab_unprobed_2026_07_18`,
the bifurcation fork corpora and the game-family showcase) is
[`docs/corpora.json`](corpora.json).

Notes:

- **`main30`** is the legacy pre-revision corpus behind the originally
  published numbers (selected by batch timestamp, `setup_ts = 1774562457`;
  see `metrics_lib.MAIN_BATCH_TS`). The historical `paper32` selector adds
  two pilot runs (one completed, one aborted with a different battery);
  `main30` is the recommended canonical set.
- **`en_demo`** is the corpus used in the demo video and screenshots
  (game `36594b66…` is the featured game).
- **`ablation_demand` vs `ru_clean`** is a matched pair: identical config
  except the `social_map` wording, quantifying the demand-characteristic
  effect (see [runs_2026_07_10.md](runs_2026_07_10.md) for per-game
  outcomes and the comparison table).
- **Replay forks** are counterfactual continuations of `36594b66…`
  produced by `src/replay.py` / `src/replay_experiment.py` (2 pilot forks
  + 30 full-run forks, see [replay_experiment.md](replay_experiment.md)).
  Their `setup` records carry `forked_from` metadata and
  `metrics_lib.select_corpus` **excludes them from every corpus**.
- **corpus_v2**: 147 completed EN transcript-only games (no probe battery),
  frozen 2026-07-12; transcripts (`game.jsonl`) are committed, per-step
  `state.jsonl` snapshots (~300 MB) are omitted from the repo.
- Large-file exception: `logs/f2510502…/state.jsonl` (a 15-round
  `ablation_demand` game) is 151 MB and exceeds GitHub's 100 MB limit, so
  it is omitted from the public repository; the game's `game.jsonl` and
  `introspection.jsonl` — everything the metrics use — are included. Only
  replay-forking that particular game requires the omitted snapshots.

## Loading

```python
# run from src/
import metrics_lib as M
games = M.load_games(M.default_logs_dir())
en_demo = M.select_corpus(games, "en_demo")   # main30 | en_demo | ablation_demand | ru_clean
```

`analyze_metrics.py` prints a full corpus audit (completion, winner,
probe counts, pilot/aborted flags) with `--logs ../logs`.
