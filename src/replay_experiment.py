#!/usr/bin/env python3
"""
Pivotal-utterance attribution via counterfactual replay (M1-M2 only).

For an utterance U at (round r, msg_seq s) of a recorded game, run two
fork batches from state.jsonl snapshots:

  PRE  arm — fork at the snapshot immediately BEFORE (r, s): the speaker's
             utterance is resampled, everything downstream re-rolled;
  POST arm — fork at (r, s): U is fixed in every player's context,
             everything downstream re-rolled.

Because M3 interventions (edit_utterance / inject_belief) are NOT
implemented, we cannot hold "everything else" fixed while swapping U for a
controlled alternative.  Attribution is therefore estimated as the shift
between the PRE and POST outcome distributions (reroll-variance design):

  pivotality(U, m) = E[m | POST forks] - E[m | PRE forks]

for metrics m: fork-round elimination target, winner, and per-player mean
normalized suspicion at vote time (from suspicion_ranking probes).

Usage (run from src/):

    # inspect candidate fork points
    python replay.py --game <game_id> --list

    # dry run: estimate API-call cost, no LLM traffic
    python replay_experiment.py --game <game_id> -u 1.7 -n 5 --dry-run

    # pilot: one utterance, one reroll per arm (2 forks total)
    python replay_experiment.py --game <game_id> -u 1.7 -n 1 \
        -c ../configs/config_en_demo.yaml -o pilot.json

    # full run: 3 utterances x 2 arms x 5 rerolls = 30 forks
    python replay_experiment.py --game <game_id> -u 1.7 -u 1.8 -u 1.9 -n 5 \
        -c ../configs/config_en_demo.yaml --parallel -o full.json

Output: a single JSON file with per-replica records, per-arm aggregates,
the factual (parent) branch metrics, and PRE->POST deltas.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import time

import yaml
from dotenv import load_dotenv

from llm_backend import shutdown_backends
from replay import run_fork_batch

SRC_DIR = os.path.dirname(os.path.abspath(__file__))


# ── parent-log helpers ───────────────────────────────────────────


def load_jsonl(path: str) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def snapshot_keys(state_path: str) -> list[tuple[int, int]]:
    """(round, msg_seq) of every snapshot, in file (= chronological) order."""
    return [(r["round"], r["msg_seq"]) for r in load_jsonl(state_path)]


def find_pre_snapshot(keys: list[tuple[int, int]], r: int, s: int) -> tuple[int, int]:
    """Snapshot immediately before (r, s) in chronological order."""
    if (r, s) not in keys:
        raise SystemExit(f"No snapshot at round={r} msg_seq={s}. Available: {keys}")
    idx = keys.index((r, s))
    if idx == 0:
        raise SystemExit(f"({r},{s}) is the first snapshot — no PRE point exists.")
    return keys[idx - 1]


def find_utterance(game_events: list[dict], r: int, s: int) -> dict:
    """The public message logged at (round r, msg_seq s) in game.jsonl."""
    for ev in game_events:
        if ev.get("round") == r and ev.get("msg_seq") == s and "player" in ev:
            return {
                "round": r,
                "msg_seq": s,
                "kind": ev.get("kind"),
                "speaker": ev.get("player"),
                "text": ev.get("response", ""),
            }
    raise SystemExit(f"No public message at round={r} msg_seq={s} in game.jsonl")


# ── metric extraction (works for parent and forked logs alike) ───


def normalized_suspicion(ranking: list) -> dict[str, float]:
    """suspicion_ranking answer -> {player: susp in [0,1]}, 1 = most suspicious."""
    try:
        rows = [(str(d["player"]), float(d["score"])) for d in ranking]
    except (TypeError, KeyError, ValueError):
        return {}
    if len(rows) < 2:
        return {}
    rows.sort(key=lambda x: -x[1])
    k = len(rows)
    return {name: 1.0 - i / (k - 1) for i, (name, _) in enumerate(rows)}


def extract_metrics(log_dir: str, fork_round: int) -> dict:
    """Fork-round vote outcome + vote-time suspicion + final outcome."""
    events = load_jsonl(os.path.join(log_dir, "game.jsonl"))

    tally, votes, eliminated, winner, rounds = None, None, None, None, None
    for ev in events:
        k = ev.get("kind")
        if k == "vote_tally" and ev.get("round") == fork_round and tally is None:
            tally, votes = ev.get("tally"), ev.get("votes")
        elif k == "day_eliminate" and ev.get("round") == fork_round and eliminated is None:
            eliminated = ev.get("eliminated")
        elif k == "game_over":
            winner, rounds = ev.get("winner"), ev.get("round")

    # Vote-time suspicion: mean normalized rank over all suspicion_ranking
    # probes issued during the fork round's day_vote phase (prober != subject).
    susp_sum: dict[str, float] = {}
    susp_n: dict[str, int] = {}
    intro_path = os.path.join(log_dir, "introspection.jsonl")
    if os.path.isfile(intro_path):
        for rec in load_jsonl(intro_path):
            if (rec.get("probe_id") != "suspicion_ranking"
                    or rec.get("round") != fork_round
                    or rec.get("phase") != "day_vote"
                    or not rec.get("answer_parse_ok")):
                continue
            for name, val in normalized_suspicion(rec.get("answer_parsed") or []).items():
                if name == rec.get("player_name"):
                    continue  # self-suspicion excluded
                susp_sum[name] = susp_sum.get(name, 0.0) + val
                susp_n[name] = susp_n.get(name, 0) + 1

    suspicion = {n: round(susp_sum[n] / susp_n[n], 4) for n in sorted(susp_sum)}
    return {
        "vote_tally": tally,
        "votes": votes,
        "eliminated": eliminated,
        "winner": winner,
        "rounds": rounds,
        "suspicion_vote_time": suspicion,
        "suspicion_n_probes": {n: susp_n[n] for n in sorted(susp_n)},
    }


def aggregate_arm(replicas: list[dict]) -> dict:
    ok = [r for r in replicas if "error" not in r]
    n = len(ok)
    if n == 0:
        return {"n": 0}
    elim_dist: dict[str, int] = {}
    susp_sum: dict[str, float] = {}
    susp_n: dict[str, int] = {}
    mafia_wins, rounds_sum = 0, 0
    for r in ok:
        m = r["metrics"]
        elim_dist[str(m["eliminated"])] = elim_dist.get(str(m["eliminated"]), 0) + 1
        if m["winner"] == "Mafia":
            mafia_wins += 1
        rounds_sum += m["rounds"] or 0
        for name, v in m["suspicion_vote_time"].items():
            susp_sum[name] = susp_sum.get(name, 0.0) + v
            susp_n[name] = susp_n.get(name, 0) + 1
    return {
        "n": n,
        "errors": len(replicas) - n,
        "elim_dist": elim_dist,
        "p_mafia_win": round(mafia_wins / n, 4),
        "mean_rounds": round(rounds_sum / n, 2),
        "mean_suspicion": {k: round(susp_sum[k] / susp_n[k], 4) for k in sorted(susp_sum)},
    }


def arm_delta(pre: dict, post: dict, target: str | None) -> dict:
    if not pre.get("n") or not post.get("n"):
        return {}
    players = sorted(set(pre["mean_suspicion"]) | set(post["mean_suspicion"]))
    d = {
        "suspicion": {
            p: round(post["mean_suspicion"].get(p, 0.0) - pre["mean_suspicion"].get(p, 0.0), 4)
            for p in players
        },
        "p_mafia_win": round(post["p_mafia_win"] - pre["p_mafia_win"], 4),
    }
    if target:
        p_pre = pre["elim_dist"].get(target, 0) / pre["n"]
        p_post = post["elim_dist"].get(target, 0) / post["n"]
        d["p_eliminate_target"] = {"target": target, "pre": round(p_pre, 4),
                                   "post": round(p_post, 4), "delta": round(p_post - p_pre, 4)}
    return d


# ── cost estimate (dry run) ──────────────────────────────────────


def estimate_fork_calls(parent_dir: str, r: int, s: int, probe_scale: float) -> dict:
    """Approximate per-fork API calls from the parent continuation after (r, s)."""
    def after(rr: int, ss: int) -> bool:
        return rr > r or (rr == r and (ss > s or ss == -1))

    speeches = sum(1 for ev in load_jsonl(os.path.join(parent_dir, "game.jsonl"))
                   if "msg_seq" in ev and "player" in ev and after(ev["round"], ev["msg_seq"]))
    probes_full = 0
    ipath = os.path.join(parent_dir, "introspection.jsonl")
    if os.path.isfile(ipath):
        probes_full = sum(1 for rec in load_jsonl(ipath)
                          if after(rec.get("round", 0), rec.get("public_msg_seq", 0)))
    probes = int(round(probes_full * probe_scale))
    return {"speeches": speeches, "probes": probes, "total": speeches + probes}


# ── main ─────────────────────────────────────────────────────────


def parse_utterance(spec: str) -> tuple[int, int]:
    spec = spec.lstrip("Rr")
    try:
        r, s = spec.split(".")
        return int(r), int(s)
    except ValueError:
        raise SystemExit(f"Bad --utterance '{spec}': expected R.S, e.g. 1.7")


def main() -> None:
    load_dotenv(os.path.join(SRC_DIR, "..", ".env"))

    ap = argparse.ArgumentParser(description="Pivotal-utterance attribution via reroll variance")
    ap.add_argument("--game", required=True, help="Parent game id (logs/<game_id>/)")
    ap.add_argument("-u", "--utterance", action="append", required=True,
                    help="Utterance as ROUND.MSG_SEQ (e.g. 1.7); repeatable")
    ap.add_argument("-n", "--num-rerolls", type=int, default=1,
                    help="Rerolls per arm (PRE and POST each get N forks)")
    ap.add_argument("-c", "--config", default=os.path.join(SRC_DIR, "..", "configs", "config_en_demo.yaml"))
    ap.add_argument("-d", "--logs-dir", default=os.path.join(SRC_DIR, "..", "logs"))
    ap.add_argument("--probes", choices=["suspicion", "full", "none"], default="suspicion",
                    help="Probe battery in forks: suspicion_ranking only (default, ~5x cheaper), "
                         "full parent battery, or none (no suspicion metric)")
    ap.add_argument("--target", default=None,
                    help="Player of interest for P(eliminated) delta (e.g. the accused)")
    ap.add_argument("--backend", default=None, help="Override backend for ALL players")
    ap.add_argument("--parallel", action="store_true", help="Run each arm's rerolls in parallel")
    ap.add_argument("--dry-run", action="store_true", help="Only print API-call estimate")
    ap.add_argument("-o", "--out", default=None, help="Output JSON path")
    args = ap.parse_args()

    parent_dir = os.path.join(args.logs_dir, args.game)
    state_path = os.path.join(parent_dir, "state.jsonl")
    if not os.path.isfile(state_path):
        raise SystemExit(f"No state.jsonl in {parent_dir} — parent recorded without snapshots.")

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg = copy.deepcopy(cfg)
    full_probes = cfg.get("introspection", {}).get("probes", [])
    if args.probes == "suspicion":
        kept = [p for p in full_probes if p["id"] == "suspicion_ranking"]
        if not kept:
            raise SystemExit("Config has no suspicion_ranking probe to keep.")
        cfg["introspection"]["probes"] = kept
        probe_scale = len(kept) / max(len(full_probes), 1)
    elif args.probes == "none":
        cfg.setdefault("introspection", {})["enabled"] = False
        probe_scale = 0.0
    else:
        probe_scale = 1.0

    keys = snapshot_keys(state_path)
    game_events = load_jsonl(os.path.join(parent_dir, "game.jsonl"))

    plan = []
    for spec in args.utterance:
        r, s = parse_utterance(spec)
        utt = find_utterance(game_events, r, s)
        pre = find_pre_snapshot(keys, r, s)
        plan.append({"utterance": utt, "pre_fork": list(pre), "post_fork": [r, s]})

    # ── dry run: cost estimate only ──
    est_total = 0
    print(f"Plan: {len(plan)} utterance(s) x 2 arms x {args.num_rerolls} reroll(s) "
          f"= {2 * len(plan) * args.num_rerolls} forks  (probes={args.probes})")
    for item in plan:
        u = item["utterance"]
        for arm, (fr, fs) in (("PRE ", item["pre_fork"]), ("POST", item["post_fork"])):
            e = estimate_fork_calls(parent_dir, fr, fs, probe_scale)
            est_total += e["total"] * args.num_rerolls
            print(f"  {arm} fork @ R{fr}.{fs}  (~{e['speeches']} speech + ~{e['probes']} probe "
                  f"= ~{e['total']} calls/fork)   U: {u['speaker']} R{u['round']}.{u['msg_seq']}")
    print(f"Estimated total: ~{est_total} API calls")
    if args.dry_run:
        return

    # ── run ──
    out = {
        "experiment": "pivotal_utterance_attribution",
        "design": "reroll-variance (M1-M2): PRE arm forks before U, POST arm forks after U; "
                  "no utterance editing (M3 interventions not implemented)",
        "parent_game_id": args.game,
        "config": os.path.abspath(args.config),
        "probes_mode": args.probes,
        "num_rerolls_per_arm": args.num_rerolls,
        "target": args.target,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "utterances": [],
    }

    for item in plan:
        u = item["utterance"]
        r = u["round"]
        entry = {
            "utterance": {**u, "text": u["text"]},
            "pre_fork": item["pre_fork"],
            "post_fork": item["post_fork"],
            "factual": extract_metrics(parent_dir, r),
        }
        for arm_name, (fr, fs) in (("pre", item["pre_fork"]), ("post", item["post_fork"])):
            print(f"\n── {arm_name.upper()} arm: {args.num_rerolls} fork(s) @ R{fr}.{fs} ──")
            results = run_fork_batch(
                cfg, parent_dir, fr, fs, args.num_rerolls,
                backend_override=args.backend, parallel=args.parallel,
            )
            replicas = []
            for res in sorted(results, key=lambda x: x.get("replica_idx", 0)):
                rep = {"replica_idx": res.get("replica_idx"), "game_id": res.get("game_id")}
                if "error" in res:
                    rep["error"] = res["error"]
                else:
                    rep["metrics"] = extract_metrics(
                        os.path.join(args.logs_dir, res["game_id"]), r)
                replicas.append(rep)
            entry[arm_name] = {"replicas": replicas, "aggregate": aggregate_arm(replicas)}
        entry["delta_post_minus_pre"] = arm_delta(
            entry["pre"]["aggregate"], entry["post"]["aggregate"], args.target)
        out["utterances"].append(entry)

    out_path = args.out or os.path.join(
        args.logs_dir, f"replay_experiment_{args.game[:8]}_{int(time.time())}.json")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nResults written to {os.path.abspath(out_path)}")

    for entry in out["utterances"]:
        u = entry["utterance"]
        print(f"\nU @ R{u['round']}.{u['msg_seq']} ({u['speaker']}):")
        print(f"  factual: eliminated={entry['factual']['eliminated']}, "
              f"winner={entry['factual']['winner']}")
        for arm in ("pre", "post"):
            print(f"  {arm:4s}: {entry[arm]['aggregate']}")
        print(f"  delta: {entry.get('delta_post_minus_pre')}")

    shutdown_backends()


if __name__ == "__main__":
    main()
