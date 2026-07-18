#!/usr/bin/env python3
"""Idea 06, step 3: pick ~20 maximally dissimilar variants per point.

Selection (per brief, incl. the vote-coverage trap):
  1. seed = the FACTUAL reply from the parent trace (variant 0, no re-gen);
  2. target coverage: for every unique legal vote target present among the
     samples but not yet covered by the selected set, add the sample with
     that target that is farthest (max-min cosine distance) from the
     current selection — plain text cosine cannot distinguish identical
     votes with different rationales;
  3. fill to N_SELECT by farthest-point (maxmin cosine) over the remainder.

Embeddings: intfloat/multilingual-e5-small with "query: " prefix,
L2-normalized (same as mafia2/src/state_clustering.py), CPU.

Diversity stat: mean pairwise cosine of the selected 20 vs the mean over
random 20-subsets (seeded), reported per point into selected.json.

Usage: mafia/.venv/bin/python select_diverse.py [--points p1,p2]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as CM  # noqa: E402

E5_MODEL = "intfloat/multilingual-e5-small"
SEED = 20260716
N_RANDOM_DRAWS = 200


_MODEL = None


def embed(texts: list[str]) -> np.ndarray:
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(E5_MODEL, device="cpu")
    return _MODEL.encode([f"query: {t}" for t in texts], batch_size=64,
                         show_progress_bar=False, normalize_embeddings=True)


def maxmin_pick(emb: np.ndarray, selected: list[int],
                candidates: list[int]) -> int:
    """Candidate with max (min distance to selected) = min (max cosine)."""
    sel = emb[selected]                       # [s, d]
    sims = emb[candidates] @ sel.T            # [c, s]
    return candidates[int(np.argmin(sims.max(axis=1)))]


def mean_pairwise_cos(emb: np.ndarray, idx: list[int]) -> float:
    sub = emb[idx]
    g = sub @ sub.T
    n = len(idx)
    return float((g.sum() - np.trace(g)) / (n * (n - 1)))


def select_point(p: dict, n_select: int) -> dict:
    pid = p["point_id"]
    bundle = CM.fork_and_prompt(p["game_id"], p["round"], p["voter"])
    legal = set(bundle["alive"]) - {p["voter"]}
    factual = bundle["factual_reply"]

    rows = CM.load_jsonl(os.path.join(CM.point_dir(pid), "samples.jsonl"))
    rows.sort(key=lambda r: r["sample_idx"])

    # texts[0] = factual (variant 0); dedup exact duplicates of any text
    texts = [factual]
    meta = [{"variant_idx": 0, "source": "factual", "sample_idx": None}]
    seen = {factual}
    for r in rows:
        t = r["text"].strip()
        if not t or t in seen:
            continue
        seen.add(t)
        texts.append(t)
        meta.append({"source": "sampled", "sample_idx": r["sample_idx"]})
    targets = [CM.parse_vote(t, legal) for t in texts]

    emb = embed(texts)
    selected = [0]
    stages = {0: "factual"}

    # stage 2: vote-target coverage (unique targets incl. "no valid vote")
    all_targets = sorted({t for t in targets if t is not None})
    if targets.count(None):
        all_targets.append(None)
    for tgt in all_targets:
        if len(selected) >= n_select:
            break
        if tgt in {targets[i] for i in selected}:
            continue
        cands = [i for i in range(len(texts))
                 if i not in selected and targets[i] == tgt]
        if not cands:
            continue
        pick = maxmin_pick(emb, selected, cands)
        selected.append(pick)
        stages[pick] = "target_coverage"

    # stage 3: farthest-point fill
    while len(selected) < min(n_select, len(texts)):
        cands = [i for i in range(len(texts)) if i not in selected]
        pick = maxmin_pick(emb, selected, cands)
        selected.append(pick)
        stages[pick] = "maxmin"

    # diversity stat: selected vs random 20-subsets of the sampled pool
    sel_cos = mean_pairwise_cos(emb, selected)
    rng = np.random.default_rng(SEED)
    rand_cos = [mean_pairwise_cos(
        emb, list(rng.choice(len(texts), size=len(selected), replace=False)))
        for _ in range(N_RANDOM_DRAWS)]

    variants = []
    for k, i in enumerate(selected):
        variants.append({
            "variant_idx": k, "source": meta[i]["source"],
            "sample_idx": meta[i]["sample_idx"], "stage": stages[i],
            "vote_target": targets[i], "text": texts[i],
        })
    return {
        "point_id": pid, "n_samples_raw": len(rows),
        "n_unique_texts": len(texts) - 1, "n_selected": len(selected),
        "target_dist_samples": {str(t): targets[1:].count(t)
                                for t in all_targets},
        "targets_selected": [v["vote_target"] for v in variants],
        "diversity": {
            "mean_pairwise_cos_selected": sel_cos,
            "mean_pairwise_cos_random20": float(np.mean(rand_cos)),
            "random20_std": float(np.std(rand_cos)),
            "n_random_draws": N_RANDOM_DRAWS,
            "embed_model": E5_MODEL,
        },
        "variants": variants,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", default=None)
    ap.add_argument("--n-select", type=int, default=CM.N_SELECT)
    args = ap.parse_args()

    points = json.load(open(CM.POINTS_JSON))["points"]
    if args.points:
        want = set(args.points.split(","))
        points = [p for p in points if p["point_id"] in want]

    for p in points:
        path = os.path.join(CM.point_dir(p["point_id"]), "samples.jsonl")
        if not os.path.isfile(path):
            print(f"SKIP {p['point_id']}: no samples.jsonl")
            continue
        out = select_point(p, args.n_select)
        opath = os.path.join(CM.point_dir(p["point_id"]), "selected.json")
        json.dump(out, open(opath, "w"), ensure_ascii=False, indent=1)
        d = out["diversity"]
        print(f"{p['point_id']}: {out['n_samples_raw']} raw / "
              f"{out['n_unique_texts']} unique -> {out['n_selected']} "
              f"(targets {out['target_dist_samples']}); "
              f"cos sel={d['mean_pairwise_cos_selected']:.3f} "
              f"rand={d['mean_pairwise_cos_random20']:.3f}")


if __name__ == "__main__":
    main()
