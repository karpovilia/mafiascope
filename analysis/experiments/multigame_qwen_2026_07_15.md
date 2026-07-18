# Werewolf multigame study (idea 02) — engine, smokes, and HPC handoff

Date: 2026-07-15. Engine branch (in `mafia`): `worktree-agent-af2ac9c53e4fa748c`.
Status: **engine + configs + smokes complete and validated; Qwen batches staged
for HPC A100 (not yet generated).** Batches were moved off the local A6000 box to
HSE HPC A100 by decision on 2026-07-15 (feasibility, see §5).

## 1. What this delivers

The Mafia engine now yields a pure Werewolf reskin **from config with zero loop
edits**, reuses the dormant Sheriff as the Seer, and gains a self-contained
Resistance/Avalon-lite module — all without disturbing the existing Mafia traces.
Three new corpora (`ww_skin_qwen`, `ww_seer_qwen`, `resistance_qwen`) are
registered in `docs/corpora.json` as **pending HPC generation** on the shared seed
grid 9000..9049.

## 2. Engine changes (mafia branch, atomic commits)

| commit | change |
|---|---|
| `0df60e2` | skin-from-config: `SKIN_PACKS`/`get_skin_pack` (Werewolf EN pack appended, references existing Mafia tables so multilingual additions flow through); `game.skin`/`game.game_type` recorded in the setup event; Sheriff/Seer check result rendered in skin lexicon while the event log keeps canonical Mafia/Not-Mafia; per-game RNG seed grid (`random.Random(seed)`), unseeded path byte-identical. |
| `27c5c93` | `game_resistance.py`: mission-based group game, **no elimination**; reuses the introspection engine (same probe battery, untouched), event logger, backends; same JSONL layout + composite keys; Spies known only privately, mission cards secret, only aggregate outcome announced. |
| `4ad64dc` | configs: ww_skin / ww_seer / resistance (Qwen) + deepseek EN smokes. |
| `1d6b7d2` | optional `python-dotenv` (bare GPU boxes lack it). |
| `1af9cd9` | opt-in `enable_thinking` backend knob (default unchanged). |
| `e2a06cb` | HPC A100 array + test-partition smoke sbatch; `--max-rounds` CLI; Qwen configs point at the HPC mirror weights and are byte-identical to `config_v100_qwen7b.yaml` on backend + probe battery; `config_mafia_qwen.yaml` base arm added. |

**Design invariant (H1 fidelity):** the machine-readable ACTION/VOTE protocol
tokens (`ACTION: Kill`, `VOTE:`) are held identical across skins on purpose, so
`_ACTION_RE`/`_VOTE_RE` in `state_clustering` and the engine parser are untouched.
Only the narrative frame changes (Mafia→Werewolves, town→village, kill described
as devour, Sheriff→Seer). H1 is therefore a clean test of frame sensitivity with
the protocol fixed.

## 3. Smoke validation (deepseek API, EN) — all pass

| setup | result | evidence |
|---|---|---|
| **Base Mafia (regression)** | PASS | Mafia win, 2 rounds. Trace structurally identical to the pre-refactor engine: kinds `{setup, intro, night_mafia, night_doctor, night_kill, night_analysis, day_discuss, day_vote, vote_tally, day_eliminate, game_over}`; setup carries `game_type=mafia, skin=mafia`. |
| **Werewolf-skin** | PASS | Werewolf lexicon present ("wolves", "village", "lynch"); mechanics identical (a Mafia-role player lynched). `game_type=werewolf_skin`. |
| **Werewolf+Seer** | PASS | Seer (internal role Sheriff) scried a player; **check result reaches only the Seer** — the string "CHECK RESULT: … is Not a Werewolf" appears in the Seer's private context (state.jsonl) and **0 times** anywhere in the public game.jsonl; event log keeps canonical `Not Mafia`. Skin lexicon rendered ("a Werewolf"/"Not a Werewolf"). |
| **Resistance-lite** | PASS | Spy win (3 missions failed). 13 mission-card records, **all flagged private / never broadcast**; `mission_result` announces only aggregate (success + sabotage count + team). No hidden-role leak to the public channel. |

`--max-rounds` + `--no-introspection` (the test-partition smoke invocation)
validated locally: a 1-round game finished in 24 s with no introspection log.

## 4. Hypotheses and analysis plan (run when corpora land)

- **H1 skin-invariance** — paired by seed, `config_mafia_qwen` vs `ww_skin_qwen`:
  minority (Mafia/Werewolf) win rate (Wilson/Jeffreys, paired McNemar on shared
  seeds) and the 16-event profile. Difference ⇒ frame sensitivity finding.
- **H2 mechanics>skin** — `ww_skin_qwen` vs `ww_seer_qwen`: expect lower `NO_INFO`
  share and lower belief flip rate, higher village win rate once the village has
  the Seer channel. Belief dynamics via `belief_dynamics.py`.
- **H3 vocabulary transfer** — label all three corpora with the frozen 16-event
  vocabulary (`event_labeler.py`, deepseek judge, unchanged): report the `OTHER`
  fraction per game_type (<5% target). Manually read top `OTHER` sentences; for
  the Seer games watch for systematic claim/counter-claim clusters
  (`CLAIM_ANNOUNCEMENT`/`COUNTER_CLAIM` candidates). Resistance recall-by-round
  needs separate normalization (nobody dies) — reported apart from the shared
  Mafia metrics, per the idea's pitfall note.

Pipeline is ready: `state_clustering.py --unit sentence` (filter game_ids by
`game_type` from the setup record) → `event_labeler.py` → `aggregate_events.py`.

## 5. Why the batches moved to HPC A100

On the allotted A6000 box (`supermicro-gpu`) the root disk was 100 % full, so the
run was forced entirely into `/dev/shm` (repo + a RAM copy of the weights + all
caches via `TMPDIR`/`TRITON_CACHE_DIR`/`HF_HOME`) — that path was validated
(Qwen3-8B loads and generates). The blocker was throughput, not disk: the mandated
probe battery fires after **every** public message and each probe is a **blocking,
sequential** call within a game, so with Qwen3 thinking on one game costs ≈2 GPU-h
and 50 games/arm is a multi-GPU-day job on a single card. Per the 2026-07-15
decision the batches run on HPC A100 instead; the local box was released (my
processes stopped, `/dev/shm` cleaned, GPUs idle, no foreign jobs touched).

## 6. HPC run (staged; submit commands in the handoff)

- Weights: `/home/iakarpov/hf_mirror/qwen_Qwen3-8B` (configs reference this).
- Env: conda `mafia_llm`, `HF_HUB_OFFLINE=1`.
- Test smoke (partition `test`, 25 min, probes off, 2 rounds): per config.
- Production array (partition `rocky`, `type_e`, `--gpus=1`, no `--mem`):
  `--array=0-14%5` over `src/run_hpc_a100_array.sbatch` = 3 setups × 5 tasks ×
  10 games = 50/setup on seeds 9000..9049.
