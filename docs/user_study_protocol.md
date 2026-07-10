# Formative User Study Protocol: Error/Deception Localization with the MafiaScope Viewer

Status: ready to run (not yet conducted; the paper claims only that this protocol
exists and is future work — do NOT report results that have not been collected).
Addresses: C-7 / EIC W5 / R3 W1 (formative evaluation of the viewer) and reader
feedback item 4 (2026-07-10 editorial revision).

## 1. Research questions

- RQ1 (efficiency): does the viewer reduce the time researchers need to localize
  the cause of an agent/crowd error in an unfamiliar recorded game, compared with
  working directly on the raw JSONL logs?
- RQ2 (accuracy): does it improve the correctness of the localization?
- RQ3 (usability): which viewer affordances (timeline, impersonate, metrics panel,
  branch) do researchers actually reach for, and where do they stall?

## 2. Design

- Formative, within-subject: each participant performs two localization tasks,
  one with the **viewer** (full UI, branch button disabled to keep sessions
  bounded) and one with **raw logs** (`game.jsonl` + `introspection.jsonl` opened
  in the participant's editor of choice; `grep`/`jq`/Python allowed, viewer
  forbidden).
- N = 5 researchers (LLM-agents / NLP / HCI background; not MafiaScope
  developers; have never seen the two task games). Russian reading ability is
  required for Task B (RU game); recruit accordingly or swap Task B for an EN
  game of comparable length if recruitment fails.
- Tool order and task-to-tool assignment counterbalanced:

  | P | task 1 | tool 1 | task 2 | tool 2 |
  |---|--------|--------|--------|--------|
  | P1 | A | viewer | B | raw |
  | P2 | A | raw | B | viewer |
  | P3 | B | viewer | A | raw |
  | P4 | B | raw | A | viewer |
  | P5 | A | viewer | B | raw |

- Per-task time cap: 12 minutes (answer forced at cap; timeout recorded).
- Session length: 30–40 min total.

## 3. Session script (30–40 min)

1. Intro + consent, no personal data recorded beyond role/experience bracket
   (5 min).
2. Guided warm-up on a game that is NOT a task game (EN demo `0da78714`, 3
   rounds): scrub timeline, open impersonate, open `#metrics` (5 min; warm-up
   happens before both conditions so raw-logs participants also know the data
   model — for the raw condition additionally show the JSONL schema cheat-sheet,
   section 7).
3. Task 1 (max 12 min): timer from task sheet handover to verbal answer.
4. Task 2 (max 12 min): same, other tool.
5. SUS questionnaire on the viewer + 3 open questions (5 min).
6. Debrief (3 min).

Screen + audio recorded with consent; experimenter notes tool actions
(think-aloud encouraged but not enforced).

## 4. Tasks and gold answers

### Task A — crowd error in an unfamiliar EN game (`36594b66-05d1-434c-be65-13360eafca9e`)

Committed EN demo game (round-2 Mafia win; `state.jsonl` present). In round 1
the village eliminates villager Gray 5:1 after a Mafia deflection and pile-on.

Prompt to participant: "In this game the village eliminated one of its own in
round 1. Which single public utterance was most responsible for that
elimination? Name the speaker and the utterance (round.seq is enough)."

Gold key (replay-verified, `docs/replay_experiment.md`;
30 forks): **Finley's pile-on R1.8** (fixing it raises P(eliminated=Gray) from
0.4 to 0.8). Scoring: 2 = Finley R1.8; 1 = Logan R1.7 (the deflection that
seeded it, replay delta +0.2) or "Finley, wrong utterance"; 0 = other.

### Task B — stalled village in a long RU game (`f2510502-8441-439e-9511-884551eda9fd`)

Committed 15-round ablation game (`config_ablation_demand.yaml`, villagers won).
The long duration makes raw-log navigation genuinely hard and the viewer's
timeline/metrics genuinely useful; the probe logs contain rounds in which
villager beliefs already fingered the surviving Mafioso while the vote kept
missing.

Prompt to participant: "Villagers won, but only after 15 rounds. Identify the
longest-surviving Mafia agent, and the earliest round in which at least one
villager privately held a committed, correct belief that this agent was Mafia
while the crowd still failed to eliminate them for at least two further rounds.
Name the agent and that round."

Gold key: derive mechanically BEFORE recruitment and pin it here (do not run
participants against an unpinned key):

```bash
# longest-surviving Mafioso + eliminations timeline
python - <<'EOF'
import json,collections
g=[json.loads(l) for l in open('logs/f2510502-8441-439e-9511-884551eda9fd/game.jsonl')]
# inspect role assignments + elimination events
EOF
# earliest committed correct belief: filter introspection.jsonl role_assessment
# records (repaired parse) for guess==Mafia on the true Mafioso, confidence>=50
```

Scoring: 2 = correct agent + round within ±1 of key; 1 = correct agent, wrong
round; 0 = wrong agent. Two experimenters derive the key independently from the
logs and reconcile; record the reconciled key in this file before session 1.

## 5. Measures

- Time-to-answer per task (s), timeout flag.
- Accuracy per task (0/1/2 per the keys above).
- Stated confidence in the answer (0–100), for exploratory
  confidence-vs-correctness reading.
- Tool-action notes: which views were opened (timeline / impersonate / metrics /
  calibration / log filter), first view that surfaced the answer.
- SUS, 10 standard items on the viewer condition (score 0–100).
- Open questions:
  1. "What did you look for that the interface did not show you?"
  2. "Where did you feel lost, and what got you unstuck?"
  3. "If you could add or remove one panel for this kind of investigation, what
     would it be?"

## 6. Success criteria (formative, pre-registered here)

- S1: median time-to-answer with the viewer <= 60% of raw-logs median.
- S2: mean accuracy with the viewer >= raw-logs mean, with no viewer timeouts.
- S3: SUS >= 68 (published above-average benchmark).
- S4: >= 3/5 participants spontaneously open impersonate mode or the metrics
  panel during their viewer task.
- S5: every session fits 40 min.

Failure of S1/S2 is itself a reportable formative finding (the viewer does not
help for localization); failure of S3/S4 feeds the roadmap (onboarding,
discoverability). None of the criteria gate the demo submission.

## 7. Materials checklist (prepare before session 1)

- [ ] Task sheets A/B (one paragraph each, exactly the prompts above).
- [ ] JSONL schema cheat-sheet (one page: composite keys, `role_assessment` /
      `social_map` record fields, repaired-vs-raw parse fields) — given in BOTH
      conditions so the comparison targets the interface, not schema knowledge.
- [ ] Pinned gold key for Task B (section 4), reconciled by two experimenters.
- [ ] Viewer served locally with exactly the two task games + warm-up game
      (`serve_viewer.py`; branch button disabled).
- [ ] Raw-logs workspace: copies of the two game dirs, editor + `jq` available.
- [ ] Timer, SUS forms, consent forms, recording setup.

## 8. Analysis plan

Descriptive only (n=5, formative): per-condition medians/means, per-participant
deltas, SUS score, coded open-question themes (two coders, reconcile). No
significance testing; report all five participants individually in a table.

## 9. Draft paper paragraph (placeholders — fill only with collected data)

> **Formative evaluation.** Five researchers (LLM-agent/NLP/HCI backgrounds,
> none involved in development) each localized the cause of a crowd error in an
> unfamiliar recorded game twice, once with the viewer and once with raw JSONL
> logs (within-subject, counterbalanced order and task assignment, 12-minute
> cap). With the viewer, the median localization took [T_v] min versus [T_r]
> min on raw logs (accuracy [A_v] vs. [A_r] of 2; [k] timeouts), SUS was
> [S] , and [m]/5 participants reached for impersonate mode or the metrics
> panel unprompted. The most requested additions were [theme-1] and [theme-2],
> which we fold into the roadmap.

## 10. Ethics

Internal formative study of a research tool; participants are colleagues acting
as expert users, no sensitive or personal data collected, recordings deleted
after coding; consent covers recording and anonymous quotation. No IRB filing
anticipated (verify against local institutional policy before running).
