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
| corpus-v2 | `configs/config_deepseek.yaml` | revised | RU | ~250 *(forthcoming — generation in progress)* | — | — | yes |
| replay forks | parent configs | revised | EN/RU | 32 | 3,043 | 28 / 4 | yes |

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
- **corpus-v2** (~250 RU games, revised instrument) is being generated at
  the time of this snapshot and will be added in a follow-up release.
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
