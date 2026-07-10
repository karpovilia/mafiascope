# Pivotal-Utterance Attribution via Counterfactual Replay

Status: pilot completed 2026-07-10 (see results below).
Script: `src/replay_experiment.py`. Results JSON: `replay_pilot_36594b66.json` (this directory).

## Question

How much did a single utterance U (e.g., a Mafia player's first accusation) causally
contribute to the day-vote outcome and to the group's suspicion distribution?

## What the fork API supports (M1–M2) and what it does not

Implemented (`src/replay.py`, `MafiaGame.from_snapshot` in `src/game.py`):

- Full-state snapshots in `logs/<game_id>/state.jsonl`, one per public message,
  keyed by `(round, msg_seq)`. A snapshot is written **after** message `s` has been
  broadcast to all players and probed; `pending` stores the resume point.
- `from_snapshot(cfg, parent_dir, round, msg_seq)` restores every player's full
  conversation context and continues from the step **after** the snapshot with fresh
  LLM sampling ("the snapshot restores the information state, not the randomness").
- `run_fork_batch(...)` runs N continuations (optionally in parallel threads), each
  logged as a normal game under `logs/<new_id>/` with `forked_from` / `fork_point` /
  `fork_batch_id` / `replica_idx` metadata (picked up by the viewer as branches).

**Not implemented (M3–M4):** interventions — `edit_utterance`, `inject_belief`,
`override_night_action`. The `intervention` field in fork metadata is a placeholder
(`None`). Consequently we **cannot** hold the rest of the game fixed while replacing U
with a controlled alternative; a fork is always a "reroll from here".

## Design: reroll-variance attribution

For utterance U at `(r, s)` we run two fork arms of N rerolls each:

- **PRE arm** — fork at the snapshot immediately preceding `(r, s)` (normally
  `(r, s-1)`): the speaker resamples their utterance, so U is replaced by a draw from
  the speaker's counterfactual utterance distribution; everything downstream is
  resampled too.
- **POST arm** — fork at `(r, s)`: U is fixed in every player's context (it was
  broadcast before the snapshot); everything downstream is resampled.

Pivotality of U w.r.t. a metric m is the arm contrast:

    pivotality(U, m) = E[m | POST] − E[m | PRE]

The POST arm's within-arm variance is the resampling noise floor; PRE−POST differences
must exceed it to be attributable to U. Metrics per replica (extracted from the fork's
`game.jsonl` / `introspection.jsonl`):

1. **Fork-round vote outcome** — vote tally and eliminated player of round `r`
   (e.g., P(eliminated = Gray)).
2. **Vote-time suspicion** — mean normalized suspicion rank per player over all
   `suspicion_ranking` probes in the `day_vote` phase of round `r` (rank normalized to
   [0,1], 1 = most suspicious; self-rankings excluded).
3. **Game outcome** — winner and number of rounds.

The factual (parent) branch is extracted with the same code and reported for reference.

### Honest limitations

- **This is not a clean single-utterance intervention.** The PRE arm resamples U from
  the same speaker in the same information state, so the contrast estimates the effect
  of the *realized* U relative to the speaker's *typical* utterance at that point — not
  relative to silence or to a specific alternative. If the speaker (here: Mafia Logan)
  reliably produces some deflection at `(r, s)`, pivotality of the specific wording is
  diluted. M3 `edit_utterance` would fix this; until then this is the strongest design
  M1–M2 supports.
- With small N the contrast is noisy; N ≥ 5 per arm is the minimum for the paper, and
  elimination probabilities should be reported with binomial CIs.
- Forks inherit non-determinism end-to-end (votes, night kills), so downstream metrics
  (winner) mix U's effect with later branching; the round-`r` vote metrics are the
  cleanest attribution target.
- Probes are passive (read players' contexts, never write back), so pruning the probe
  battery in forks does not change game dynamics — only measurement density. The
  script defaults to `--probes suspicion` (suspicion_ranking only, ≈5× cheaper);
  `--probes full` reproduces the parent battery, `--probes none` drops introspection
  (loses metric 2).

## Script

`src/replay_experiment.py` (run from `src/`, venv `../.venv`):

```
python replay_experiment.py --game <game_id> \
    -u R.S [-u R.S ...]        # utterance(s) as round.msg_seq, e.g. 1.7
    -n <rerolls per arm> \
    -c ../configs/config_en_demo.yaml \
    [--probes suspicion|full|none] [--target <player>] [--parallel] \
    [--dry-run]                # print API-call estimate only, no LLM traffic
    -o out.json
```

Output JSON: per-utterance `{utterance, pre_fork, post_fork, factual, pre, post,
delta_post_minus_pre}` with per-replica records (`game_id` of each fork, vote tally,
eliminated, winner, suspicion table) and per-arm aggregates.

## Pilot (game `36594b66`, EN demo corpus, DeepSeek)

Parent game: 7 players, Logan+Finley Mafia; R1 night kills Jordan; in the R1
discussion Logan (`R1.7`) is the first to redirect suspicion onto Gray ("Gray's
already trying..."); Gray is eliminated 5:1; Mafia wins on round 2 by parity.
Factual round-1 metrics: eliminated = Gray, vote-time suspicion(Gray) = 0.96 (most
suspicious), winner = Mafia.

Pilot command (1 utterance × 2 arms × 1 reroll = 2 forks, suspicion-only probes):

```
python replay_experiment.py --game 36594b66-05d1-434c-be65-13360eafca9e \
    -u 1.7 -n 1 --target Gray \
    -o ../docs/replay_pilot_36594b66.json
```

Measured cost: POST fork 74 API calls (12 speech/vote/night + 62 suspicion probes);
PRE fork 182 calls because its branch survived to round 4 instead of 2 — **256 calls
total**. The `--dry-run` estimate (~134) assumes forks are as long as the parent
continuation; branches that live longer cost proportionally more.

### Pilot result

Harness worked end-to-end: both forks completed, branch logs written under
`logs/<fork_id>/` (PRE `0c6a5ddf`, POST `987ae95f`), metrics extracted, JSON written.

| arm | fork @ | eliminated (R1) | suspicion(Gray) | suspicion(Logan) | winner |
|-----|--------|-----------------|-----------------|------------------|--------|
| factual | — | Gray | 0.96 | 0.55 | Mafia (R2) |
| PRE (n=1) | R1.6 | **Logan** | 0.72 | **0.85** | Mafia (R4) |
| POST (n=1) | R1.7 | Gray | 0.96 | 0.58 | Mafia (R2) |

Delta (POST − PRE): P(eliminate Gray) 0 → 1; suspicion(Gray) +0.23;
suspicion(Logan) −0.27; winner unchanged (Mafia both ways, but the PRE branch needed
4 rounds after losing Logan on day 1).

With n=1 per arm this is smoke-test evidence, not an attribution estimate — but the
single PRE reroll is suggestive: when Logan's actual deflection onto Gray is
resampled, the R1 vote flipped to eliminating Logan himself, i.e., the realized
utterance plausibly saved its speaker and doomed Gray. The full run (N ≥ 5 per arm)
is needed to turn this anecdote into a distributional claim with CIs.

## Full run: command and cost

Proposed full experiment on `36594b66` — three candidate pivotal utterances of the R1
discussion (Logan's deflection `1.7`, Finley's pile-on `1.8`, Casey's independent
endorsement `1.9`), N = 5 rerolls per arm:

```
cd src && ../.venv/bin/python replay_experiment.py \
    --game 36594b66-05d1-434c-be65-13360eafca9e \
    -u 1.7 -u 1.8 -u 1.9 -n 5 --target Gray --parallel \
    -o ../docs/replay_full_36594b66.json
```

Cost (calibrated by the pilot): 74–182 API calls per fork with `--probes suspicion`
(74 when the branch ends on round 2 like the parent, ~180 when it survives to round
4) → 3 utterances × 2 arms × 5 rerolls = **30 forks ≈ 2,200–5,500 API calls,
realistically ~3,500** on deepseek-chat. With `--probes full` multiply probe traffic
by ≈5 (~15,000 calls) — not recommended while other experiments share the API budget.
Do **not** launch the full run while the 9 background games are still running.

## Full run: results (2026-07-10, n=5 per arm, 30 forks, 0 errors)

Output: `replay_full_36594b66.json`. All 30 forks completed as normal logged
games (fork dirs carry `forked_from` metadata and are excluded from corpus
metrics by `metrics_lib.select_corpus`).

| U | speaker | P(elim=Gray) PRE→POST | Δ | Δ suspicion(Gray) | Δ suspicion(Logan) | P(Mafia win) PRE/POST |
|---|---------|----------------------|---|-------------------|--------------------|-----------------------|
| R1.7 | Logan (Mafia, deflection) | 0.6 → 0.8 | +0.2 | +0.10 | −0.04 | 0.8 / 0.8 |
| R1.8 | Finley (pile-on) | 0.4 → 0.8 | **+0.4** | +0.18 | −0.11 | 0.8 / 0.8 |
| R1.9 | Casey (endorsement) | 0.8 → 0.8 | 0.0 | −0.00 | +0.09 | 1.0 / 1.0 |

Reading:

* **The most pivotal utterance for Gray's elimination is Finley's R1.8 pile-on**,
  not Logan's R1.7 deflection that seeded it: with R1.8 in context Gray is
  eliminated in 4/5 branches; with Finley's message resampled the vote scatters
  (Gray 2, Logan 2, no-lynch 1) — in two of five branches the village lynches
  the mafioso instead.
* The pilot's dramatic n=1 flip at R1.7 (PRE branch eliminated Logan) does
  **not** survive n=5: PRE still eliminates Gray in 3/5 branches. This is
  exactly why the screencast script's segment-6 guard note demands checking
  against the full run — use the R1.8 numbers, phrased distributionally.
* Pivotality here is *local* (who dies in round 1), not global:
  P(Mafia win) is 0.8–1.0 in every arm — on this corpus DeepSeek mafia wins
  regardless of the first lynch, so utterance-level attribution should be
  reported against the elimination distribution, not the game outcome.
