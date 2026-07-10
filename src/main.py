#!/usr/bin/env python3
"""
LLM Mafia — entry point.

    python main.py                          # 1 game
    python main.py -c config_local.yaml     # local model
    python main.py -n 10 --parallel         # 10 games batched on GPU
"""

from __future__ import annotations

import argparse
import json
import os
import random
import threading
import time

import yaml
from dotenv import load_dotenv

from game import MafiaGame
from llm_backend import shutdown_backends


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_one_game(cfg: dict, game_idx: int, results: list) -> None:
    """Run a single game. Designed to be called from a thread."""
    try:
        game = MafiaGame(cfg)
        result = game.run()
        result["game_idx"] = game_idx
        result["log_dir"] = game.game_log_dir
        results.append(result)
        winner = result.get("winner", "?")
        rounds = result.get("rounds", "?")
        print(f"\n  [Game {game_idx}] finished: {winner} wins in {rounds} rounds")
    except Exception as exc:
        print(f"\n  [Game {game_idx}] ERROR: {exc}")
        results.append({"game_idx": game_idx, "error": str(exc)})


def main() -> None:
    # Load .env from project root (one level up from src/)
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    parser = argparse.ArgumentParser(description="LLM Mafia game with introspection")
    parser.add_argument("-c", "--config", default="../configs/config.yaml", help="Path to config YAML")
    parser.add_argument("-n", "--num-games", type=int, default=None,
                        help="Number of games (overrides config)")
    parser.add_argument("--parallel", action="store_true",
                        help="Run games in parallel threads (use with transformers_batched)")
    parser.add_argument("--no-introspection", action="store_true")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.seed is not None:
        random.seed(args.seed)

    if args.no_introspection:
        cfg.setdefault("introspection", {})["enabled"] = False

    # CLI flag overrides config, config overrides default of 1
    n = args.num_games or cfg.get("game", {}).get("num_games", 1)
    results: list[dict] = []
    t0 = time.monotonic()

    if args.parallel and n > 1:
        print(f"\n{'━'*60}")
        print(f"  PARALLEL: {n} games")
        print(f"{'━'*60}")

        threads = []
        for i in range(n):
            t = threading.Thread(target=run_one_game, args=(cfg, i + 1, results))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()
    else:
        for i in range(n):
            print(f"\n{'━'*60}")
            print(f"  GAME {i + 1} / {n}")
            print(f"{'━'*60}")
            run_one_game(cfg, i + 1, results)

    elapsed = time.monotonic() - t0

    # Summary
    print(f"\n{'═'*60}")
    print(f"  SUMMARY: {len(results)} games in {elapsed:.1f}s")
    print(f"{'═'*60}")
    wins = {}
    for r in results:
        w = r.get("winner", "error")
        wins[w] = wins.get(w, 0) + 1
    for w, c in sorted(wins.items(), key=lambda x: -x[1]):
        print(f"  {w}: {c} wins")

    if n > 1:
        with open("results.json", "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"  Saved to results.json")

    shutdown_backends()


if __name__ == "__main__":
    main()
