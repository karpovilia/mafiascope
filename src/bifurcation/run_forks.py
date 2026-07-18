#!/usr/bin/env python3
"""Idea 06, step 4: fork the parent game at each bifurcation point and play
one continuation per selected variant, with the variant text injected as the
forked player's own reply on their vote turn.

Mechanics = idea 3B replay (scratchpad/idea3b/replay_moves.py), but instead
of a canned "VOTE: X" the FULL sampled utterance is committed as the
player's next respond() output (monkeypatch on the single Player instance;
no engine edit).  The engine then parses the vote out of the text with its
own ACTION_PATTERNS — a variant without a legal VOTE: line simply casts no
vote, which is honest counterfactual behavior.

Quarantine: every fork's setup event carries forked_from / fork_point plus
  intervention = {type: "inject_message", player, variant_idx, source,
                  temperature, experiment: "bifurcation_2026_07_16"}
so metrics_lib.select_corpus excludes them from observational corpora.

Introspection and snapshots are OFF in forks (only the outcome is needed).
Results appended to data/bifurcation/fork_results.jsonl (resumable).

Usage: mafia/.venv/bin/python run_forks.py [--points p1,p2] [--workers 8]
       [--variants 0,1,2]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as CM  # noqa: E402

sys.path.insert(0, CM.MAFIA_SRC)
load_dotenv(os.path.join(CM.MAFIA, ".env"))

from game import MafiaGame                  # noqa: E402
from llm_backend import shutdown_backends   # noqa: E402

RESULTS = os.path.join(CM.DATA, "fork_results.jsonl")
CONFIG = os.path.join(CM.MAFIA, "configs/config_deepseek.yaml")
# BIF_EXPERIMENT overrides the quarantine tag (random-baseline control run
# tags its forks bifurcation_random_2026_07_18)
EXPERIMENT = os.environ.get("BIF_EXPERIMENT", "bifurcation_2026_07_16")


def force_next_turn(player, forced_text: str) -> None:
    """Patch this Player instance so its NEXT respond() commits forced_text
    without calling the backend; all later turns are normal."""
    orig = player.respond
    fired = {"v": False}

    def patched(prompt):
        if not fired["v"]:
            fired["v"] = True
            player._messages.append({"role": "user", "content": prompt})
            player._messages.append({"role": "assistant", "content": forced_text})
            return forced_text
        return orig(prompt)

    player.respond = patched


def run_one_fork(cfg: dict, point: dict, variant: dict) -> dict:
    r, seq = point["fork"]
    game = MafiaGame.from_snapshot(
        cfg, os.path.join(CM.LOGS, point["game_id"]), r, seq,
        fork_meta={
            "fork_batch_id": None,
            "intervention": {
                "type": "inject_message",
                "player": point["voter"],
                "variant_idx": variant["variant_idx"],
                "source": variant["source"],           # factual | sampled
                "sample_idx": variant["sample_idx"],
                "temperature": (None if variant["source"] == "factual"
                                else CM.TEMPERATURE),
                "experiment": EXPERIMENT,
            },
        },
    )
    p = next((q for q in game.players if q.player_name == point["voter"]), None)
    if p is None:
        raise RuntimeError(f"voter {point['voter']} not in fork")
    force_next_turn(p, variant["text"])
    return game.run()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", default=None)
    ap.add_argument("--variants", default=None,
                    help="comma-separated variant_idx subset (debug)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    cfg = copy.deepcopy(yaml.safe_load(open(CONFIG)))
    cfg.setdefault("introspection", {})["enabled"] = False
    cfg.setdefault("game", {})["snapshots"] = False
    cfg["game"]["language"] = "en"      # all selected parents are EN
    cfg.setdefault("logging", {})["log_dir"] = CM.LOGS
    cfg["logging"]["console_level"] = "warn"

    points = json.load(open(CM.POINTS_JSON))["points"]
    if args.points:
        want = set(args.points.split(","))
        points = [p for p in points if p["point_id"] in want]
    vsub = (set(int(x) for x in args.variants.split(","))
            if args.variants else None)

    done = set()
    if os.path.isfile(RESULTS):
        for row in CM.load_jsonl(RESULTS):
            if row.get("winner"):
                done.add((row["point_id"], row["variant_idx"]))

    jobs = []
    for p in points:
        spath = os.path.join(CM.point_dir(p["point_id"]), "selected.json")
        if not os.path.isfile(spath):
            print(f"SKIP {p['point_id']}: no selected.json")
            continue
        sel = json.load(open(spath))
        for v in sel["variants"]:
            if vsub is not None and v["variant_idx"] not in vsub:
                continue
            if (p["point_id"], v["variant_idx"]) in done:
                continue
            jobs.append((p, v))
    print(f"{len(jobs)} forks to run ({len(done)} cached)")

    lock = threading.Lock()
    fh = open(RESULTS, "a")
    t0 = time.time()
    n = {"ok": 0, "err": 0}

    def work(p, v):
        try:
            res = run_one_fork(cfg, p, v)
            row = {"point_id": p["point_id"], "variant_idx": v["variant_idx"],
                   "source": v["source"], "vote_target": v["vote_target"],
                   "fork_game_id": res.get("game_id"),
                   "winner": res.get("winner"), "rounds": res.get("rounds")}
        except Exception as exc:
            row = {"point_id": p["point_id"], "variant_idx": v["variant_idx"],
                   "source": v["source"], "vote_target": v["vote_target"],
                   "fork_game_id": None, "winner": None, "error": str(exc)}
        with lock:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            n["ok" if row.get("winner") else "err"] += 1
            total = n["ok"] + n["err"]
            if total % 5 == 0:
                print(f"  ..{total}/{len(jobs)} forks "
                      f"({n['err']} err, {time.time()-t0:.0f}s)", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, p, v) for p, v in jobs]
        for _ in as_completed(futs):
            pass
    fh.close()
    print(f"done: {n['ok']} ok, {n['err']} errors, {time.time()-t0:.0f}s")
    shutdown_backends()


if __name__ == "__main__":
    main()
