#!/usr/bin/env python3
"""Idea 06 control: RANDOM-baseline bifurcation points (no probe guidance).

Population: exactly the same decision population as select_points.py —
innocent DAY votes in EN corpus32 games with state.jsonl snapshots — but
with NO quadrant filtering and NO use of probe signals for selection.
Points are drawn uniformly at random (seed 20260719), excluding the 16
(game_id, round, voter) decisions already used by the probe-guided run.
Only technical requirements apply: voter alive, frozen-context bundle
(pre-action snapshot + verbatim vote prompt) reconstructable, EN player.

Quadrant/s1/move_good are still RECORDED per point (post-hoc annotation
for the comparison report) — they play no role in the draw.

Output: $BIF_DATA_DIR/points.json (run with
BIF_DATA_DIR=.../data/bifurcation_random).

Usage: BIF_DATA_DIR=... mafia/.venv/bin/python select_points_random.py [-n 8]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as CM                    # noqa: E402
import select_points as SP             # noqa: E402

SEED = 20260719
GUIDED_POINTS = os.path.join(CM.M2, "data/bifurcation/points.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--n-points", type=int, default=8)
    args = ap.parse_args()

    used = {(p["game_id"], p["round"], p["voter"])
            for p in json.load(open(GUIDED_POINTS))["points"]}
    assert len(used) == 16, f"expected 16 guided points, got {len(used)}"

    games = SP.en_corpus32_games()
    print(f"EN corpus32 games with snapshots: {len(games)}")
    units = SP.innocent_vote_units(games)
    pool = [u for u in units
            if (u["game_id"], u["round"], u["voter"]) not in used]
    print(f"innocent day votes: {len(units)} total, "
          f"{len(pool)} after excluding the 16 guided points")

    # uniform draw with rejection of technically infeasible points:
    # shuffle once, walk the list, keep the first n feasible
    rng = random.Random(SEED)
    rng.shuffle(pool)
    points, skipped = [], []
    for u in pool:
        if len(points) >= args.n_points:
            break
        bundle = CM.fork_and_prompt(u["game_id"], u["round"], u["voter"])
        if bundle is None:
            skipped.append({**u, "skip": "no_preaction_snapshot_or_prompt"})
            continue
        if bundle["language"] != "en":
            skipped.append({**u, "skip": "non_en_player"})
            continue
        u["fork"] = bundle["fork"]
        u["vote_seq"] = bundle["vote_seq"]
        u["alive"] = bundle["alive"]
        u["roles_alive"] = bundle["roles"]
        u["in_3b_replay"] = False
        u["point_id"] = (f"rand_{u['game_id'][:8]}_r{u['round']}_{u['voter']}")
        points.append(u)

    os.makedirs(CM.DATA, exist_ok=True)
    out = {"seed": SEED, "selection": "uniform_random_no_probe",
           "n_points": args.n_points,
           "pool_size": len(pool), "excluded_guided_points": 16,
           "skipped": skipped, "points": points}
    json.dump(out, open(CM.POINTS_JSON, "w"), ensure_ascii=False, indent=1)
    print(f"wrote {len(points)} points -> {CM.POINTS_JSON}; "
          f"skipped {len(skipped)}")
    for p in points:
        print(f"  {p['point_id']:<28} quadrant(post-hoc)={p['quadrant']} "
              f"winner={p['winner']} alive={len(p['alive'])}")


if __name__ == "__main__":
    main()
