#!/usr/bin/env python3
"""Compare the second-order-feedback arm against the EN observational baseline.

Arms (selected from game.jsonl setup events, replay forks excluded):

* ``feedback`` — games whose setup carries ``"feedback_to_context": true``
  (configs/config_en_feedback.yaml; the agent's own latest probe answers are
  fed back into its action prompts — invasive by design, see
  docs/reviews/2026_07_10_r2/feedback_arm.md).
* ``en_base``  — the EN observational corpus: en_demo (2026-07-10 batch) plus
  any later config_en_demo.yaml batches ("en-v2", seeds 201+).  Selector:
  EN-language probe battery (no Cyrillic in probe questions), no feedback
  flag.  The RU corpora and the RU ablation_demand arm are excluded by the
  language test.

Metrics (per arm + delta):

* Mafia win rate  — completed games; Wilson 95% CI + Jeffreys posterior
  (``metrics_lib.binomial_estimates``).
* mean rounds     — completed games; game-level bootstrap CI.
* F1 first-order accuracy — villager-side committed-guess accuracy pooled
  over all rounds; game-level cluster bootstrap CI.
* F3 over-prediction ratio — predicted vs actual "suspects"
  (``second_order_pairs`` canonical rule: Mafia–Mafia pairs excluded,
  confidence threshold 50); game-level cluster bootstrap CI.

Usage:
    python compare_feedback.py [--logs-dir ../logs] [--boot 1000]
                               [--mode repaired|raw] [--seed 0]
"""
from __future__ import annotations

import argparse
import re
from typing import Sequence

import metrics_lib as M

CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")


def is_english(g: M.Game) -> bool:
    """EN battery = no Cyrillic in any probe question (RU corpora and the
    RU ablation_demand arm all have Cyrillic questions)."""
    return bool(g.probes) and not any(CYRILLIC.search(r.get("question") or "")
                                      for r in g.probes)


def select_arms(games: Sequence[M.Game]) -> tuple[list[M.Game], list[M.Game]]:
    eligible = [g for g in games if g.probes and g.forked_from is None]
    feedback = [g for g in eligible if g.feedback_to_context]
    en_base = [g for g in eligible if not g.feedback_to_context and is_english(g)]
    return feedback, en_base


# ── stat helpers ─────────────────────────────


def stat_mean(sample: Sequence[float]) -> float | None:
    return sum(sample) / len(sample) if sample else None


def stat_f1_overall(sample: Sequence[M.FirstOrderCells]) -> float | None:
    """Committed-guess accuracy pooled over all rounds of the resample."""
    num = den = 0.0
    for c in sample:
        for a, b in c.acc.values():
            num += a
            den += b
    return num / den if den else None


def fmt_ci(ci: tuple[float, float] | None, prec: int = 3) -> str:
    if ci is None:
        return "[n/a]"
    return f"[{ci[0]:.{prec}f}, {ci[1]:.{prec}f}]"


def fmt(x: float | None, prec: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{prec}f}"


# ── per-arm report ───────────────────────────


def arm_stats(games: list[M.Game], mode: str, n_boot: int, seed: int) -> dict:
    done = [g for g in games if g.completed]
    k_mafia = sum(1 for g in done if g.winner == "Mafia")
    win = M.binomial_estimates(k_mafia, len(done))

    rounds = [float(g.final_round) for g in done if g.final_round is not None]
    rounds_mean = stat_mean(rounds)
    rounds_ci = M.bootstrap_ci(rounds, stat_mean, n_boot, seed) if rounds else None

    cells = [M.first_order_cells(g, mode) for g in games]
    f1 = stat_f1_overall(cells)
    f1_ci = M.bootstrap_ci(cells, stat_f1_overall, n_boot, seed)

    pairs_per_game = [M.second_order_pairs(g, mode) for g in games]
    so = M.second_order_summary([p for ps in pairs_per_game for p in ps])
    aggs = [M.so_aggregate(ps) for ps in pairs_per_game]
    ratio_ci = M.bootstrap_ci(aggs, M.stat_so_ratio, n_boot, seed)

    return dict(
        n_games=len(games), n_completed=len(done), win=win,
        rounds_mean=rounds_mean, rounds_ci=rounds_ci,
        f1=f1, f1_ci=f1_ci, f1_n=int(sum(b for c in cells for _, b in c.acc.values())),
        so=so, ratio_ci=ratio_ci,
    )


def print_arm(label: str, s: dict) -> None:
    w = s["win"]
    print(f"\n── {label} ──────────────────────────────")
    print(f"  games: {s['n_games']} ({s['n_completed']} completed)")
    print(f"  Mafia win rate : {fmt(w['rate'])}  ({w['k']}/{w['n']})"
          f"  Wilson95 {fmt_ci(w['wilson95'])}"
          f"  Jeffreys mean {w['post_mean']:.3f} cred95 {fmt_ci(w['cred95'])}")
    print(f"  mean rounds    : {fmt(s['rounds_mean'], 2)}  boot95 {fmt_ci(s['rounds_ci'], 2)}")
    print(f"  F1 accuracy    : {fmt(s['f1'])}  (n committed guesses = {s['f1_n']})"
          f"  boot95 {fmt_ci(s['f1_ci'])}")
    so = s["so"]
    if so.get("n"):
        print(f"  F3 pairs       : n={so['n']}  accuracy={so['accuracy']:.3f}"
              f"  (majority baseline {so['majority_baseline']:.3f})")
        print(f"  F3 over-pred.  : {fmt(so['suspects_ratio'])}"
              f"  (pred {so['pred_suspects']} / actual {so['actual_suspects']} 'suspects')"
              f"  boot95 {fmt_ci(s['ratio_ci'])}")
    else:
        print("  F3             : no scoreable pairs")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--logs-dir", default=M.default_logs_dir())
    ap.add_argument("--boot", type=int, default=1000, help="bootstrap resamples")
    ap.add_argument("--mode", default="repaired", choices=("repaired", "raw"),
                    help="answer parse mode (default: repaired)")
    ap.add_argument("--seed", type=int, default=0, help="bootstrap RNG seed")
    args = ap.parse_args()

    games = M.load_games(args.logs_dir, tolerant=True)
    feedback, en_base = select_arms(games)

    print(f"logs dir: {args.logs_dir}   parse mode: {args.mode}   boot: {args.boot}")
    print(f"feedback arm: {len(feedback)} games "
          f"({[g.game_id[:8] for g in feedback]})")
    print(f"EN baseline (en_demo + later en batches): {len(en_base)} games")

    fb = arm_stats(feedback, args.mode, args.boot, args.seed) if feedback else None
    base = arm_stats(en_base, args.mode, args.boot, args.seed) if en_base else None

    if fb:
        print_arm("feedback arm (feedback_to_context=true)", fb)
    else:
        print("\nno feedback-arm games found")
    if base:
        print_arm("EN baseline", base)
    else:
        print("\nno EN baseline games found")

    if fb and base:
        print("\n── deltas (feedback − baseline; CIs above are per-arm, "
              "not for the difference) ──")
        for name, key in (("Mafia win rate", None), ("mean rounds", "rounds_mean"),
                          ("F1 accuracy", "f1")):
            a = fb["win"]["rate"] if key is None else fb[key]
            b = base["win"]["rate"] if key is None else base[key]
            print(f"  {name:15s}: "
                  + ("n/a" if a is None or b is None else f"{a - b:+.3f}"))
        ra, rb = fb["so"].get("suspects_ratio"), base["so"].get("suspects_ratio")
        print("  F3 over-pred.  : "
              + ("n/a" if ra is None or rb is None else f"{ra - rb:+.3f}"))


if __name__ == "__main__":
    main()
