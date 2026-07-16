#!/usr/bin/env python3
"""Finish interrupted games from their state.jsonl snapshots.

The V100 Qwen arm array (run_v100_array.sbatch) TIMEOUTs at 16h with games
cut off mid-play. Every game writes phase-boundary snapshots to state.jsonl
(pending stage + remaining players + full player context), and the engine can
restore from any snapshot via MafiaGame.from_snapshot + run(resume=...). This
driver walks a logs dir, finds unfinished games that still have a usable
snapshot, and plays each one to completion (a new game_id, forked_from the
parent) with the parent's own arm settings.

Arm reconstruction: the three live arms (base / fb1 / fb2) differ only in
introspection.feedback_to_context and feedback_order, both logged in each
game's setup record. We load a base config and override those two fields per
game, so one run resumes all arms correctly. (No mixed arm ran — no game
carries feedback_players.)

Idempotent + array-shardable:
  * skips finished games (game_over present),
  * skips games with an empty/absent snapshot (nothing to resume),
  * skips games already continued once (their id appears as some game's
    forked_from) — so re-running does not fork the same parent twice,
  * skips games touched within --min-idle-sec (a running job still owns them),
  * --shard-index/--shard-count partition eligible games across array tasks.

Usage (single GPU, all arms):
    python src/resume_games.py --logs logs --config configs/config_v100_qwen7b.yaml \
        --parallel --max-parallel 10

Array shard (one shard per task, see resume_v100_array.sbatch):
    python src/resume_games.py --logs logs --config configs/config_v100_qwen7b.yaml \
        --parallel --max-parallel 10 --shard-index $TASK --shard-count $N

Multilingual mode (--lang-map): the map JSON {game_id: {"lang": .., "seed": ..}}
names the interrupted games of the lang seed-grid runs. Only mapped games (or
their resume descendants — chains are followed via setup.forked_from) are
resumed, each with its own configs/hpc_lang/config_lang_<lang>_a100.yaml, and a
finished child is appended to logs/lang_<lang>_done.jsonl with the original
seed, so run_lang_games.py never regenerates that slot.
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


def read_setup(game_dir: str) -> dict | None:
    p = os.path.join(game_dir, "game.jsonl")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            first = f.readline()
        rec = json.loads(first)
        return rec if rec.get("kind") == "setup" else None
    except (OSError, json.JSONDecodeError):
        return None


def is_finished(game_dir: str) -> bool:
    """game_over marker lives near the tail of game.jsonl."""
    p = os.path.join(game_dir, "game.jsonl")
    try:
        with open(p, "rb") as f:
            f.seek(-min(4096, os.path.getsize(p)), 2)
            return b'"game_over"' in f.read()
    except OSError:
        return False


def last_snapshot(game_dir: str, tail_bytes: int = 16 << 20) -> dict | None:
    """Latest state.jsonl record = the resume point (round, msg_seq, pending).

    Snapshot lines carry full player context and can be large, so read only a
    tail chunk and take the last complete JSON line (keeps --dry-run light)."""
    p = os.path.join(game_dir, "state.jsonl")
    try:
        size = os.path.getsize(p)
    except OSError:
        return None
    if size == 0:
        return None
    try:
        with open(p, "rb") as f:
            f.seek(-min(tail_bytes, size), 2)
            chunk = f.read()
    except OSError:
        return None
    for line in reversed(chunk.split(b"\n")):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            # truncated first line of the tail window; stop and give up
            return None
    return None


def root_ancestor(logs_root: str, gid: str) -> str:
    """Follow setup.forked_from up to the original (non-forked) game."""
    cur, seen = gid, {gid}
    while True:
        s = read_setup(os.path.join(logs_root, cur))
        parent = (s or {}).get("forked_from")
        if not parent or parent in seen:
            return cur
        seen.add(parent)
        cur = parent


def resumed_parents(logs_root: str) -> set[str]:
    """Parent ids that already have a continuation (some setup.forked_from)."""
    parents: set[str] = set()
    for gid in os.listdir(logs_root):
        s = read_setup(os.path.join(logs_root, gid))
        if s and s.get("forked_from"):
            parents.add(s["forked_from"])
    return parents


def eligible_games(logs_root: str, min_idle_sec: float) -> list[dict]:
    """Unfinished games with a usable snapshot, not active, not already resumed."""
    already = resumed_parents(logs_root)
    now = time.time()
    out: list[dict] = []
    for gid in sorted(os.listdir(logs_root)):
        gdir = os.path.join(logs_root, gid)
        if not os.path.isdir(gdir):
            continue
        setup = read_setup(gdir)
        if not setup:
            continue
        if gid in already:
            continue
        if is_finished(gdir):
            continue
        gpath = os.path.join(gdir, "game.jsonl")
        if now - os.path.getmtime(gpath) < min_idle_sec:
            continue  # a running job still owns this game
        snap = last_snapshot(gdir)
        if snap is None:
            continue  # intro-only / empty state — nothing to restore
        out.append({
            "game_id": gid,
            "dir": gdir,
            "round": snap["round"],
            "msg_seq": snap["msg_seq"],
            "feedback_to_context": bool(setup.get("feedback_to_context", False)),
            "feedback_order": int(setup.get("feedback_order", 2)),
        })
    return out


def run_one_resume(base_cfg: dict, item: dict, results: list,
                   logs_root: str | None = None) -> None:
    gid = item["game_id"]
    try:
        cfg = json.loads(json.dumps(base_cfg))  # deep copy
        intro = cfg.setdefault("introspection", {})
        intro["feedback_to_context"] = item["feedback_to_context"]
        intro["feedback_order"] = item["feedback_order"]
        game = MafiaGame.from_snapshot(
            cfg, item["dir"], item["round"], item["msg_seq"],
            fork_meta={"resume_of": gid},
        )
        result = game.run()
        result["parent"] = gid
        results.append(result)
        print(f"  [{gid[:8]} @R{item['round']}.{item['msg_seq']}] "
              f"-> {result.get('winner','?')} in {result.get('rounds','?')} rounds "
              f"(new {result['game_id'][:8]})")
        if item.get("lang") and logs_root:
            # register the finished slot so run_lang_games.py skips this seed
            ledger = os.path.join(logs_root, f"lang_{item['lang']}_done.jsonl")
            rec = {"seed": item["seed"], "game_id": result["game_id"],
                   "winner": result.get("winner"), "rounds": result.get("rounds"),
                   "lang": item["lang"], "log_dir": game.game_log_dir,
                   "forked_from": gid}
            with open(ledger, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"  [{gid[:8]}] RESUME ERROR: {exc}")
        results.append({"parent": gid, "error": str(exc)})


def main() -> None:
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    ap = argparse.ArgumentParser(description="Finish interrupted Mafia games from snapshots")
    ap.add_argument("--logs", default="logs", help="dir of parent game logs")
    ap.add_argument("--config", default="../configs/config_v100_qwen7b.yaml",
                    help="base arm config (feedback fields overridden per game)")
    ap.add_argument("--parallel", action="store_true")
    ap.add_argument("--max-parallel", type=int, default=10)
    ap.add_argument("--min-idle-sec", type=float, default=900,
                    help="skip games modified within this window (still running)")
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="list eligible games and exit (no compute)")
    ap.add_argument("--lang-map", default=None,
                    help="JSON {game_id: {lang, seed}} of interrupted multilingual "
                         "seed-grid games; enables per-language configs + ledger append")
    ap.add_argument("--configs-dir", default="../configs/hpc_lang",
                    help="dir with config_lang_<lang>_a100.yaml (lang-map mode)")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    items = eligible_games(args.logs, args.min_idle_sec)
    lang_map: dict | None = None
    if args.lang_map:
        with open(args.lang_map, "r", encoding="utf-8") as f:
            lang_map = json.load(f)
        kept = []
        for it in items:
            ent = lang_map.get(it["game_id"]) \
                or lang_map.get(root_ancestor(args.logs, it["game_id"]))
            if not ent:
                continue  # not a lang-grid game — out of scope in this mode
            it["lang"], it["seed"] = ent["lang"], ent["seed"]
            cfg_path = os.path.join(args.configs_dir, f"config_lang_{it['lang']}_a100.yaml")
            if not os.path.isfile(cfg_path):
                raise SystemExit(f"missing lang config: {cfg_path}")
            kept.append(it)
        items = kept
    if args.shard_count > 1:
        items = [it for i, it in enumerate(items) if i % args.shard_count == args.shard_index]
    if args.limit:
        items = items[:args.limit]

    arms = {}
    for it in items:
        key = "base" if not it["feedback_to_context"] else f"fb{it['feedback_order']}"
        arms[key] = arms.get(key, 0) + 1
    print(f"eligible to resume: {len(items)} games "
          f"(shard {args.shard_index}/{args.shard_count}) | arms: {arms}")
    if args.dry_run or not items:
        for it in items:
            extra = f" lang={it['lang']} seed={it['seed']}" if it.get("lang") else ""
            print(f"  {it['game_id']}  R{it['round']}.{it['msg_seq']}  "
                  f"ftc={it['feedback_to_context']} order={it['feedback_order']}{extra}")
        return

    if lang_map is not None:
        cfg_cache: dict[str, dict] = {}

        def cfg_for(it: dict) -> dict:
            lang = it["lang"]
            if lang not in cfg_cache:
                cfg_cache[lang] = load_config(
                    os.path.join(args.configs_dir, f"config_lang_{lang}_a100.yaml"))
            return cfg_cache[lang]
    else:
        base_cfg = load_config(args.config)

        def cfg_for(it: dict) -> dict:
            return base_cfg
    results: list[dict] = []
    t0 = time.monotonic()

    if args.parallel and len(items) > 1:
        sem = threading.Semaphore(args.max_parallel)
        threads = []

        def worker(it):
            with sem:
                run_one_resume(cfg_for(it), it, results, logs_root=args.logs)

        for it in items:
            t = threading.Thread(target=worker, args=(it,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
    else:
        for it in items:
            run_one_resume(cfg_for(it), it, results, logs_root=args.logs)

    elapsed = time.monotonic() - t0
    fin = [r for r in results if "winner" in r]
    err = [r for r in results if "error" in r]
    print(f"\nresumed {len(results)} games in {elapsed:.1f}s: "
          f"{len(fin)} finished, {len(err)} errored")
    shutdown_backends()


if __name__ == "__main__":
    main()
