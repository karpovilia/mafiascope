#!/usr/bin/env python3
"""Seed-grid driver for the multilingual Qwen outcome experiment.

Runs N games for ONE language config on a fixed, paired seed grid
(seed = seed_base + i, i=0..N-1 — identical grid across languages so games
pair 1:1 by seed for McNemar). The heavy local model loads ONCE (get_backend
caches by backend name) and is reused for every game in the process.

Idempotent resume: completed seeds are appended to
  <log_dir>/lang_<lang>_done.jsonl
and skipped on restart. Each finished game also records
  {seed, game_id, winner, rounds, lang} there for corpus registration.

Usage (run from src/, like main.py):
  python run_lang_games.py -c ../configs/config_lang_zh_qwen.yaml -n 50
  python run_lang_games.py -c ../configs/config_lang_zh_qwen.yaml -n 50 --seed-base 9000
"""
from __future__ import annotations
import argparse, json, os, random, time
import yaml
from dotenv import load_dotenv
from game import MafiaGame
from llm_backend import shutdown_backends


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", required=True)
    ap.add_argument("-n", "--num-games", type=int, default=50)
    ap.add_argument("--seed-base", type=int, default=9000)
    args = ap.parse_args()

    cfg = load_config(args.config)
    lang = cfg["game"]["language"]
    log_root = cfg.get("logging", {}).get("log_dir", "../logs")
    os.makedirs(log_root, exist_ok=True)
    ledger_path = os.path.join(log_root, f"lang_{lang}_done.jsonl")

    done_seeds = set()
    if os.path.exists(ledger_path):
        with open(ledger_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                        if "error" not in rec:   # ERR seeds are retried, not skipped
                            done_seeds.add(rec["seed"])
                    except Exception:
                        pass
    print(f"[run_lang_games] lang={lang} target={args.num_games} "
          f"already_done={len(done_seeds)} ledger={ledger_path}")

    t0 = time.monotonic()
    for i in range(args.num_games):
        seed = args.seed_base + i
        if seed in done_seeds:
            print(f"  [skip] seed={seed} already done")
            continue
        random.seed(seed)  # reproducible roster/name shuffle per paired seed
        try:
            game = MafiaGame(cfg)
            result = game.run()
            rec = {"seed": seed, "game_id": result["game_id"],
                   "winner": result["winner"], "rounds": result["rounds"],
                   "lang": lang, "log_dir": game.game_log_dir}
        except Exception as exc:
            rec = {"seed": seed, "error": str(exc), "lang": lang}
            print(f"  [game seed={seed}] ERROR: {exc}")
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"  [done] seed={seed} -> {rec.get('winner','ERR')} "
              f"in {rec.get('rounds','?')} rounds ({rec.get('game_id','')[:8]})")

    print(f"[run_lang_games] lang={lang} finished in {time.monotonic()-t0:.0f}s")
    shutdown_backends()


if __name__ == "__main__":
    main()
