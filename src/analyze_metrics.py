#!/usr/bin/env python3
"""Evaluation metrics for the MafiaScope paper, computed from logs/*/introspection.jsonl.

Revision-aware version (review round 1, P1-1/P1-2/P1-6/P1-7/P1-11, P2-1/P2-7/P2-9):

* reports every metric in TWO parse modes — ``raw`` (the stored
  ``answer_parsed`` fields, i.e. exactly the originally published numbers,
  with their 26.9% role_assessment parse rate) and ``repaired`` (re-parse
  of ``answer_raw`` through the same JSON-repair pass the paper's F4
  describes);
* canonical F3 scoring excludes Mafia-Mafia pairs and comes with a
  sensitivity grid over the confidence threshold (30/50/70) and the
  staleness window of the matched role_assessment (none / <=1 round);
* 95% game-level cluster bootstrap CIs (default 1000 resamples) for the
  key quantities, with n reported for every round and every bin;
* ECE + Brier for calibration;
* full corpus audit (which games, completed, winner, pilot/aborted) and a
  proposed inclusion criterion;
* probing cost (calls, latency, size);
* logs path is a CLI argument, defaulting to <repo>/logs.

All metric definitions live in metrics_lib.py (shared, reusable).
"""
from __future__ import annotations

import argparse
import sys

import metrics_lib as M


def pct(x: float | None, digits: int = 1) -> str:
    return "  n/a" if x is None else f"{x * 100:.{digits}f}%"


def ci_str(ci: tuple[float, float] | None, digits: int = 1) -> str:
    if ci is None:
        return "[CI n/a]"
    return f"[{ci[0] * 100:.{digits}f}, {ci[1] * 100:.{digits}f}]"


def hdr(title: str, ch: str = "=") -> None:
    print(f"\n{ch * 4} {title} {ch * 4}")


# ────────────────────────────────────────────
#  Corpus audit (P1-7)
# ────────────────────────────────────────────

def print_audit(games: list[M.Game]) -> None:
    hdr("CORPUS AUDIT (P1-7)")
    print(f"log directories with game.jsonl: {len(games)}")
    print(f"{'game':10s} {'batch':6s} {'done':5s} {'winner':10s} {'steps':>5s} {'probes':>6s}  battery")
    for row in M.audit_corpus(games):
        battery = ",".join(p[:4] for p in row["battery"]) or "-"
        print(f"{row['game_id'][:8]:10s} "
              f"{'main' if row['main_batch'] else 'other':6s} "
              f"{'yes' if row['completed'] else 'NO':5s} "
              f"{str(row['winner']):10s} "
              f"{row['n_game_records']:5d} {row['n_probe_records']:6d}  {battery}")

    with_probes = M.select_corpus(games, "paper32")
    main = [g for g in with_probes if g.is_main_batch]
    other = [g for g in with_probes if not g.is_main_batch]
    no_probes = [g for g in games if not g.probes]
    en_demo = M.select_corpus(games, "en_demo")
    ablation = M.select_corpus(games, "ablation_demand")
    ru_clean = M.select_corpus(games, "ru_clean")
    forks = [g for g in games if g.forked_from is not None]

    def wins(gs):
        done = [g for g in gs if g.completed]
        maf = sum(1 for g in done if g.winner == "Mafia")
        return len(done), maf

    def outcome_line(gs):
        """Mafia win rate with Wilson CI + Jeffreys credible interval, mean rounds with bootstrap CI."""
        done = [g for g in gs if g.completed]
        if not done:
            return "no completed games"
        maf = sum(1 for g in done if g.winner == "Mafia")
        e = M.binomial_estimates(maf, len(done))
        rounds = [g.final_round for g in done if g.final_round is not None]
        r_ci = M.bootstrap_ci(rounds, lambda s: sum(s) / len(s) if s else None, 1000, 0)
        return (f"Mafia {maf}/{len(done)} = {e['rate']*100:.0f}% "
                f"Wilson95 [{e['wilson95'][0]*100:.0f}, {e['wilson95'][1]*100:.0f}] "
                f"Jeffreys mean {e['post_mean']*100:.0f}% cred95 [{e['cred95'][0]*100:.0f}, {e['cred95'][1]*100:.0f}]; "
                f"rounds mean {sum(rounds)/len(rounds):.1f} CI [{r_ci[0]:.1f}, {r_ci[1]:.1f}]")

    print("\n-- accounting --")
    d, m = wins(with_probes)
    print(f"games with probes (paper's '32 games'): {len(with_probes)}, "
          f"probe records {sum(len(g.probes) for g in with_probes)}, "
          f"completed {d}, Mafia won {m}  -> '18 of 31 completed' for this set")
    d, m = wins(main)
    print(f"main batch (single setup ts {M.MAIN_BATCH_TS}): {len(main)} games, "
          f"probe records {sum(len(g.probes) for g in main)}, "
          f"completed {d}, Mafia won {m}  -> paper's 'Mafia won 17 of 30' matches THIS set")
    print(f"  outcomes: {outcome_line(main)}")
    for g in other:
        print(f"pilot/other: {g.game_id[:8]} setup_ts {g.setup_ts:.0f}, "
              f"{'completed, winner ' + str(g.winner) if g.completed else 'ABORTED (no game_over)'}, "
              f"{len(g.probes)} probe records, battery {','.join(g.battery)}")
    for g in no_probes:
        print(f"no probes (excluded everywhere): {g.game_id[:8]} "
              f"setup_ts {g.setup_ts:.0f}, {g.n_game_records} game records")
    d, m = wins(en_demo)
    print(f"en_demo corpus (2026-07-10, excluded from RU corpora): {len(en_demo)} games, "
          f"completed {d}, Mafia won {m}, "
          f"probe records {sum(len(g.probes) for g in en_demo)}")
    d, m = wins(ablation)
    print(f"ablation_demand corpus (2026-07-10, excluded from RU corpora): {len(ablation)} games, "
          f"completed {d}, Mafia won {m}, "
          f"probe records {sum(len(g.probes) for g in ablation)}")
    d, m = wins(ru_clean)
    print(f"ru_clean corpus (2026-07-10, matched arm for ablation_demand): {len(ru_clean)} games, "
          f"completed {d}, Mafia won {m}, "
          f"probe records {sum(len(g.probes) for g in ru_clean)}")
    for label, gs in (("en_demo", en_demo), ("ablation_demand", ablation), ("ru_clean", ru_clean)):
        print(f"  outcomes {label}: {outcome_line(gs)}")
    for g in forks:
        print(f"replay fork (excluded everywhere): {g.game_id[:8]} "
              f"forked_from {str(g.forked_from)[:8]}, {len(g.probes)} probe records")
    print("\nproposed inclusion criterion: the single-config 30-game main batch")
    print("(all completed, same probe battery). Consistent numbers: 30 games,")
    main_probes = sum(len(g.probes) for g in main)
    print(f"{main_probes} probe records, Mafia won 17 of 30. If pilots are kept: 32 games,")
    print("24245 records, 'Mafia won 18 of 31 completed; one pilot game was aborted'.")

    r4 = [(g, r) for g in with_probes for r in g.probes if r["round"] >= 4]
    if r4:
        roles = {g.gt.get(r["player_name"]) for g, r in r4}
        print(f"\nfate of round 4 (P2-9/Table 1 footnote): {len(r4)} probe records, "
              f"all from {'/'.join(sorted(str(x) for x in roles))} observers -> "
              "excluded from villager-side F1 by construction, hence no round-4 row.")


# ────────────────────────────────────────────
#  Parse rates & repair bias (P1-1)
# ────────────────────────────────────────────

def print_parse(games: list[M.Game]) -> None:
    hdr("JSON PARSE RATES: raw vs repaired (P1-1)")
    raw = M.parse_stats(games, "raw")
    rep = M.parse_stats(games, "repaired")
    for k in sorted(raw):
        ok_r, n = raw[k]
        ok_p, _ = rep[k]
        print(f"  {k:22s} raw {ok_r:5d}/{n:5d} = {ok_r / n * 100:5.1f}%   "
              f"repaired {ok_p:5d}/{n:5d} = {ok_p / n * 100:5.1f}%")
    tr = sum(v[0] for v in raw.values()); tn = sum(v[1] for v in raw.values())
    tp = sum(v[0] for v in rep.values())
    print(f"  {'TOTAL':22s} raw {tr:5d}/{tn:5d} = {tr / tn * 100:5.1f}%   "
          f"repaired {tp:5d}/{tn:5d} = {tp / tn * 100:5.1f}%")

    print("\nrole_assessment parse rate by round (raw) — missingness is round-correlated:")
    by_rnd = M.parse_rate_by_round(games, "role_assessment", "raw")
    for rnd, (ok, n) in sorted(by_rnd.items()):
        print(f"  round {rnd}: {ok}/{n} = {ok / n * 100:.1f}%")

    bias = M.repair_bias_stats(games)
    print("\nrepair bias direction (truncated tail of the list is lost):")
    print(f"  originally parsed answers: n={bias['n_originally_parsed']}, "
          f"mean players per answer {bias['mean_items_originally_parsed']:.2f}")
    print(f"  repair-recovered answers:  n={bias['n_recovered']}, "
          f"mean players per answer {bias['mean_items_recovered']:.2f}")
    print("  -> recovered answers are shorter: players late in the answer list are")
    print("     under-represented; repair removes the round-correlated missingness")
    print("     but introduces a milder within-answer position bias.")


# ────────────────────────────────────────────
#  F1 + F2 for one (corpus, mode) variant
# ────────────────────────────────────────────

def print_f1_f2(label: str, corpus: list[M.Game], mode: str, n_boot: int, seed: int) -> None:
    hdr(f"F1/F2 — {label}", "-")
    cells = [M.first_order_cells(g, mode) for g in corpus]
    summ = M.first_order_summary(cells)

    print("F1: villager-side first-order beliefs by round (with chance baselines, P1-11)")
    print(f"{'rnd':>3s} {'committed acc':>22s} {'95% CI':>15s} {'chance':>7s} "
          f"{'Unknown':>16s} {'Mafia recall':>18s} {'95% CI':>15s} {'chance':>7s}")
    for rnd, s in summ.items():
        acc_ci = M.bootstrap_ci(cells, M.stat_round_ratio("acc", rnd), n_boot, seed)
        rec_ci = M.bootstrap_ci(cells, M.stat_round_ratio("recall", rnd), n_boot, seed)
        print(f"{rnd:3d} "
              f"{pct(s['acc']):>9s} (n={s['acc_n']:5d}) {ci_str(acc_ci):>15s} {pct(s['acc_chance']):>7s} "
              f"{pct(s['unk']):>7s} (n={s['unk_n']:5d}) "
              f"{pct(s['recall']):>7s} (n={s['recall_n']:4d}) {ci_str(rec_ci):>15s} {pct(s['recall_chance']):>7s}")
    print("  (chance acc = random permutation of true alive-role multiset;")
    print("   chance recall = n_alive_mafia / n_alive_others at probe time)")

    calib = [x for c in cells for x in c.calib]
    aggs = [M.calib_aggregate(c.calib) for c in cells]
    bins = M.calibration_bins(calib)
    eb = M.ece_brier(calib)
    print("\nF2: calibration, ALL bins with n and 95% CI (P1-6), + ECE/Brier (P2-1)")
    for b, d in bins.items():
        ci = M.bootstrap_ci(aggs, M.stat_calib_bin(b), n_boot, seed)
        flag = "  (tiny bin — do not interpret)" if d["n"] < 50 else ""
        print(f"  conf {d['lo']:3d}-{d['hi']:3d}: acc {pct(d['acc'])} (n={d['n']:5d}) "
              f"CI {ci_str(ci)}  mean conf {d['mean_conf']:.1f}{flag}")
    ece_ci = M.bootstrap_ci(aggs, M.stat_ece, n_boot, seed)
    brier_ci = M.bootstrap_ci(aggs, M.stat_brier, n_boot, seed)
    print(f"  ECE   = {eb['ece']:.3f}  CI [{ece_ci[0]:.3f}, {ece_ci[1]:.3f}]  (n={eb['n']})")
    print(f"  Brier = {eb['brier']:.3f}  CI [{brier_ci[0]:.3f}, {brier_ci[1]:.3f}]")


# ────────────────────────────────────────────
#  F3 for one (corpus, mode) variant
# ────────────────────────────────────────────

def so_line(corpus: list[M.Game], mode: str, n_boot: int, seed: int, *,
            threshold: float, exclude_mm: bool, window: int | None,
            with_ci: bool = True) -> str:
    per_game_pairs = [
        M.second_order_pairs(g, mode, threshold=threshold,
                             exclude_mafia_pairs=exclude_mm, recency_rounds=window)
        for g in corpus
    ]
    pairs = [p for gp in per_game_pairs for p in gp]
    s = M.second_order_summary(pairs)
    if not s.get("n"):
        return "    (no pairs)"
    line = (f"acc {pct(s['accuracy'])} vs majority({s['majority_class']}) {pct(s['majority_baseline'])} "
            f"diff {s['diff'] * 100:+.1f}pp  n={s['n']}  "
            f"pred/actual suspects {s['pred_suspects']}/{s['actual_suspects']}")
    if s["suspects_ratio"] is not None:
        line += f" = {s['suspects_ratio']:.2f}"
    if with_ci:
        aggs = [M.so_aggregate(gp) for gp in per_game_pairs]
        acc_ci = M.bootstrap_ci(aggs, M.stat_so_acc, n_boot, seed)
        diff_ci = M.bootstrap_ci(aggs, M.stat_so_diff, n_boot, seed)
        ratio_ci = M.bootstrap_ci(aggs, M.stat_so_ratio, n_boot, seed)
        rc = f"[{ratio_ci[0]:.2f}, {ratio_ci[1]:.2f}]" if ratio_ci else "n/a"
        line += (f"\n      CI: acc {ci_str(acc_ci)}  diff {ci_str(diff_ci)}  ratio {rc}")
    return line


def print_f3(label: str, corpus: list[M.Game], mode: str, n_boot: int, seed: int,
             sensitivity: bool) -> None:
    hdr(f"F3 second-order consistency — {label}", "-")
    print("canonical rule: Mafia-Mafia pairs EXCLUDED, conf threshold 50, no staleness window")
    print("  canonical:", so_line(corpus, mode, n_boot, seed,
                                  threshold=50, exclude_mm=True, window=None))
    print("  paper-script legacy (Mafia-Mafia pairs INCLUDED — reproduces published 49.5/55.4 in raw mode):")
    print("   ", so_line(corpus, mode, n_boot, seed,
                         threshold=50, exclude_mm=False, window=None, with_ci=False))
    if sensitivity:
        print("  sensitivity (Mafia-Mafia excluded):")
        for thr in (30, 50, 70):
            for win in (None, 1):
                w = "no window " if win is None else "<=1 round "
                print(f"    thr {thr}, {w}:",
                      so_line(corpus, mode, n_boot, seed,
                              threshold=thr, exclude_mm=True, window=win, with_ci=False))


# ────────────────────────────────────────────
#  Probing cost (P2-9)
# ────────────────────────────────────────────

def print_cost(label: str, corpus: list[M.Game]) -> None:
    c = M.probe_cost(corpus)
    hdr(f"PROBING COST — {label} (P2-9)", "-")
    print(f"  probe calls: {c['n_probes']} total, {c['probes_per_game']:.0f} per game")
    print(f"  game-move LLM calls: {c['game_llm_calls']} total, "
          f"{c['game_llm_calls_per_game']:.0f} per game "
          f"-> probing multiplies LLM calls x{c['call_multiplier']:.1f}")
    print(f"  mean probe latency: {c['mean_latency_ms'] / 1000:.1f}s; "
          f"probe wall-clock per game (sequential): {c['probe_wall_clock_s_per_game'] / 3600:.1f}h")
    print(f"  probe sizes: prompt {c['prompt_chars_per_probe']:.0f} chars, "
          f"answer {c['answer_chars_per_probe']:.0f} chars per call")
    print(f"  est. tokens (~3 chars/token for this RU/JSON corpus): "
          f"~{c['est_tokens_per_probe']:.0f}/probe, ~{c['est_tokens_per_game'] / 1000:.0f}k/game (estimate)")


# ────────────────────────────────────────────
#  Main
# ────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--logs", default=M.default_logs_dir(),
                    help="path to the logs directory (default: <repo>/logs)")
    ap.add_argument("--boot", type=int, default=1000, help="bootstrap resamples (default 1000)")
    ap.add_argument("--seed", type=int, default=0, help="bootstrap RNG seed")
    args = ap.parse_args()

    games = M.load_games(args.logs)
    if not games:
        sys.exit(f"no game logs found under {args.logs}")

    print_audit(games)

    paper32 = M.select_corpus(games, "paper32")
    main30 = M.select_corpus(games, "main30")

    print_parse(paper32)

    variants = [
        ("paper corpus (32 games), RAW-parsed  [as published]", paper32, "raw", False),
        ("paper corpus (32 games), REPAIRED", paper32, "repaired", True),
        ("main batch (30 games), REPAIRED  [recommended canonical]", main30, "repaired", True),
    ]
    for label, corpus, mode, sens in variants:
        print_f1_f2(label, corpus, mode, args.boot, args.seed)
        print_f3(label, corpus, mode, args.boot, args.seed, sensitivity=sens)

    print_cost("paper corpus (32 games)", paper32)
    print_cost("main batch (30 games)", main30)

    print("\nNOTE on repair bias direction: the JSON-repair pass keeps only the complete")
    print("prefix of a truncated answer list, so repaired-mode metrics under-sample")
    print("players that appear late in each answer (see repair bias stats above).")


if __name__ == "__main__":
    main()
