#!/usr/bin/env python3
"""Idea 06, step 6: outcome spread over the ~20 forks of each bifurcation
point; perception vs policy comparison; selection-diversity stats.

Definitions:
  parent_winner   winner of the (unforked) parent game;
  P(V win)        share of valid forks won by Villagers (side of the innocent
                  forked voter);
  flip share      share of valid forks whose winner != parent_winner
                  (all selected points come from Mafia-won parents or not —
                  recorded per point, judge from the JSON);
  controllable    point with BOTH outcomes present across its forks.

Output: analysis/bifurcation_2026_07_16.json (+ console tables).
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as CM  # noqa: E402

OUT = os.path.join(CM.M2, "analysis/bifurcation_2026_07_16.json")


def load_forks() -> dict[tuple, dict]:
    """fork_results.jsonl of the current CM.DATA; last write wins."""
    rows = CM.load_jsonl(os.path.join(CM.DATA, "fork_results.jsonl")) \
        if os.path.isfile(os.path.join(CM.DATA, "fork_results.jsonl")) else []
    forks: dict[tuple, dict] = {}
    for r in rows:
        forks[(r["point_id"], r["variant_idx"])] = r
    return forks


def build_per_point(points: list[dict], forks: dict[tuple, dict]) -> list[dict]:
    """Per-point outcome/diversity stats (shared with analyze_random.py)."""
    per_point = []
    for p in points:
        pid = p["point_id"]
        spath = os.path.join(CM.DATA, pid, "selected.json")
        if not os.path.isfile(spath):
            continue
        sel = json.load(open(spath))
        vres = []
        for v in sel["variants"]:
            fr = forks.get((pid, v["variant_idx"]))
            vres.append({
                "variant_idx": v["variant_idx"], "source": v["source"],
                "stage": v["stage"], "vote_target": v["vote_target"],
                "target_is_mafia": (
                    p["roles_alive"].get(v["vote_target"]) == "Mafia"
                    if v["vote_target"] else None),
                "winner": fr.get("winner") if fr else None,
                "rounds": fr.get("rounds") if fr else None,
                "fork_game_id": fr.get("fork_game_id") if fr else None,
                "error": fr.get("error") if fr else "not_run",
            })
        valid = [v for v in vres if v["winner"]]
        wins = Counter(v["winner"] for v in valid)
        pv = wins.get("Villagers", 0) / len(valid) if valid else None
        flips = [v for v in valid if v["winner"] != p["winner"]]
        factual = next((v for v in valid if v["source"] == "factual"), None)
        # split by whether the variant voted a true mafioso
        maf = [v for v in valid if v["target_is_mafia"]]
        oth = [v for v in valid if not v["target_is_mafia"]]
        per_point.append({
            "point_id": pid, "quadrant": p["quadrant"],
            "game_id": p["game_id"], "round": p["round"],
            "voter": p["voter"], "factual_target": p["target"],
            "parent_winner": p["winner"], "in_3b_replay": p["in_3b_replay"],
            "n_alive": len(p["alive"]),
            "n_forks_valid": len(valid), "n_forks_total": len(vres),
            "winners": dict(wins),
            "p_villagers_win": pv,
            "flip_share": len(flips) / len(valid) if valid else None,
            "controllable": len(set(v["winner"] for v in valid)) > 1
            if valid else None,
            "factual_fork_winner": factual["winner"] if factual else None,
            "rounds_mean": (sum(v["rounds"] for v in valid) / len(valid))
            if valid else None,
            "rounds_min_max": ([min(v["rounds"] for v in valid),
                                max(v["rounds"] for v in valid)]
                               if valid else None),
            "p_v_win_vote_mafia": (sum(v["winner"] == "Villagers"
                                       for v in maf) / len(maf)
                                   if maf else None),
            "n_vote_mafia": len(maf),
            "p_v_win_vote_other": (sum(v["winner"] == "Villagers"
                                       for v in oth) / len(oth)
                                   if oth else None),
            "diversity": sel["diversity"],
            "target_dist_samples": sel["target_dist_samples"],
            "variants": vres,
        })
    return per_point


def main() -> None:
    meta = json.load(open(CM.POINTS_JSON))
    per_point = build_per_point(meta["points"], load_forks())

    def agg(pts):
        pts = [x for x in pts if x["n_forks_valid"]]
        if not pts:
            return {}
        return {
            "n_points": len(pts),
            "mean_p_villagers_win": sum(x["p_villagers_win"] for x in pts) / len(pts),
            "mean_flip_share": sum(x["flip_share"] for x in pts) / len(pts),
            "controllable_points": sum(x["controllable"] for x in pts),
            "mean_rounds": sum(x["rounds_mean"] for x in pts) / len(pts),
            "mean_cos_selected": sum(
                x["diversity"]["mean_pairwise_cos_selected"] for x in pts) / len(pts),
            "mean_cos_random20": sum(
                x["diversity"]["mean_pairwise_cos_random20"] for x in pts) / len(pts),
        }

    by_quad = {q: agg([x for x in per_point if x["quadrant"] == q])
               for q in ("policy_gap", "perception_gap")}

    # pooled extras per quadrant: behavioral lock-in of the 500-sample pool
    # (modal-target concentration) and P(V win) conditional on the variant
    # voting a true mafioso; list of outcome-flipping variants
    for q, a in by_quad.items():
        qs = [x for x in per_point if x["quadrant"] == q and x["n_forks_valid"]]
        concs, fact_modal = [], 0
        vm_w = vm_n = vo_w = vo_n = 0
        flips = []
        for x in qs:
            dist = x["target_dist_samples"]
            tot = sum(dist.values())
            modal = max(dist, key=dist.get)
            concs.append(dist[modal] / tot)
            fact_modal += (modal == x["factual_target"])
            for v in x["variants"]:
                if not v["winner"]:
                    continue
                if v["target_is_mafia"]:
                    vm_n += 1
                    vm_w += v["winner"] == "Villagers"
                else:
                    vo_n += 1
                    vo_w += v["winner"] == "Villagers"
                if v["winner"] != x["parent_winner"]:
                    flips.append({"point_id": x["point_id"],
                                  "variant_idx": v["variant_idx"],
                                  "vote_target": v["vote_target"],
                                  "target_is_mafia": v["target_is_mafia"]})
        a["modal_target_concentration_mean"] = sum(concs) / len(concs)
        a["factual_is_modal_points"] = fact_modal
        a["p_v_win_vote_mafia_pooled"] = vm_w / vm_n if vm_n else None
        a["n_vote_mafia_pooled"] = vm_n
        a["p_v_win_vote_other_pooled"] = vo_w / vo_n if vo_n else None
        a["n_vote_other_pooled"] = vo_n
        a["flipping_variants"] = flips

    out = {
        "title": "Idea 06: bifurcation points — 500 hi-T samples, "
                 "20 maxmin-diverse variants, 1 fork each",
        "date": "2026-07-16",
        "sampling": {"model": CM.MODEL, "temperature": CM.TEMPERATURE,
                     "max_tokens": CM.MAX_TOKENS, "n_samples": CM.N_SAMPLES,
                     "n_selected": CM.N_SELECT},
        "selection_seed": meta["seed"],
        "by_quadrant": by_quad,
        "points": per_point,
    }
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

    print(f"{'point':<28} {'quad':<11} {'P(Vwin)':>7} {'flip':>5} "
          f"{'ctrl':>4} {'factWin':>9} {'rounds':>7}")
    for x in per_point:
        if x["p_villagers_win"] is None:
            print(f"{x['point_id']:<28} {x['quadrant']:<11}  (no forks)")
            continue
        print(f"{x['point_id']:<28} {x['quadrant'][:10]:<11} "
              f"{x['p_villagers_win']:>7.2f} {x['flip_share']:>5.2f} "
              f"{str(x['controllable'])[:1]:>4} "
              f"{str(x['factual_fork_winner'])[:9]:>9} "
              f"{x['rounds_mean']:>7.1f}")
    print()
    for q, a in by_quad.items():
        print(q, json.dumps(a, indent=None))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
