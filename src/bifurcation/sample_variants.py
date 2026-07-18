#!/usr/bin/env python3
"""Idea 06, step 2: ~500 independent high-temperature variants per point.

Each request replays the agent's frozen pre-action context verbatim
(pre-vote snapshot messages + the exact vote prompt the engine sent) —
NO hint about previous variants or "play differently" (observer effect is
forbidden by design).  Sampling params: temperature=1.2, max_tokens=400
(game value), model deepseek-chat.

Raw responses + API usage go to data/bifurcation/<point_id>/samples.jsonl
(resumable: existing sample_idx are skipped).  Heavy data is git-ignored
(mafia2/.gitignore: data/).

Usage: mafia/.venv/bin/python sample_variants.py [--points p1,p2] [-n 500]
       [--workers 10]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as CM  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", default=None,
                    help="comma-separated point_ids (default: all)")
    ap.add_argument("-n", "--nsamples", type=int, default=CM.N_SAMPLES)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    key = CM.load_dotenv_key()
    points = json.load(open(CM.POINTS_JSON))["points"]
    if args.points:
        want = set(args.points.split(","))
        points = [p for p in points if p["point_id"] in want]
    print(f"{len(points)} points, {args.nsamples} samples each")

    jobs = []          # (point, sample_idx)
    ctx = {}           # point_id -> (messages, meta)
    for p in points:
        b = CM.fork_and_prompt(p["game_id"], p["round"], p["voter"])
        assert b is not None, f"bundle vanished for {p['point_id']}"
        msgs = [dict(m) for m in b["pre_messages"]]
        msgs.append({"role": "user", "content": b["prompt"]})
        ctx[p["point_id"]] = msgs
        path = os.path.join(CM.point_dir(p["point_id"]), "samples.jsonl")
        done = set()
        if os.path.isfile(path):
            for r in CM.load_jsonl(path):
                done.add(r["sample_idx"])
        jobs += [(p, i) for i in range(args.nsamples) if i not in done]
        if done:
            print(f"  {p['point_id']}: {len(done)} cached")

    print(f"{len(jobs)} API calls to make")
    lock = threading.Lock()
    fhs = {}
    stats = {"n": 0, "prompt_toks": 0, "cache_hit": 0, "compl_toks": 0}
    t0 = time.time()

    def one(p, idx):
        res = CM.call_deepseek(key, ctx[p["point_id"]],
                               temperature=CM.TEMPERATURE,
                               max_tokens=CM.MAX_TOKENS)
        u = res["usage"]
        row = {"point_id": p["point_id"], "sample_idx": idx,
               "text": res["content"],
               "model": CM.MODEL, "temperature": CM.TEMPERATURE,
               "max_tokens": CM.MAX_TOKENS,
               "usage": {k: u.get(k) for k in
                         ("prompt_tokens", "completion_tokens",
                          "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")}}
        with lock:
            pid = p["point_id"]
            if pid not in fhs:
                fhs[pid] = open(os.path.join(CM.point_dir(pid),
                                             "samples.jsonl"), "a")
            fhs[pid].write(json.dumps(row, ensure_ascii=False) + "\n")
            fhs[pid].flush()
            stats["n"] += 1
            stats["prompt_toks"] += u.get("prompt_tokens", 0) or 0
            stats["cache_hit"] += u.get("prompt_cache_hit_tokens", 0) or 0
            stats["compl_toks"] += u.get("completion_tokens", 0) or 0
            if stats["n"] % 50 == 0:
                print(f"  ..{stats['n']}/{len(jobs)} "
                      f"({time.time()-t0:.0f}s, "
                      f"in={stats['prompt_toks']/1e6:.2f}M "
                      f"hit={stats['cache_hit']/1e6:.2f}M "
                      f"out={stats['compl_toks']/1e6:.2f}M)", flush=True)

    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one, p, i): (p["point_id"], i) for p, i in jobs}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as exc:
                failed += 1
                print(f"  FAILED {futs[fut]}: {exc}", flush=True)
    for fh in fhs.values():
        fh.close()

    # cost at deepseek-chat prices (2026-07: $0.27/M in miss, $0.07/M in hit,
    # $1.10/M out)
    miss = stats["prompt_toks"] - stats["cache_hit"]
    cost = miss / 1e6 * 0.27 + stats["cache_hit"] / 1e6 * 0.07 \
        + stats["compl_toks"] / 1e6 * 1.10
    print(f"done: {stats['n']} ok, {failed} failed, {time.time()-t0:.0f}s; "
          f"tokens in={stats['prompt_toks']/1e6:.2f}M "
          f"(hit {stats['cache_hit']/1e6:.2f}M) "
          f"out={stats['compl_toks']/1e6:.2f}M; est cost ${cost:.2f}")


if __name__ == "__main__":
    main()
