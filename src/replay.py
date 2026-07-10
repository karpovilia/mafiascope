#!/usr/bin/env python3
"""
Counterfactual replay CLI — fork a recorded game at any snapshot step
and play the continuation N times ("хроники Амбера").

    python replay.py --game <game_id> --round 2 --seq 14 -n 5
    python replay.py --game <game_id> --list            # show fork points
    python replay.py --game <game_id> -r 2 -s 14 -n 10 --backend deepseek

Each replica is a normal game under logs/<new_game_id>/ whose setup event
carries forked_from / fork_point / fork_batch_id / replica_idx metadata —
prepare_viewer.py picks these up and the viewer shows them as branches.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import uuid

import yaml
from dotenv import load_dotenv

from game import MafiaGame
from llm_backend import shutdown_backends


def list_fork_points(state_path: str) -> None:
    print(f"Fork points in {state_path}:")
    with open(state_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            pend = r.get("pending", {})
            print(f"  R{r['round']:>2} seq={r['msg_seq']:>3}  phase={r['phase']:<14} "
                  f"next={pend.get('stage','?'):<12} alive={len(r['alive'])}")


def run_fork_batch(
    cfg: dict,
    parent_log_dir: str,
    round_num: int,
    msg_seq: int,
    n_replays: int,
    *,
    backend_override: str | None = None,
    parallel: bool = False,
    fork_batch_id: str | None = None,
    on_result=None,
) -> list[dict]:
    """Run N continuations of a snapshot. Returns per-replica results."""
    fork_batch_id = fork_batch_id or str(uuid.uuid4())
    results: list[dict] = []
    lock = threading.Lock()

    def one(replica_idx: int) -> None:
        try:
            game = MafiaGame.from_snapshot(
                cfg, parent_log_dir, round_num, msg_seq,
                backend_override=backend_override,
                fork_meta={
                    "fork_batch_id": fork_batch_id,
                    "replica_idx": replica_idx,
                    "intervention": None,  # M3: edit_utterance / inject_belief / override_night_action
                },
            )
            result = game.run()
            result["replica_idx"] = replica_idx
            result["fork_batch_id"] = fork_batch_id
        except Exception as exc:  # keep the batch alive if one replica dies
            result = {"replica_idx": replica_idx, "fork_batch_id": fork_batch_id,
                      "error": str(exc)}
        with lock:
            results.append(result)
        if on_result:
            on_result(result)

    if parallel and n_replays > 1:
        threads = [threading.Thread(target=one, args=(i,)) for i in range(n_replays)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    else:
        for i in range(n_replays):
            one(i)
    return results


def main() -> None:
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    parser = argparse.ArgumentParser(description="Fork a recorded Mafia game from a snapshot")
    parser.add_argument("-c", "--config", default="../configs/config.yaml")
    parser.add_argument("-d", "--logs-dir", default="../logs")
    parser.add_argument("--game", required=True, help="Parent game id (logs/<game_id>/)")
    parser.add_argument("-r", "--round", type=int, help="Fork round")
    parser.add_argument("-s", "--seq", type=int, help="Fork msg_seq (-1 = round end)")
    parser.add_argument("-n", "--num-replays", type=int, default=1)
    parser.add_argument("--backend", default=None, help="Override backend for ALL players")
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--list", action="store_true", help="List available fork points and exit")
    parser.add_argument("--no-introspection", action="store_true")
    args = parser.parse_args()

    parent_log_dir = os.path.join(args.logs_dir, args.game)
    state_path = os.path.join(parent_log_dir, "state.jsonl")
    if not os.path.isfile(state_path):
        raise SystemExit(f"No state.jsonl in {parent_log_dir} — the parent game was "
                         f"recorded without snapshots (game.snapshots: true).")

    if args.list:
        list_fork_points(state_path)
        return

    if args.round is None or args.seq is None:
        parser.error("--round and --seq are required (or use --list)")

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if args.no_introspection:
        cfg.setdefault("introspection", {})["enabled"] = False

    results = run_fork_batch(
        cfg, parent_log_dir, args.round, args.seq, args.num_replays,
        backend_override=args.backend, parallel=args.parallel,
    )

    print(f"\n{'═'*60}\n  FORK BATCH — parent {args.game[:8]} @ R{args.round}.{args.seq}\n{'═'*60}")
    wins: dict[str, int] = {}
    for r in sorted(results, key=lambda x: x.get("replica_idx", 0)):
        w = r.get("winner", "error")
        wins[w] = wins.get(w, 0) + 1
        print(f"  run#{r.get('replica_idx')}: {w} "
              f"({r.get('rounds', '?')} rounds)  {r.get('game_id', '')[:8]}")
    for w, c in sorted(wins.items(), key=lambda x: -x[1]):
        print(f"  {w}: {c}/{len(results)}")

    shutdown_backends()


if __name__ == "__main__":
    main()
