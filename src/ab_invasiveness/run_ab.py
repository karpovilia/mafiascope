#!/usr/bin/env python3
"""Randomized A/B driver for the MafiaScope probe-invasiveness experiment.

Design (2026-07-18, follow-up to mafia2/analysis/unprobed_control_2026_07_15.md §6):
  * Arm A = probed  (configs/ab_probed.yaml   — battery of config_en_demo.yaml)
  * Arm B = unprobed(configs/ab_unprobed.yaml — introspection.enabled: false)
    The two configs are identical except the introspection block; snapshots
    are off in BOTH arms (disk economy only, no effect on API traffic).
  * n = 120 games per arm, 240 slots total.  A single shuffled slot->arm
    sequence (random.Random(20260718).shuffle over 120 A's + 120 B's) is
    generated deterministically inside this script; games from both arms run
    interleaved in ONE shared thread pool (default 10 workers) in ONE launch,
    so arm cannot be confounded with time-of-run or server state.
  * Per-slot game seed: SEED_BASE + slot (reproducible roster/name RNG inside
    MafiaGame; independent of thread interleaving).
  * Idempotent resume via ledger <mafia>/logs/ab_done.jsonl (field "arm");
    slots with a successful record are skipped, error slots are retried.
  * Token/usage accounting: DeepSeekBackend.generate is monkey-patched to
    (a) record the API "usage" object per game (thread-local: one worker
    thread == one game at a time) and globally, and (b) retry harder on
    connection resets (known DeepSeek flakiness).
  * Stop criteria: projected total cost > $40, or >20% of finished games
    contain ERROR messages (systematic API failure).

Usage:
  python run_ab.py                # run / resume the generation
  python run_ab.py --workers 8
  python run_ab.py --analyze      # no generation: stats + JSON to mafia2/analysis
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

MAFIA_ROOT = "/home/ki/repos/mafia"
MAFIA_SRC = os.path.join(MAFIA_ROOT, "src")
MAFIA_LOGS = os.path.join(MAFIA_ROOT, "logs")
MAFIA2_ROOT = "/home/ki/repos/mafia2"
LEDGER_PATH = os.path.join(MAFIA_LOGS, "ab_done.jsonl")

SHUFFLE_SEED = 20260718     # single randomization seed for the 240-slot sequence
SEED_BASE = 7180000         # per-slot game seed = SEED_BASE + slot
N_PER_ARM = 120
ARM_CONFIG = {
    "A": os.path.join(MAFIA_ROOT, "configs", "ab_probed.yaml"),
    "B": os.path.join(MAFIA_ROOT, "configs", "ab_unprobed.yaml"),
}
COST_CAP_USD = 40.0
ERROR_GAME_FRACTION_CAP = 0.20

# deepseek-chat pricing per 1M tokens (docs, 2026-07; estimate for the cap)
PRICE_IN_MISS = 0.28
PRICE_IN_HIT = 0.028
PRICE_OUT = 0.42

sys.path.insert(0, MAFIA_SRC)
from dotenv import load_dotenv                      # noqa: E402
load_dotenv(os.path.join(MAFIA_ROOT, ".env"))
import requests                                     # noqa: E402
import yaml                                         # noqa: E402
import llm_backend                                  # noqa: E402
from game import MafiaGame                          # noqa: E402
from llm_backend import _OpenAICompatibleBackend, _RETRY_STATUSES  # noqa: E402


# ────────────────────────────────────────────────────────────────
#  Usage accounting + hardened retries (monkey-patch of generate)
# ────────────────────────────────────────────────────────────────
_usage_lock = threading.Lock()
GLOBAL_USAGE = {"calls": 0, "errors": 0, "prompt_tokens": 0,
                "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0,
                "completion_tokens": 0}
_tls = threading.local()


def _tls_usage() -> dict:
    if not hasattr(_tls, "u"):
        _tls.u = None
    return _tls.u


def reset_thread_usage() -> None:
    _tls.u = {k: 0 for k in GLOBAL_USAGE}


def _account(usage: dict | None, error: bool = False) -> None:
    inc = {"calls": 1, "errors": 1 if error else 0}
    if usage:
        pt = usage.get("prompt_tokens", 0)
        ct = usage.get("completion_tokens", 0)
        hit = usage.get("prompt_cache_hit_tokens", 0)
        miss = usage.get("prompt_cache_miss_tokens", pt - hit)
        inc.update(prompt_tokens=pt, completion_tokens=ct,
                   prompt_cache_hit_tokens=hit, prompt_cache_miss_tokens=miss)
    with _usage_lock:
        for k, v in inc.items():
            GLOBAL_USAGE[k] += v
        u = _tls_usage()
        if u is not None:
            for k, v in inc.items():
                u[k] += v


def usage_cost(u: dict) -> float:
    return (u.get("prompt_cache_miss_tokens", 0) * PRICE_IN_MISS
            + u.get("prompt_cache_hit_tokens", 0) * PRICE_IN_HIT
            + u.get("completion_tokens", 0) * PRICE_OUT) / 1e6


def _patched_generate(self, messages, max_tokens=None):
    """llm_backend generate + usage accounting + 6 attempts incl. connection
    resets (the stock version gives up after 3; ConnectionReset is a known
    DeepSeek flakiness under parallel load)."""
    headers = {"Authorization": f"Bearer {self.api_key}",
               "Content-Type": "application/json"}
    body = {"model": self.model, "messages": messages,
            "max_tokens": max_tokens or self.max_tokens}
    if self.temperature is not None:
        body["temperature"] = self.temperature
    if self.top_p is not None:
        body["top_p"] = self.top_p
    if self.reasoning_effort is not None:
        body["reasoning_effort"] = self.reasoning_effort
    body.update(self.extra_body)
    last_err = None
    for attempt in range(1, 7):
        try:
            r = requests.post(self.api_url, headers=headers, json=body,
                              timeout=self.timeout)
            if r.status_code in _RETRY_STATUSES and attempt < 6:
                time.sleep(min(2 ** (attempt - 1), 8))
                continue
            r.raise_for_status()
            data = r.json()
            self.served_model = data.get("model", self.served_model)
            _account(data.get("usage"))
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            last_err = exc
            if attempt < 6:
                time.sleep(min(2 ** (attempt - 1), 8))
    _account(None, error=True)
    return f"ERROR: {last_err}"


_OpenAICompatibleBackend.generate = _patched_generate


# ────────────────────────────────────────────────────────────────
#  Slot sequence (deterministic randomization)
# ────────────────────────────────────────────────────────────────
def slot_sequence() -> list[tuple[int, str, int]]:
    """[(slot, arm, seed)] — 120 A + 120 B shuffled by Random(20260718)."""
    arms = ["A"] * N_PER_ARM + ["B"] * N_PER_ARM
    random.Random(SHUFFLE_SEED).shuffle(arms)
    return [(i, arm, SEED_BASE + i) for i, arm in enumerate(arms)]


# ────────────────────────────────────────────────────────────────
#  Per-game post-processing
# ────────────────────────────────────────────────────────────────
def scan_game_log(log_dir: str) -> dict:
    """Count public messages / ERROR messages straight from game.jsonl."""
    out = {"public_msgs": 0, "error_msgs": 0, "winner": None, "rounds": None,
           "game_over": False}
    path = os.path.join(log_dir, "game.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ev = json.loads(line)
                kind = ev.get("kind")
                if kind in ("intro", "day_discuss"):
                    out["public_msgs"] += 1
                    if str(ev.get("response", "")).startswith("ERROR"):
                        out["error_msgs"] += 1
                elif kind == "game_over":
                    out["game_over"] = True
                    out["winner"] = ev.get("winner")
                    out["rounds"] = ev.get("round", ev.get("rounds"))
    except FileNotFoundError:
        pass
    return out


# ────────────────────────────────────────────────────────────────
#  Generation
# ────────────────────────────────────────────────────────────────
class Runner:
    def __init__(self, workers: int):
        self.workers = workers
        self.stop = threading.Event()
        self.stop_reason: str | None = None
        self.ledger_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        self.cost_by_arm = {"A": [], "B": []}   # per finished game
        self.error_games = 0
        self.done_games = 0
        self.base_cfg = {}
        for arm, path in ARM_CONFIG.items():
            with open(path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            cfg.setdefault("logging", {})["log_dir"] = MAFIA_LOGS  # cwd-proof
            self.base_cfg[arm] = cfg
        # sanity: configs differ ONLY in the introspection block
        a = {k: v for k, v in self.base_cfg["A"].items() if k != "introspection"}
        b = {k: v for k, v in self.base_cfg["B"].items() if k != "introspection"}
        assert a == b, "arm configs differ outside the introspection block!"
        assert self.base_cfg["A"]["introspection"]["enabled"] is True
        assert self.base_cfg["B"]["introspection"]["enabled"] is False

    # ---- ledger -------------------------------------------------
    def load_done(self) -> dict[int, dict]:
        done = {}
        if os.path.exists(LEDGER_PATH):
            with open(LEDGER_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if "error" not in rec:
                        done[rec["slot"]] = rec
        # rebuild running stats for the stop criteria
        for rec in done.values():
            self.cost_by_arm[rec["arm"]].append(rec.get("cost_usd", 0.0))
            self.done_games += 1
            if rec.get("error_msgs", 0) > 0:
                self.error_games += 1
        return done

    def append_ledger(self, rec: dict) -> None:
        with self.ledger_lock:
            with open(LEDGER_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---- stop criteria -----------------------------------------
    def check_stop(self) -> None:
        with self.stats_lock:
            n_a, n_b = len(self.cost_by_arm["A"]), len(self.cost_by_arm["B"])
            if self.done_games >= 6 and n_a >= 2 and n_b >= 2:
                mean_a = sum(self.cost_by_arm["A"]) / n_a
                mean_b = sum(self.cost_by_arm["B"]) / n_b
                proj = mean_a * N_PER_ARM + mean_b * N_PER_ARM
                if proj > COST_CAP_USD and not self.stop.is_set():
                    self.stop_reason = (f"projected cost ${proj:.2f} > "
                                        f"${COST_CAP_USD} cap")
                    self.stop.set()
            if (self.done_games >= 10
                    and self.error_games / self.done_games > ERROR_GAME_FRACTION_CAP
                    and not self.stop.is_set()):
                self.stop_reason = (f"{self.error_games}/{self.done_games} games "
                                    f"contain ERROR messages (>20%)")
                self.stop.set()

    # ---- one slot ----------------------------------------------
    def run_slot(self, slot: int, arm: str, seed: int) -> None:
        if self.stop.is_set():
            return
        reset_thread_usage()
        t0 = time.monotonic()
        rec = {"slot": slot, "arm": arm, "seed": seed}
        try:
            result = game = None
            for attempt in range(1, 4):     # in-process slot retry
                try:
                    cfg = copy.deepcopy(self.base_cfg[arm])
                    cfg.setdefault("game", {})["seed"] = seed
                    game = MafiaGame(cfg)
                    result = game.run()
                    break
                except Exception as exc:
                    if attempt == 3:
                        raise
                    print(f"[slot {slot} arm {arm}] attempt {attempt} failed: "
                          f"{exc}; retrying", flush=True)
                    time.sleep(5 * attempt)
            u = _tls_usage() or {}
            scan = scan_game_log(game.game_log_dir)
            intro_path = os.path.join(game.game_log_dir, "introspection.jsonl")
            has_intro = os.path.exists(intro_path)
            if has_intro != (arm == "A"):
                raise RuntimeError(
                    f"arm/introspection mismatch: arm={arm} introspection.jsonl "
                    f"exists={has_intro} in {game.game_log_dir}")
            rec.update(
                game_id=result["game_id"], winner=result["winner"],
                rounds=result["rounds"], log_dir=game.game_log_dir,
                public_msgs=scan["public_msgs"], error_msgs=scan["error_msgs"],
                duration_s=round(time.monotonic() - t0, 1),
                api_calls=u.get("calls", 0),
                prompt_tokens=u.get("prompt_tokens", 0),
                prompt_cache_hit_tokens=u.get("prompt_cache_hit_tokens", 0),
                completion_tokens=u.get("completion_tokens", 0),
                cost_usd=round(usage_cost(u), 4), ts=time.time())
            with self.stats_lock:
                self.done_games += 1
                self.cost_by_arm[arm].append(rec["cost_usd"])
                if rec["error_msgs"] > 0:
                    self.error_games += 1
        except Exception as exc:
            rec["error"] = str(exc)
        self.append_ledger(rec)
        with self.stats_lock:
            spent = usage_cost(GLOBAL_USAGE)
            done, errs = self.done_games, self.error_games
        tag = rec.get("winner", "ERR")
        print(f"[slot {slot:3d} arm {arm}] {tag} rounds={rec.get('rounds','?')} "
              f"msgs={rec.get('public_msgs','?')} cost=${rec.get('cost_usd',0):.3f} "
              f"{rec.get('duration_s','?')}s | done {done}/240 errGames={errs} "
              f"spent=${spent:.2f}", flush=True)
        self.check_stop()

    def run(self) -> None:
        seq = slot_sequence()
        done = self.load_done()
        pending = [s for s in seq if s[0] not in done]
        print(f"[run_ab] slots total={len(seq)} done={len(done)} "
              f"pending={len(pending)} workers={self.workers} "
              f"ledger={LEDGER_PATH}", flush=True)
        arm_counts = {"A": sum(1 for _, a, _ in seq if a == "A"),
                      "B": sum(1 for _, a, _ in seq if a == "B")}
        print(f"[run_ab] arm counts in sequence: {arm_counts}", flush=True)
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = [ex.submit(self.run_slot, *s) for s in pending]
            for f in futs:
                f.result()
        spent = usage_cost(GLOBAL_USAGE)
        print(f"[run_ab] finished in {(time.monotonic()-t0)/60:.1f} min; "
              f"session spend ~${spent:.2f}; usage={GLOBAL_USAGE}", flush=True)
        if self.stop_reason:
            print(f"[run_ab] STOPPED EARLY: {self.stop_reason}", flush=True)


# ────────────────────────────────────────────────────────────────
#  Analysis
# ────────────────────────────────────────────────────────────────
def wilson_ci(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (center - half, center + half)


def analyze() -> None:
    from scipy.stats import fisher_exact, mannwhitneyu
    seq = {s: (arm, seed) for s, arm, seed in slot_sequence()}
    games = []
    with open(LEDGER_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "error" in rec:
                continue
            games.append(rec)
    # keep last record per slot
    by_slot = {}
    for rec in games:
        by_slot[rec["slot"]] = rec
    games = sorted(by_slot.values(), key=lambda r: r["slot"])
    arms = {"A": [g for g in games if g["arm"] == "A"],
            "B": [g for g in games if g["arm"] == "B"]}
    res = {"design": {
        "shuffle_seed": SHUFFLE_SEED, "seed_base": SEED_BASE,
        "n_per_arm_planned": N_PER_ARM,
        "configs": {k: os.path.relpath(v, MAFIA_ROOT)
                    for k, v in ARM_CONFIG.items()}}}
    for arm, gs in arms.items():
        n = len(gs)
        wins = sum(1 for g in gs if g["winner"] == "Mafia")
        lo, hi = wilson_ci(wins, n)
        res[f"arm_{arm}"] = {
            "label": "probed" if arm == "A" else "unprobed",
            "n": n, "mafia_wins": wins,
            "mafia_winrate": round(wins / n, 4) if n else None,
            "wilson95": [round(lo, 4), round(hi, 4)],
            "rounds_median": statistics.median(g["rounds"] for g in gs) if n else None,
            "public_msgs_median": statistics.median(g["public_msgs"] for g in gs) if n else None,
            "error_msg_games": sum(1 for g in gs if g.get("error_msgs", 0) > 0),
            "cost_usd": round(sum(g.get("cost_usd", 0) for g in gs), 2),
            "api_calls": sum(g.get("api_calls", 0) for g in gs),
        }
    a, b = arms["A"], arms["B"]
    if a and b:
        wa = sum(1 for g in a if g["winner"] == "Mafia")
        wb = sum(1 for g in b if g["winner"] == "Mafia")
        table = [[wa, len(a) - wa], [wb, len(b) - wb]]
        res["fisher_winner"] = {
            "table": table,
            "p": float(fisher_exact(table, alternative="two-sided")[1])}
        ra = [g["rounds"] for g in a]; rb = [g["rounds"] for g in b]
        res["mannwhitney_rounds"] = {
            "p": float(mannwhitneyu(ra, rb, alternative="two-sided")[1])}
        ma = [g["public_msgs"] for g in a]; mb = [g["public_msgs"] for g in b]
        res["mannwhitney_public_msgs"] = {
            "p": float(mannwhitneyu(ma, mb, alternative="two-sided")[1])}
    res["total_cost_usd"] = round(sum(g.get("cost_usd", 0) for g in games), 2)
    res["games"] = [{k: g.get(k) for k in
                     ("slot", "arm", "seed", "game_id", "winner", "rounds",
                      "public_msgs", "error_msgs", "duration_s", "api_calls",
                      "prompt_tokens", "prompt_cache_hit_tokens",
                      "completion_tokens", "cost_usd")} for g in games]
    out = os.path.join(MAFIA2_ROOT, "analysis", "ab_invasiveness_2026_07_18.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in res.items() if k != "games"},
                     indent=2, ensure_ascii=False))
    print(f"[analyze] written {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--analyze", action="store_true")
    args = ap.parse_args()
    if args.analyze:
        analyze()
        return
    Runner(args.workers).run()
    llm_backend.shutdown_backends()


if __name__ == "__main__":
    main()
