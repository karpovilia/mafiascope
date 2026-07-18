#!/usr/bin/env python3
"""Driver: top up the gpt-4o-mini MafiaScope corpus to 100 completed games.

Runs games from config_chatgpt_corpus.yaml in parallel worker processes
(OpenAI backend => IO-bound, CPU load is negligible per game) until TARGET
completed games are recorded in the ledger:

    <log_dir>/gpt100_done.jsonl

Idempotent resume: on restart, completed (non-error) ledger records are
counted and only the remainder is generated. Failed/rejected attempts are
recorded with an "error" field and do not count.

Budget guard: exact token usage is taken from the OpenAI `usage` field of
every response (llm_backend.get_usage_totals, per-process delta per game).
gpt-4o-mini pricing: $0.15/1M input, $0.60/1M output. If the projected total
cost for TARGET games exceeds BUDGET_STOP_USD, the driver stops submitting
new games, drains running ones, and exits with a report line.

Validity guard: a game is rejected (not counted) if it made zero successful
API calls or if more than half of its speech records (intro + day_discuss)
are ERROR strings — that means an outage ate the game, not a played game.

Usage (from src/, like main.py):
    python run_gpt100.py -c ../configs/config_chatgpt_corpus.yaml -n 72
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import yaml
from dotenv import load_dotenv

PRICE_IN = 0.15 / 1e6   # gpt-4o-mini USD per input token
PRICE_OUT = 0.60 / 1e6  # gpt-4o-mini USD per output token
BUDGET_STOP_USD = 60.0

SRC_DIR = os.path.dirname(os.path.abspath(__file__))


def _run_one_game(config_path: str, seed: int) -> dict:
    """Worker: play one full game, return a ledger record."""
    os.chdir(SRC_DIR)  # log_dir "../logs" must resolve from src/
    load_dotenv(os.path.join(SRC_DIR, "..", ".env"))
    # imports inside the worker so the parent process stays light
    from game import MafiaGame
    import llm_backend

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    u0 = llm_backend.get_usage_totals()
    random.seed(seed)  # reproducible roster/name shuffle
    t0 = time.monotonic()
    game = MafiaGame(cfg)
    result = game.run()
    dt = time.monotonic() - t0
    u1 = llm_backend.get_usage_totals()
    usage = {k: u1[k] - u0[k] for k in u1}
    cost = usage["prompt_tokens"] * PRICE_IN + usage["completion_tokens"] * PRICE_OUT

    # validity: reject outage-eaten games
    err = tot = 0
    with open(os.path.join(game.game_log_dir, "game.jsonl"), encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("kind") in ("intro", "day_discuss"):
                tot += 1
                if str(rec.get("response", "")).startswith("ERROR:"):
                    err += 1
    err_frac = err / tot if tot else 1.0

    out = {
        "seed": seed,
        "game_id": result["game_id"],
        "winner": result["winner"],
        "rounds": result["rounds"],
        "usage": usage,
        "cost_usd": round(cost, 4),
        "err_frac": round(err_frac, 3),
        "duration_s": round(dt, 1),
        "ts": time.time(),
    }
    if usage["calls"] == 0 or err_frac > 0.5:
        out["error"] = f"rejected: calls={usage['calls']} err_frac={err_frac:.2f}"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", default="../configs/config_chatgpt_corpus.yaml")
    ap.add_argument("-n", "--target", type=int, default=72,
                    help="completed NEW games required in the ledger")
    ap.add_argument("-w", "--workers", type=int, default=6)
    ap.add_argument("--seed-base", type=int, default=7000)
    args = ap.parse_args()

    os.chdir(SRC_DIR)
    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    log_root = cfg.get("logging", {}).get("log_dir", "../logs")
    os.makedirs(log_root, exist_ok=True)
    ledger_path = os.path.join(log_root, "gpt100_done.jsonl")

    done = 0
    cost_done = 0.0
    used_seeds = set()
    if os.path.exists(ledger_path):
        with open(ledger_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                used_seeds.add(rec.get("seed"))
                if "error" not in rec:
                    done += 1
                    cost_done += rec.get("cost_usd", 0.0)
    print(f"[gpt100] resume: done={done}/{args.target} cost so far=${cost_done:.2f} "
          f"ledger={ledger_path}", flush=True)

    def next_seed() -> int:
        s = args.seed_base
        while s in used_seeds:
            s += 1
        used_seeds.add(s)
        return s

    stop_reason = None
    t_start = time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        while done < args.target and (futures or stop_reason is None):
            # top up the pool
            while stop_reason is None and len(futures) < args.workers and \
                    done + len(futures) < args.target + 2:  # small overshoot margin for retries
                s = next_seed()
                futures[pool.submit(_run_one_game, args.config, s)] = s
            if not futures:
                break
            fut = next(as_completed(futures))
            seed = futures.pop(fut)
            try:
                rec = fut.result()
            except Exception as exc:
                rec = {"seed": seed, "error": f"exception: {exc}", "ts": time.time()}
            with open(ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if "error" not in rec:
                done += 1
                cost_done += rec["cost_usd"]
                proj = cost_done / done * args.target
                print(f"[gpt100] done={done}/{args.target} seed={seed} "
                      f"winner={rec['winner']} rounds={rec['rounds']} "
                      f"err_frac={rec['err_frac']} cost=${rec['cost_usd']:.3f} "
                      f"total=${cost_done:.2f} proj=${proj:.2f} "
                      f"elapsed={time.monotonic()-t_start:.0f}s", flush=True)
                if proj > BUDGET_STOP_USD:
                    stop_reason = (f"budget projection ${proj:.2f} > ${BUDGET_STOP_USD}: "
                                   f"stopping after {done} games")
            else:
                print(f"[gpt100] FAILED seed={seed}: {rec['error']}", flush=True)

    if stop_reason:
        print(f"[gpt100] STOP: {stop_reason}", flush=True)
    print(f"[gpt100] finished: done={done}/{args.target} total_cost=${cost_done:.2f} "
          f"wall={time.monotonic()-t_start:.0f}s", flush=True)


if __name__ == "__main__":
    main()
