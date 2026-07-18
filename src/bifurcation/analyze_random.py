#!/usr/bin/env python3
"""Idea 06 control: analysis of the RANDOM-baseline points + comparison
with the probe-guided run (bifurcation_2026_07_16.json).

Comparisons (per brief):
  (a) fork-level flip share: random forks vs guided 320 (Fisher exact);
  (b) point-level: share of points with >=1 flip (8 vs 16, Fisher exact);
  (c) flip structure: share of forks/flips voting a true mafioso,
      P(V win | vote mafia) vs P(V win | other), random vs policy_gap;
  (d) behavioral lock-in (modal-target concentration of the 500-sample
      pool) of random points vs the guided quadrants.

Output: analysis/bifurcation_random_2026_07_18.json (+ console tables).

Usage: mafia/.venv/bin/python analyze_random.py
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("BIF_DATA_DIR",
                      "/home/ki/repos/mafia2/data/bifurcation_random")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as CM   # noqa: E402
import analyze as A   # noqa: E402

from scipy.stats import fisher_exact, mannwhitneyu  # noqa: E402

GUIDED_JSON = os.path.join(CM.M2, "analysis/bifurcation_2026_07_16.json")
OUT = os.path.join(CM.M2, "analysis/bifurcation_random_2026_07_18.json")


def sampling_cost(points: list[dict]) -> dict:
    tot = {"calls": 0, "prompt_tokens": 0, "cache_hit": 0, "completion": 0}
    for p in points:
        path = os.path.join(CM.DATA, p["point_id"], "samples.jsonl")
        if not os.path.isfile(path):
            continue
        for r in CM.load_jsonl(path):
            u = r.get("usage", {})
            tot["calls"] += 1
            tot["prompt_tokens"] += u.get("prompt_tokens") or 0
            tot["cache_hit"] += u.get("prompt_cache_hit_tokens") or 0
            tot["completion"] += u.get("completion_tokens") or 0
    miss = tot["prompt_tokens"] - tot["cache_hit"]
    tot["usd"] = round(miss / 1e6 * 0.27 + tot["cache_hit"] / 1e6 * 0.07
                       + tot["completion"] / 1e6 * 1.10, 2)
    return tot


def pooled(pts: list[dict]) -> dict:
    """Pooled fork-level counts + lock-in over a set of per-point rows."""
    n_valid = n_flips = 0
    vm_w = vm_n = vo_w = vo_n = 0
    flips, flip_points = [], set()
    concs, fact_modal = [], 0
    for x in pts:
        dist = x["target_dist_samples"]
        tot = sum(dist.values())
        if tot:
            modal = max(dist, key=dist.get)
            concs.append(dist[modal] / tot)
            fact_modal += (modal == x["factual_target"])
        for v in x["variants"]:
            if not v["winner"]:
                continue
            n_valid += 1
            if v["target_is_mafia"]:
                vm_n += 1
                vm_w += v["winner"] == "Villagers"
            else:
                vo_n += 1
                vo_w += v["winner"] == "Villagers"
            if v["winner"] != x["parent_winner"]:
                n_flips += 1
                flip_points.add(x["point_id"])
                flips.append({"point_id": x["point_id"],
                              "variant_idx": v["variant_idx"],
                              "vote_target": v["vote_target"],
                              "target_is_mafia": v["target_is_mafia"]})
    return {
        "n_points": len(pts), "n_forks_valid": n_valid, "n_flips": n_flips,
        "flip_share_pooled": n_flips / n_valid if n_valid else None,
        "points_with_flip": len(flip_points),
        "p_v_win_vote_mafia_pooled": vm_w / vm_n if vm_n else None,
        "n_vote_mafia_pooled": vm_n,
        "p_v_win_vote_other_pooled": vo_w / vo_n if vo_n else None,
        "n_vote_other_pooled": vo_n,
        "flips_on_mafia_vote": sum(1 for f in flips if f["target_is_mafia"]),
        "modal_target_concentration_mean": (sum(concs) / len(concs)
                                            if concs else None),
        "factual_is_modal_points": fact_modal,
        "flipping_variants": flips,
    }


def main() -> None:
    meta = json.load(open(CM.POINTS_JSON))
    points = meta["points"]
    per_point = A.build_per_point(points, A.load_forks())
    rnd = pooled(per_point)

    guided = json.load(open(GUIDED_JSON))
    gpts = [x for x in guided["points"] if x["n_forks_valid"]]
    gd = pooled(gpts)
    gd_by_quad = {q: pooled([x for x in gpts if x["quadrant"] == q])
                  for q in ("policy_gap", "perception_gap")}

    # (a) fork-level Fisher: flips vs non-flips, random vs guided
    tab_forks = [[rnd["n_flips"], rnd["n_forks_valid"] - rnd["n_flips"]],
                 [gd["n_flips"], gd["n_forks_valid"] - gd["n_flips"]]]
    odds_f, p_f = fisher_exact(tab_forks)
    # guided policy_gap alone vs random (the strongest guided arm)
    gp = gd_by_quad["policy_gap"]
    tab_pol = [[rnd["n_flips"], rnd["n_forks_valid"] - rnd["n_flips"]],
               [gp["n_flips"], gp["n_forks_valid"] - gp["n_flips"]]]
    odds_p, p_p = fisher_exact(tab_pol)

    # (b) point-level Fisher: points with >=1 flip
    tab_points = [[rnd["points_with_flip"],
                   rnd["n_points"] - rnd["points_with_flip"]],
                  [gd["points_with_flip"],
                   gd["n_points"] - gd["points_with_flip"]]]
    odds_pt, p_pt = fisher_exact(tab_points)

    # exploratory: random points by post-hoc quadrant
    by_posthoc = {}
    quad_of = {p["point_id"]: p.get("quadrant") for p in points}
    for q in sorted({str(quad_of[x["point_id"]]) for x in per_point}):
        by_posthoc[q] = pooled([x for x in per_point
                                if str(quad_of[x["point_id"]]) == q])
        by_posthoc[q].pop("flipping_variants")

    div = [x["diversity"] for x in per_point]
    cost = sampling_cost(points)

    out = {
        "title": "Idea 06 control: random-baseline bifurcation points "
                 "(uniform draw, no probe signals) vs probe-guided",
        "date": "2026-07-18",
        "sampling": {"model": CM.MODEL, "temperature": CM.TEMPERATURE,
                     "max_tokens": CM.MAX_TOKENS, "n_samples": CM.N_SAMPLES,
                     "n_selected": CM.N_SELECT},
        "selection_seed": meta["seed"],
        "population": "innocent EN corpus32 day votes, 16 guided points "
                      "excluded, no quadrant filter, no probe signals",
        "random": {**rnd, "per_point": per_point},
        "guided": {"pooled": gd, "by_quadrant": {
            q: {k: v for k, v in a.items() if k != "flipping_variants"}
            for q, a in gd_by_quad.items()}},
        "random_by_posthoc_quadrant": by_posthoc,
        "tests": {
            "fork_level_random_vs_guided": {
                "table": tab_forks, "fisher_odds_ratio": odds_f,
                "fisher_p_two_sided": p_f},
            "fork_level_random_vs_guided_policy_gap": {
                "table": tab_pol, "fisher_odds_ratio": odds_p,
                "fisher_p_two_sided": p_p},
            "point_level_random_vs_guided": {
                "table": tab_points, "fisher_odds_ratio": odds_pt,
                "fisher_p_two_sided": p_pt},
            # cluster-robust view: forks of one point share a parent game,
            # so also compare per-point flip shares (n=8 vs n=16)
            "point_flip_shares_mannwhitney": {
                "random": [x["flip_share"] for x in per_point],
                "guided": [x["flip_share"] for x in gpts],
                "u": (mw := mannwhitneyu(
                    [x["flip_share"] for x in per_point],
                    [x["flip_share"] for x in gpts],
                    alternative="two-sided"))[0],
                "p_two_sided": mw[1]},
        },
        "diversity_mean": {
            "cos_selected": sum(d["mean_pairwise_cos_selected"]
                                for d in div) / len(div),
            "cos_random20": sum(d["mean_pairwise_cos_random20"]
                                for d in div) / len(div),
        },
        "sampling_cost": cost,
    }
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

    print(f"{'point':<26} {'posthoc_quad':<15} {'P(Vwin)':>7} {'flip':>5} "
          f"{'ctrl':>4} {'factWin':>9} {'rounds':>7}")
    for x in per_point:
        if x["p_villagers_win"] is None:
            print(f"{x['point_id']:<26} (no forks)")
            continue
        print(f"{x['point_id']:<26} {str(quad_of[x['point_id']]):<15} "
              f"{x['p_villagers_win']:>7.2f} {x['flip_share']:>5.2f} "
              f"{str(x['controllable'])[:1]:>4} "
              f"{str(x['factual_fork_winner'])[:9]:>9} "
              f"{x['rounds_mean']:>7.1f}")
    print()
    print("random pooled:", {k: v for k, v in rnd.items()
                             if k != "flipping_variants"})
    print("guided pooled:", {k: v for k, v in gd.items()
                             if k != "flipping_variants"})
    print("tests:", json.dumps(out["tests"], indent=1))
    print("sampling cost:", cost)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
