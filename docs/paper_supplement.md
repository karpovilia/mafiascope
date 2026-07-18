# MafiaScope Paper Supplement

Extended tables and analysis details moved out of the EMNLP 2026 demo paper appendices
(2-page appendix limit). Section names mirror the former appendix structure. All numbers
were produced by the released analysis code (`src/metrics_lib.py` and companions);
provenance markers reference the same recomputation runs as the paper source.

## S1. Extended parse-rate and missingness accounting (formerly Appendix B)

Pinned 32-game corpus (corpus32): of 20,199 probe calls, 13,815 returned parsed answers.

- 98.3% of the 6,384 missing answers (6,277/6,384) are API transport errors that never
  delivered a model answer; 99.2% of delivered answers parse.
- The loss is orthogonal to role (cluster permutation p=0.72) and to answer content,
  concentrating in early rounds of a subset of runs.
- JSON repair keeps only the complete prefix of a truncated answer and writes nothing
  itself. On corpus32 it recovers zero answers. On the 30-game legacy batch it recovers
  4,118 answers (4,234 when the 2 pilot games are included; the paper reports the
  30-game figure).
- Low-loss stratum robustness: the 10 games with near-zero probe loss reproduce every
  headline number within the full-corpus CIs.
- Cross-model replication (f1_f5 replication audit, 2026-07-18): transport loss is not
  provider-specific. 17.7% of gpt-4o-mini probe calls (4,354/24,587) also failed in
  transit over the same network path (connection reset / proxy), accounting for 93.5%
  of that corpus's losses and touching 26 of 28 games. Content-level refusals do exist
  there: 303 delivered answers (1.5%) are refusals, of which 299 sit on a single probe,
  `personality_profile`; game-relevant probes (role_assessment, suspicion_ranking,
  social_map) parse at ~100% of delivered answers.
- Probe-call volume per game: 631 (corpus32), 784 (legacy), 878 (gpt-4o-mini); the
  probe-to-game-call multiplier is ~24x on all three corpora.

## S2. Full calibration tables (formerly Appendix C, F2)

Methodology: repaired mode, fixed-width confidence bins of width 20 with confidence 100
folded into the top bin, cluster bootstrap over games B=1000.

corpus32 bins (fresh role-assessment guesses):

| Confidence bin | Accuracy | n |
|---|---|---|
| 0-19 | (too small to interpret) | 4 |
| 20-39 | 46.4% | 360 |
| 40-59 | 43.0% | 2,320 |
| 60-79 | 46.0% | 3,338 |
| 80-99 (100 folded in) | 54.6% | 994 |

- corpus32: ECE 0.168 (CI [0.130, 0.203]), Brier 0.283. Top-bin mean confidence 85.2
  (gap -30.6 pp).
- Language arms: ECE 0.150 (CI [0.109, 0.184]) in the 21 English games, 0.189
  (CI [0.119, 0.256]) in the 11 Russian ones; both miscalibrated in the same direction.
- Legacy 30-game batch: ECE 0.222 (CI [0.171, 0.279]), Brier 0.290; top-bin (80-99,
  n=5,977) accuracy 62.5% at mean confidence 87.7 (gap -25.2 pp).
- gpt-4o-mini 28 games: ECE 0.239 (CI [0.155, 0.327]), Brier 0.317; top-bin (n=4,448)
  accuracy 53.1% at mean confidence 85.4 (gap -32.3 pp).

## S3. F3 second-order details (formerly Appendix C)

Wording ablation. The legacy social-map template contained a suggestive sentence, "if
you suspect someone, they may sense it", so the probe could have implanted the very
belief it measures. Two matched Russian batches of 5 games differing only in that
sentence: the over-prediction appears under both wordings, 1.58 (CI [1.46, 1.77]) with
the sentence and 1.48 (CI [1.39, 1.61]) without it, and in the English batch as well
(1.45, CI [1.18, 1.72]). The effect is not an artefact of the wording; whether the
sentence added a little on top cannot be judged from 5 games per wording (overlapping
intervals). The case-study corpus uses the clean wording throughout.

Replication of the over-prediction ratio (overall / innocents / Mafia):

| Corpus | overall | innocents | Mafia |
|---|---|---|---|
| corpus32 | 1.53 [1.44, 1.64] (n=15,638 pairs) | 1.84 [1.67, 2.04] | 1.08 [0.94, 1.25] |
| legacy (main30) | 1.63 [1.53, 1.74] (n=26,664) | 2.38 [2.07, 2.88] | 1.04 [0.95, 1.14] |
| gpt-4o-mini (28) | 1.93 [1.76, 2.17] (n=19,870) | 2.27 [1.84, 3.03] | 1.45 [1.19, 1.84] |

Agreement vs. trivial majority baseline: corpus32 +10.1 pp (CI [5.7, 12.4]);
legacy -4.4 pp (CI [-8.2, -0.1], significantly below); gpt-4o-mini -1.7 pp
(CI [-6.2, +2.4], zero inside the interval, so "no longer beats the baseline").
The +10.1 pp advantage over majority is corpus-specific; the ratio and its role split
replicate everywhere (in weakened form on gpt-4o-mini, where Mafia is no longer near 1).

Threshold grid (ratio): 1.32 [1.21, 1.41] at threshold 30, 1.53 at 50, 3.05
[2.59, 3.82] at 70; smallest at the most liberal threshold, so no
compressed-denominator artefact. Input control: on pairs with a confident own
assessment (70+, matching Mafia's knowing input) innocents over-predict at 1.95
[1.73, 2.21] vs Mafia 1.24 [1.08, 1.48]; the confident Mafia ratio edges above 1.
The agreement grid itself (thresholds 30/50/70: 48.2/54.7/70.3 vs. majority
47.5/44.7/78.1) remains in the paper (Table 3).

## S4. F4 vote-coupling details (formerly Appendix C)

Innocent voters, probes strictly before the vote; chance = uniform vote over alive
others. CIs from the released code (2026-07-18 alignment).

- Top-1: 64.9% (48/74; chance 27.3%; CI [54.7, 76.7]). Post-vote probes match the vote
  in 95.1% (77/81).
- Committed-Mafia set: 71.2% (57/80; CI [62.5, 79.1]; chance 41.7% = set size over
  alive others).
- Mafia: follows its stated ranking as often as innocents (70.0%, n=30), but
  committed-set alignment stays at chance: 29.6% vs. 25.2% (n=27, CI [14.3, 46.2]),
  partner-shielding. On gpt-4o-mini this decoupling inverts: Mafia committed-set 83.7%
  (41/49) vs. chance 27.9%.
- Transcript baselines: most-accused 58.8% (47/80) on corpus32; probe increment not
  separable from zero there (McNemar p=0.66 at n=68 shared votes). Legacy batch: probe
  85.6% vs. strongest heuristic 78.1% (n=201, p=0.024). gpt-4o-mini: top-1 79.4%
  (n=214, chance 27.1%), increment +3.4 pp (82.0 vs. 78.5, n=205, p=0.016). Mafia
  voters show no increment on any corpus (p=0.69/1.0 corpus32; 0.078/0.13 legacy;
  0.38/1.0 gpt).

## S5. Test-retest and belief-dynamics details (formerly Appendix B)

- Suspicion volatility: mean L1 shift between consecutive suspicion vectors on common
  support, death-forced flips excluded from the flip rate. corpus32 average 0.300
  (CI [0.284, 0.318]) against a test-retest floor of 0.219.
- Flip-rate floor: 34.8% (CI [23.9, 47.1]) from 40 frozen-context points probed 5
  times each over the same API path, reweighted to the corpus's pair composition;
  observed flip rate 48.7% (CI [45.1, 52.8], n=2,583 consecutive pairs).
- 51.6% of flips return to a previously abandoned suspect.
- Round structure: floor 0.65 in round 0 (near-uniform suspicion makes the top pick
  arbitrary), 0.21 in round 1; observed mid-game flip rates 0.46-0.48, so real
  movement concentrates in mid-game.
- The legacy corpus's Mafia-instability asymmetry does not replicate on corpus32
  (0.312 vs. 0.294 volatility, overlapping CIs).

## S6. F1 replication details (f1_f5 replication audit, 2026-07-18)

- Legacy batch (main30): the shape replicates fully. Unknown in round 0: 81.8%
  [78.4, 85.4]; recall of the true Mafia 4.3% -> 44.6% -> 61.6% by round 2 (above
  chance 33.3 from round 1); committed accuracy rises monotonically 46.0 -> 63.4.
- gpt-4o-mini (28 games): the no-opinion start replicates even more strongly (84.7%
  Unknown in round 0, still 50.1% in round 1), but there is no convergence on the
  Mafia: recall rises 3.0 -> 23.0 (r1) -> 25.9 (r2) and plateaus below the
  alive-Mafia chance share (r1: 23.0 vs. chance 37.1; r2: 25.9 vs. 30.4; r3: 24.3
  vs. 32.8). Committed accuracy declines after round 1 (55.6 -> 50.2 -> 34.2 -> 21.7;
  late rounds have small n and wide CIs). gpt games run longer (up to round 5); Mafia
  won 18/28.
- corpus32 paper numbers (Table 2 of the paper) are reproduced by the current code to
  the first decimal.

## S7. Replay (pivotal-utterance) experiment details (formerly Appendix E)

Design: branch N times directly before a candidate utterance (PRE arm: the speaker
rerolls it, a draw from its own counterfactual utterance distribution) and directly
after it (POST arm: the utterance is fixed); 3 utterances x 2 arms x 5 rerolls = 30
forks, suspicion-only probes. The POST arm's within-arm variance serves as the
resampling noise floor. Vote-time suspicion is the mean normalized rank of the player
over suspicion-ranking probes in the fork round's vote phase (1 = most suspicious).

Running-example game `36594b66`: villager Gray eliminated 5:1 (vote-time suspicion
0.96; Mafia won in round 2) after Logan's blame-shift onto Gray (R1.7), Finley's
joining accusation (R1.8), Casey's endorsement (R1.9).

| | R1.7 | R1.8 | R1.9 |
|---|---|---|---|
| Speaker | Logan (M) | Finley (M) | Casey |
| P(Gray elim.) PRE | 0.6 | 0.4 | 0.8 |
| P(Gray elim.) POST | 0.8 | 0.8 | 0.8 |
| Delta susp. (Gray) | +0.10 | +0.18 | -0.00 |
| Delta susp. (Logan) | -0.04 | -0.11 | +0.09 |
| P(Mafia win) PRE/POST | .8/.8 | .8/.8 | 1/1 |

Delta = POST - PRE. None of the contrasts is statistically significant at these sample
sizes: the largest (R1.8, 2/5 vs. 4/5 eliminations) has Fisher exact two-sided
p~0.52; no interval on the arm difference excludes zero; 3 utterances x 4 outcome
measures are inspected without multiplicity correction. Comparisons of pivotality
between utterances carry a fork-position confound: a later fork leaves less
stochasticity before the vote, so PRE probabilities at different timeline positions
are not aligned. The table documents an end-to-end run of the attribution workflow; it
suggests, but does not establish, R1.8's pivotality. All 30 forks ship as replayable
logged games. The viewer's branch dialog (formerly the Appendix E figure,
`figures/16-branch-dialog.png` in the paper repo) forks any timeline step of a
snapshotted game into N replays server-side.

## S8. User study protocol

The formative user study protocol (within-subject, N=5 external researchers, two
error-localization tasks, viewer vs. raw JSONL logs, counterbalanced, 12 minutes per
task; measures: time to localization, correctness against gold labels, SUS plus three
open questions) ships as `docs/user_study_protocol.md` in this repository. Designed,
not yet run.
