#!/usr/bin/env python3
"""Idea 06, step 1: pick bifurcation points from the 3B quadrant pool.

Pool = corpus32 EN games with state.jsonl snapshots, innocent DAY votes,
quadrants policy_gap (S1 correct, move bad) and perception_gap (S1 wrong,
move bad); the S1/move operationalization is byte-identical to idea 3B
level 1 (coupling_lib.extract_votes: last PRE-vote role_assessment committed
set vs true mafia; move good = voted a true mafioso).

Priority: decisions already replayed in 3B level 2 (raw_decisions of
analysis/move_quality_2026_07_15.json) come first — their forced-arm
baselines are directly comparable; the remainder is filled from the full
EN pool with a seeded shuffle.  Every point must yield a frozen-context
bundle (fork snapshot + verbatim vote prompt + factual reply), otherwise
it is skipped and logged.

Usage: mafia/.venv/bin/python select_points.py [--per-quadrant 8]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as CM  # noqa: E402

sys.path.insert(0, CM.MAFIA_SRC)
import metrics_lib as M          # noqa: E402
import coupling_lib as C         # noqa: E402

SEED = 20260716
QUADS = ("policy_gap", "perception_gap")


def quad(u: dict) -> str | None:
    if u.get("s1") is None or u.get("move_good") is None:
        return None
    if u["s1"] and u["move_good"]:
        return "correct_good"
    if u["s1"] and not u["move_good"]:
        return "policy_gap"
    if (not u["s1"]) and not u["move_good"]:
        return "perception_gap"
    return "luck"


def en_corpus32_games() -> list:
    reg = json.load(open(CM.MAFIA_CORPORA))["corpus32"]["game_ids"]
    games = M.load_games(CM.LOGS, tolerant=True)
    by_id = {g.game_id: g for g in games}
    out = []
    for gid in reg:
        g = by_id.get(gid)
        if g is None:
            continue
        sp = os.path.join(CM.LOGS, gid, "state.jsonl")
        if not os.path.isfile(sp):
            continue
        with open(sp) as f:
            rec = json.loads(f.readline())
        if rec["players"][0].get("language") == "en":
            out.append(g)
    return out


def innocent_vote_units(games) -> list[dict]:
    units = []
    for g in games:
        mafia = {n for n, r in g.gt.items() if r == "Mafia"}
        for rec in C.extract_votes(g):
            if rec["group"] != "innocent":
                continue
            ra_set = rec.get("ra_set")
            u = dict(game_id=g.game_id, round=rec["round"], voter=rec["voter"],
                     target=rec["target"], winner=g.winner)
            u["s1"] = bool(set(ra_set) & mafia) if ra_set is not None else None
            u["move_good"] = int(rec["target"] in mafia)
            u["quadrant"] = quad(u)
            units.append(u)
    return units


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-quadrant", type=int, default=8)
    args = ap.parse_args()

    games = en_corpus32_games()
    print(f"EN corpus32 games with snapshots: {len(games)}")
    units = innocent_vote_units(games)

    mq = json.load(open(os.path.join(CM.M2, "analysis/move_quality_2026_07_15.json")))
    replayed = {(d["game_id"], d["round"], d["voter"])
                for d in mq["level2_replay"]["raw_decisions"] if "win_by_arm" in d}

    buckets: dict[str, list[dict]] = {q: [] for q in QUADS}
    skipped = []
    for u in units:
        if u["quadrant"] not in QUADS:
            continue
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
        u["in_3b_replay"] = (u["game_id"], u["round"], u["voter"]) in replayed
        buckets[u["quadrant"]].append(u)

    rng = random.Random(SEED)
    points = []
    for q in QUADS:
        lst = buckets[q]
        pri = [u for u in lst if u["in_3b_replay"]]
        rest = [u for u in lst if not u["in_3b_replay"]]
        rng.shuffle(rest)
        take = (pri + rest)[:args.per_quadrant]
        print(f"{q}: pool={len(lst)} (3B-replayed {len(pri)}) taken={len(take)}")
        points.extend(take)

    for u in points:
        u["point_id"] = (f"{u['quadrant'].split('_')[0][:4]}_"
                         f"{u['game_id'][:8]}_r{u['round']}_{u['voter']}")

    os.makedirs(CM.DATA, exist_ok=True)
    out = {"seed": SEED, "per_quadrant": args.per_quadrant,
           "pool_sizes": {q: len(buckets[q]) for q in QUADS},
           "skipped": skipped, "points": points}
    json.dump(out, open(CM.POINTS_JSON, "w"), ensure_ascii=False, indent=1)
    print(f"wrote {len(points)} points -> {CM.POINTS_JSON}; "
          f"skipped {len(skipped)}")


if __name__ == "__main__":
    main()
