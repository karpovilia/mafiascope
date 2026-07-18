#!/usr/bin/env python3
"""F1 (belief trajectories) + F5 (probe cost / parse / transport) on
corpus32 / main30 / gpt4omini28, same methodology as the paper
(metrics_lib, repaired mode, cluster bootstrap B=1000 seed=0).
"""
import json
import os
import sys
from collections import defaultdict

FARMS = "/tmp/claude-1000/-home-ki-repos-mafia/e091718d-9521-42e2-b22a-3b403f98be04/scratchpad/coupling"
sys.path.insert(0, "/home/ki/repos/mafia/src")
import metrics_lib as M  # noqa: E402

B, SEED = 1000, 0


def stat_round_frac(attr, rnd):
    return M.stat_round_ratio(attr, rnd)


def f1_block(games):
    cells = [M.first_order_cells(g, "repaired") for g in games]
    summ = M.first_order_summary(cells)
    out = {}
    for rnd, s in sorted(summ.items()):
        acc_ci = M.bootstrap_ci(cells, M.stat_round_ratio("acc", rnd), B, SEED)
        unk_ci = M.bootstrap_ci(cells, M.stat_round_ratio("unk", rnd), B, SEED)
        rec_ci = M.bootstrap_ci(cells, M.stat_round_ratio("recall", rnd), B, SEED)
        out[rnd] = dict(
            committed_acc=s["acc"], acc_n=s["acc_n"], acc_ci95=acc_ci,
            acc_chance=s["acc_chance"],
            unknown=s["unk"], unk_n=s["unk_n"], unk_ci95=unk_ci,
            mafia_recall=s["recall"], recall_n=s["recall_n"], recall_ci95=rec_ci,
            recall_chance=s["recall_chance"],
        )
    return out


def f5_block(games):
    cost = M.probe_cost(games)
    n_probes = 0
    raw_ok = 0
    rep_ok = 0
    transport = 0          # answer_raw is an ERROR transport string
    content_fail = 0       # delivered, but unparsed even after repair
    recovered = 0          # unparsed raw, recovered by repair
    trunc_ra_fail = 0      # role_assessment: raw-unparsed, delivered (truncation etc.)
    ra_total = 0
    ra_raw_ok = 0
    for g in games:
        for r in g.probes:
            n_probes += 1
            raw = r.get("answer_raw") or ""
            is_err = raw.startswith("ERROR")
            ap_raw = r.get("answer_parsed")
            ap_rep = M.get_answer(r, "repaired")
            if ap_raw is not None:
                raw_ok += 1
            if ap_rep is not None:
                rep_ok += 1
                if ap_raw is None:
                    recovered += 1
            else:
                if is_err:
                    transport += 1
                else:
                    content_fail += 1
            if r["probe_id"] == "role_assessment":
                ra_total += 1
                if ap_raw is not None:
                    ra_raw_ok += 1
                elif not is_err:
                    trunc_ra_fail += 1
    n_fail_raw = n_probes - raw_ok
    n_fail_rep = n_probes - rep_ok
    return dict(
        n_games=len(games),
        n_probes=n_probes,
        probes_per_game=round(n_probes / len(games), 1),
        game_llm_calls_per_game=round(cost["game_llm_calls_per_game"], 1),
        call_multiplier=round(cost["call_multiplier"], 1),
        est_tokens_per_probe=round(cost["est_tokens_per_probe"]),
        parsed_raw=raw_ok, parse_rate_raw=round(raw_ok / n_probes, 4),
        parsed_repaired=rep_ok, parse_rate_repaired=round(rep_ok / n_probes, 4),
        repair_recovered=recovered,
        failures_raw=n_fail_raw,
        failures_repaired=n_fail_rep,
        transport_errors=transport,
        transport_share_of_repaired_failures=(round(transport / n_fail_rep, 4)
                                              if n_fail_rep else None),
        transport_share_of_all_probes=round(transport / n_probes, 4),
        content_level_failures=content_fail,
        content_parse_rate_of_delivered=(round(rep_ok / (n_probes - transport), 4)
                                         if n_probes - transport else None),
        role_assessment=dict(
            total=ra_total, raw_parsed=ra_raw_ok,
            raw_parse_rate=round(ra_raw_ok / ra_total, 4) if ra_total else None,
            delivered_but_raw_unparsed=trunc_ra_fail,
            trunc_share=round(trunc_ra_fail / ra_total, 4) if ra_total else None,
        ),
    )


def main():
    out = {"_config": dict(mode="repaired", bootstrap_B=B, seed=SEED,
                           farms=FARMS)}
    for name in ("corpus32", "main30", "gpt4omini"):
        games = M.load_games(os.path.join(FARMS, "farm_" + name), tolerant=True)
        print("===", name, len(games), "games", flush=True)
        out[name] = dict(f1=f1_block(games), f5=f5_block(games))
        print(json.dumps(out[name]["f5"], indent=1)[:900], flush=True)
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "f1_f5_results.json")
    with open(dst, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print("written", dst)


if __name__ == "__main__":
    main()
