# Running games — the runner subsystem

The viewer gets most of the screen time, but MafiaScope is several
cooperating parts, and the part that produces every trace the viewer shows
is the **runner** machinery documented here: the single-game/batch entry
point, the seed-grid driver, the snapshot resume driver, and the wrappers
that put them on other machines (a LAN GPU box or a SLURM cluster).

## The parts of the project

| Part | Entry points | Docs |
|---|---|---|
| **Game engine** — round loop (night/kill/save → day discuss → vote), Werewolf skin, Resistance variant, per-player contexts, snapshots | `src/game.py`, `src/game_resistance.py`, `src/player.py`, `src/prompts.py` | [architecture.md](architecture.md) |
| **Probe engine** — private belief battery after every public message, non-invasive by construction | `src/introspection.py` | [architecture.md](architecture.md) |
| **Backends & model bus** — DeepSeek / OpenAI-compatible / local `transformers` / plain-HTTP bus; Arena web panel assigns models to seats and launches batches from the browser | `src/llm_backend.py`, `src/bus_server.py`, `src/bus_client.py` | [multi_model_setup.md](multi_model_setup.md), [bus_protocol.md](bus_protocol.md) |
| **Runners** (this doc) — start, batch, grid, and finish games | `src/main.py`, `src/run_lang_games.py`, `src/resume_games.py`, `run_lang_batch.sh` | here |
| **Counterfactual replay** — fork a recorded game at any utterance, rerun N continuations | `src/replay.py`, `src/replay_experiment.py` | [replay_experiment.md](replay_experiment.md) |
| **Viewer** — timeline replay, belief graphs, impersonate, metrics panel, branch UI | `src/prepare_viewer.py`, `src/serve_viewer.py`, `src/viewer.html`, `src/dashboard.html`, `src/build_site.py` | [DOCUMENTATION.md](DOCUMENTATION.md) §7 |
| **Metrics & analysis** — corpus selection, paper metrics, belief dynamics, event labeling | `src/metrics_lib.py`, `src/analyze_metrics.py`, `src/belief_dynamics.py`, `src/event_labeler.py`, `src/aggregate_events.py` | [dataset.md](dataset.md) |

The viewer + curated dataset + fork API also ship as a container image:
`docker run --rm -p 8080:8080 -e DEEPSEEK_API_KEY=sk-... ghcr.io/karpovilia/mafiascope:latest`
(see the README section "Run with Docker" and the root `Dockerfile`).

Scaled experiments (large corpora, HPC array runs, GRPO training) live in a
separate experiments repository that reuses this engine through a symlink;
this repo stays the engine + demo + dataset home.

Every runner assumes `cwd=src/`: the engine resolves the log root as
`../logs` relative to the current working directory. All runners write the
same per-game directory `logs/<game_id>/{game,introspection,state}.jsonl`
(see [architecture.md](architecture.md) for the schemas), which is what the
viewer, replay, and metrics consume — the parts are coupled only through
these files.

## `main.py` — one game or a parallel batch

```bash
cd src
python main.py -c ../configs/config_en_demo.yaml              # one game
python main.py -c ../configs/config_en_demo.yaml -n 10 --parallel
python main.py -c ../configs/config_mafia_qwen.yaml --no-introspection --max-rounds 2  # smoke
```

- `-c/--config` — YAML with players (roles, Big Five personalities,
  per-seat backend), the `backends:` registry, `game.*` settings
  (language, snapshots, intro round, max rounds), and the probe battery
  (`introspection.probes`). See [DOCUMENTATION.md](DOCUMENTATION.md) §9.
- `-n/--num-games` + `--parallel` — a batch; games run in threads and the
  local batched `transformers` backend coalesces concurrent requests into
  GPU batches.
- `--no-introspection` / `--max-rounds N` — cheap smoke switches: disable
  the probe battery, cap the number of rounds.
- API keys come from `.env` (`DEEPSEEK_API_KEY`, …) via `python-dotenv`.

`main.py` is fire-and-forget: it does not track which games already exist.
For anything that must be *resumable* or *paired across configurations*,
use the drivers below.

## `run_lang_games.py` — paired seed-grid driver

Built for the multilingual outcome experiment (same engine, same model,
same seeds — only the game language varies), but generic: it runs N games
for ONE config on a fixed seed grid (`seed = seed_base + i`), so runs of
different configs pair 1:1 by seed (McNemar-style paired comparisons).

```bash
cd src
python run_lang_games.py -c ../configs/config_lang_zh_qwen.yaml -n 50 --seed-base 9000
```

Mechanics:

- **Ledger idempotence.** Every finished game appends
  `{seed, game_id, winner, rounds, lang, log_dir}` to
  `logs/lang_<lang>_done.jsonl`; on restart, seeds already present (in any
  record without an `"error"` key) are skipped. Failed seeds are recorded
  with `"error"` and retried next pass. Appends are line-atomic, and
  parallel shards use disjoint seed ranges, so shards of one language can
  run concurrently without collisions.
- **One model load per process.** The heavy local backend loads once
  (`get_backend` caches by name) and is reused for every game in the grid.
- The seed feeds `random.seed(seed)` before each game — it fixes the
  roster/name shuffle for pairing; LLM sampling stays stochastic.

## `resume_games.py` — finish interrupted games from snapshots

Long games on slow GPUs outlive SLURM walltimes. Every game with
`game.snapshots: true` writes a full phase-boundary snapshot per public
message (`state.jsonl`), and `MafiaGame.from_snapshot` can restore play
from any of them. This driver walks a logs dir and plays every eligible
unfinished game to completion as a **fork**: a new `game_id` whose `setup`
carries `forked_from` (the parent) and `resume_of`.

```bash
cd src
python resume_games.py --logs ../logs --config ../configs/config_v100_qwen7b.yaml --dry-run
python resume_games.py --logs ../logs --config ../configs/config_v100_qwen7b.yaml \
    --parallel --max-parallel 10 --shard-index $TASK --shard-count $N
```

Eligibility (all must hold): unfinished (no `game_over`), has a usable
snapshot, not already continued (no other game's `forked_from` points at
it), and idle for `--min-idle-sec` (a running job still owns it). Re-runs
are therefore idempotent: a child cut off mid-resume is itself picked up
on the next pass, forming a resume chain. Feedback-arm settings
(`feedback_to_context`, `feedback_order`) are restored per game from the
parent's `setup` record, so one invocation covers mixed-arm log dirs.

**Multilingual mode** (`--lang-map map.json`): the map
`{game_id: {"lang": .., "seed": ..}}` names interrupted seed-grid games.
Only mapped games (and their resume descendants — chains are followed via
`setup.forked_from`) are resumed, each with its own
`configs/hpc_lang/config_lang_<lang>_a100.yaml`, and a finished child is
appended to the language ledger under the original seed — so
`run_lang_games.py` never regenerates that slot. To also stop a *pending*
grid run from regenerating a slot that is being resumed elsewhere, append
a claim record `{"seed": N, "lang": .., "claimed_for_resume": <gid>}` to
the ledger: the driver treats any non-error record as done, and real
records are distinguished later by their `winner` field.

## Remote wrappers

- **`run_lang_batch.sh`** (repo root) — run-kit for a LAN GPU box:
  `sync` rsyncs the repo over, `run <gpu> <N> [langs…]` executes the
  seed-grid driver per language under `CUDA_VISIBLE_DEVICES`, `pull`
  rsyncs traces back into `logs/`.
- **SLURM arrays** — sbatch wrappers live in the experiments repo
  (`hpc/`): array task = (config, seed shard), 16–48 h walltime,
  smoke-first on the `test` partition. The pattern is always
  *idempotent passes*: TIMEOUT is expected, and the next submission of
  the same array continues via the ledger (`run_lang_games.py`) or via
  snapshots (`resume_games.py`).

## Launching from the browser (Arena)

`python src/bus_server.py` starts the model-bus hub with the Arena panel
on `:8765`: it shows connected bus workers, lets you assign a backend to
every seat, writes the generated config to `configs/generated/`, and
launches batches — the same runner path as `main.py`, no YAML editing.
See [multi_model_setup.md](multi_model_setup.md).
