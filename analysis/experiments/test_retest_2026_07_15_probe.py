#!/usr/bin/env python3
"""
Test-retest reliability of the suspicion_ranking probe (R3-W1, EACL review).

Freezes agent states from corpus32 EN games (snapshots in logs/<gid>/state.jsonl),
re-asks the exact logged suspicion_ranking probe k times through the same
DeepSeek API path the game engine used, and measures the within-state
(decoding-noise) flip probability and volatility — the noise floor against
which the paper's between-step flip rate (48.7%) and volatility (0.300) must
be read.

Key design facts established by code reading (src/game.py, src/player.py,
src/introspection.py):
  * The engine writes state.jsonl snapshots AFTER running the probe battery
    at the same (round, msg_seq), and the acting/observing player's context
    is not modified in between (no Sheriff in this setup, so the CHECK-RESULT
    edge case never fires).  Snapshot messages[0] is the system prompt; probe
    messages were exactly [system] + context + probe-user-turn, i.e. the
    snapshot's `messages` list plus one user turn.
  * The probe question text is logged verbatim in introspection.jsonl
    (`question`), so no template re-interpolation is needed.
  * The game backend (configs/config_en_demo.yaml) sends NO temperature/top_p
    (provider defaults) and max_tokens=300 for suspicion_ranking (ProbeConfig
    default).  We replicate that request byte-for-byte.

Usage: DEEPSEEK_API_KEY from ~/repos/mafia/.env
  python3 retest.py sample     # build + freeze the sample (points.json)
  python3 retest.py run        # fire the 200 API calls (resumable cache)
  python3 retest.py analyze    # metrics + JSON output
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

MAFIA = "/home/ki/repos/mafia"
sys.path.insert(0, os.path.join(MAFIA, "src"))
import belief_dynamics as bd  # noqa: E402
import metrics_lib as ml      # noqa: E402
from introspection import IntrospectionEngine  # noqa: E402

HERE = Path(__file__).parent
LOGS = os.path.join(MAFIA, "logs")
K_REPEATS = 5
N_POINTS = 40
SEED = 20260715
MODE = "repaired"  # paper's default parse mode (belief_dynamics --mode)

CORPUS32 = json.load(open(os.path.join(MAFIA, "docs/corpora.json")))["corpus32"]["game_ids"]


def load_dotenv_key() -> str:
    for line in open(os.path.join(MAFIA, ".env")):
        line = line.strip()
        if line.startswith("DEEPSEEK_API_KEY"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no DEEPSEEK_API_KEY in .env")


def en_corpus32_games() -> list[ml.Game]:
    games = ml.load_games(LOGS, tolerant=True)
    by_id = {g.game_id: g for g in games}
    out = []
    for gid in CORPUS32:
        g = by_id.get(gid)
        if g is None:
            continue
        sp = os.path.join(LOGS, gid, "state.jsonl")
        if not os.path.isfile(sp):
            continue
        with open(sp) as f:
            rec = json.loads(f.readline())
        if rec["players"][0].get("language") == "en":
            out.append(g)
    return out


def snapshot_index(gid: str) -> dict[tuple[int, int], dict]:
    """(round, msg_seq) -> snapshot record (lazy per-game, cached on disk usage)."""
    idx = {}
    with open(os.path.join(LOGS, gid, "state.jsonl")) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            idx[(r["round"], r["msg_seq"])] = r
    return idx


def suspicion_vec(parsed, observer: str, alive: set[str], gt: dict) -> dict | None:
    """Paper's suspicion_vector semantics (belief_dynamics.suspicion_vector),
    but with the snapshot's exact alive set instead of timestamp lookup."""
    if not isinstance(parsed, list):
        return None
    vec: dict[str, float] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        tgt = item.get("player")
        if tgt == observer or tgt not in gt or tgt not in alive:
            continue
        try:
            s = float(item.get("score", 0))
        except (TypeError, ValueError):
            continue
        vec[tgt] = max(vec.get(tgt, 0.0), max(s, 0.0))
    total = sum(vec.values())
    if not vec or total <= 0:
        return None
    return {k: v / total for k, v in vec.items()}


# ────────────────────────────── sampling ──────────────────────────────

def build_sample() -> None:
    games = en_corpus32_games()
    print(f"EN corpus32 games with snapshots: {len(games)}")
    pool = defaultdict(list)  # stratum -> candidates
    for g in games:
        snap_keys = set()
        with open(os.path.join(LOGS, g.game_id, "state.jsonl")) as f:
            for line in f:
                r = json.loads(line)
                snap_keys.add((r["round"], r["msg_seq"]))
        for r in g.probes:
            if r["probe_id"] != "suspicion_ranking":
                continue
            key = (r["round"], r["public_msg_seq"])
            if key not in snap_keys:
                continue
            parsed = ml.get_answer(r, MODE)
            # need a usable vector under the ORIGINAL answer (paper criterion)
            alive = g.alive_at(r.get("timestamp", float("inf")))
            vec = suspicion_vec(parsed, r["player_name"], alive, g.gt)
            if vec is None:
                continue
            stratum = "r0" if r["round"] == 0 else ("r1" if r["round"] == 1 else "r2+")
            pool[stratum].append(dict(
                game_id=g.game_id, round=r["round"], msg_seq=r["public_msg_seq"],
                player=r["player_name"], phase=r["phase"], role=r["role"],
                question=r["question"],
                orig_vec=vec, orig_top=max(vec, key=vec.get),
            ))
    for s, lst in sorted(pool.items()):
        print(f"  stratum {s}: {len(lst)} candidates in "
              f"{len({c['game_id'] for c in lst})} games")

    alloc = {"r0": 14, "r1": 14, "r2+": 12}
    rng = random.Random(SEED)
    sample = []
    for s, n in alloc.items():
        cands = pool[s]
        n = min(n, len(cands))
        # spread over games: round-robin over shuffled per-game buckets
        by_game = defaultdict(list)
        for c in cands:
            by_game[c["game_id"]].append(c)
        for lst in by_game.values():
            rng.shuffle(lst)
        gids = sorted(by_game)
        rng.shuffle(gids)
        picked, i = [], 0
        while len(picked) < n:
            progressed = False
            for gid in gids:
                if len(picked) >= n:
                    break
                if i < len(by_game[gid]):
                    picked.append(by_game[gid][i])
                    progressed = True
            if not progressed:
                break
            i += 1
        sample.extend(picked)
    for i, p in enumerate(sample):
        p["point_id"] = i
    (HERE / "points.json").write_text(json.dumps(sample, ensure_ascii=False, indent=1))
    print(f"sampled {len(sample)} points across "
          f"{len({p['game_id'] for p in sample})} games -> points.json")
    print("round dist:", Counter(p["round"] for p in sample))
    print("phase dist:", Counter(p["phase"] for p in sample))
    print("role dist:", Counter(p["role"] for p in sample))


# ────────────────────────────── API calls ──────────────────────────────

API_URL = "https://api.deepseek.com/chat/completions"


def call_deepseek(key: str, messages: list[dict], max_tokens: int = 300) -> str:
    body = {"model": "deepseek-chat", "messages": messages, "max_tokens": max_tokens}
    # NB: no temperature/top_p — mirrors the game backend config exactly
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = None
    for attempt in range(1, 7):
        try:
            r = requests.post(API_URL, headers=headers, json=body, timeout=120)
            if r.status_code in (429, 500, 502, 503) and attempt < 6:
                time.sleep(min(2 ** attempt, 30))
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # includes ConnectionResetError under requests
            last = exc
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"API failed after retries: {last}")


def run_calls() -> None:
    key = load_dotenv_key()
    points = json.loads((HERE / "points.json").read_text())
    cache_path = HERE / "repeats_cache.jsonl"
    done = set()
    if cache_path.exists():
        for line in open(cache_path):
            r = json.loads(line)
            done.add((r["point_id"], r["rep"]))
    print(f"{len(done)} calls already cached")

    snap_cache: dict[str, dict] = {}

    def messages_for(p) -> list[dict]:
        if p["game_id"] not in snap_cache:
            snap_cache[p["game_id"]] = snapshot_index(p["game_id"])
        rec = snap_cache[p["game_id"]][(p["round"], p["msg_seq"])]
        ps = next(x for x in rec["players"] if x["name"] == p["player"])
        assert ps["alive"], "sampled player must be alive"
        msgs = [dict(m) for m in ps["messages"]]
        assert msgs[0]["role"] == "system"
        msgs.append({"role": "user",
                     "content": f"[PRIVATE PROBE — answer honestly]\n{p['question']}"})
        return msgs

    tasks = [(p, rep) for p in points for rep in range(K_REPEATS)
             if (p["point_id"], rep) not in done]
    print(f"{len(tasks)} calls to make")
    fh = open(cache_path, "a")
    t0 = time.time()
    n_ok = 0

    def one(p, rep):
        msgs = messages_for(p)
        t = time.time()
        ans = call_deepseek(key, msgs)
        return dict(point_id=p["point_id"], rep=rep, answer_raw=ans,
                    latency_ms=int((time.time() - t) * 1000), n_ctx=len(msgs))

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(one, p, rep): (p["point_id"], rep) for p, rep in tasks}
        for fut in as_completed(futs):
            pid, rep = futs[fut]
            try:
                row = fut.result()
            except Exception as exc:
                print(f"  point {pid} rep {rep} FAILED: {exc}")
                continue
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            n_ok += 1
            if n_ok % 20 == 0:
                print(f"  {n_ok}/{len(tasks)} done ({time.time()-t0:.0f}s)")
    fh.close()
    print(f"finished: {n_ok}/{len(tasks)} new calls in {time.time()-t0:.0f}s")


# ────────────────────────────── analysis ──────────────────────────────

def pairs(xs):
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            yield xs[i], xs[j]


def analyze() -> None:
    points = json.loads((HERE / "points.json").read_text())
    by_pid = {p["point_id"]: p for p in points}
    games = {g.game_id: g for g in en_corpus32_games()}

    reps = defaultdict(dict)  # point_id -> rep -> raw
    for line in open(HERE / "repeats_cache.jsonl"):
        r = json.loads(line)
        reps[r["point_id"]][r["rep"]] = r["answer_raw"]

    snap_alive: dict[tuple, set] = {}

    def alive_at_point(p) -> set:
        k = (p["game_id"], p["round"], p["msg_seq"])
        if k not in snap_alive:
            idx = snapshot_index(p["game_id"])
            snap_alive.update({(p["game_id"], r, m): set(rec["alive"])
                               for (r, m), rec in idx.items()})
        return snap_alive[k]

    point_rows = []
    n_calls = n_parsed = n_vec = 0
    for pid, p in by_pid.items():
        g = games[p["game_id"]]
        alive = alive_at_point(p)
        draws = []  # parsed repeat vectors
        raws = []
        for rep in sorted(reps.get(pid, {})):
            raw = reps[pid][rep]
            n_calls += 1
            parsed, ok = IntrospectionEngine._extract_json(raw)
            if ok:
                n_parsed += 1
            vec = suspicion_vec(parsed if ok else None, p["player"], alive, g.gt)
            if vec is not None:
                n_vec += 1
                draws.append(dict(rep=rep, vec=vec, top=max(vec, key=vec.get)))
            raws.append(dict(rep=rep, raw=raw, parse_ok=ok,
                             vec=vec, top=(max(vec, key=vec.get) if vec else None)))
        # within-state pairwise stats over usable repeat draws
        flip_pairs = flips = 0
        l1_sum, l1_n = 0.0, 0
        for a, b in pairs(draws):
            flip_pairs += 1
            if a["top"] != b["top"]:
                flips += 1
            d = bd.l1_common_support(a["vec"], b["vec"])
            if d is not None:
                l1_sum += d
                l1_n += 1
        # orig-vs-repeat (includes game-time draw as an extra repeat)
        o_pairs = o_flips = 0
        for d in draws:
            o_pairs += 1
            if d["top"] != p["orig_top"]:
                o_flips += 1
        point_rows.append(dict(
            point_id=pid, game_id=p["game_id"], round=p["round"],
            msg_seq=p["msg_seq"], player=p["player"], role=p["role"],
            phase=p["phase"], orig_top=p["orig_top"], orig_vec=p["orig_vec"],
            n_repeats=len(reps.get(pid, {})), n_usable=len(draws),
            tops=[d["top"] for d in draws],
            flips=flips, flip_pairs=flip_pairs, l1_sum=l1_sum, l1_n=l1_n,
            orig_flips=o_flips, orig_pairs=o_pairs,
            repeats=raws,
        ))

    # pooled point estimates + cluster bootstrap over games
    per_game = defaultdict(lambda: dict(flips=0, flip_pairs=0, l1_sum=0.0, l1_n=0,
                                        orig_flips=0, orig_pairs=0))
    for row in point_rows:
        c = per_game[row["game_id"]]
        for k in ("flips", "flip_pairs", "l1_sum", "l1_n", "orig_flips", "orig_pairs"):
            c[k] += row[k]
    cells = list(per_game.values())

    def ratio(num, den):
        def fn(sample):
            ns = sum(c[num] for c in sample)
            ds = sum(c[den] for c in sample)
            return ns / ds if ds else None
        return fn

    flip_fn = ratio("flips", "flip_pairs")
    vol_fn = ratio("l1_sum", "l1_n")
    orig_fn = ratio("orig_flips", "orig_pairs")
    flip_pt, flip_ci = flip_fn(cells), ml.bootstrap_ci(cells, flip_fn, n_boot=5000)
    vol_pt, vol_ci = vol_fn(cells), ml.bootstrap_ci(cells, vol_fn, n_boot=5000)
    orig_pt, orig_ci = orig_fn(cells), ml.bootstrap_ci(cells, orig_fn, n_boot=5000)

    # per-point mean (equal point weights) as robustness check
    pt_means = [r["flips"] / r["flip_pairs"] for r in point_rows if r["flip_pairs"]]
    mean_of_points = sum(pt_means) / len(pt_means)

    # between-step baseline recomputed with the paper's own code:
    # full corpus32 (validation) and the EN-21 subset (matched comparison)
    all_games = ml.load_games(LOGS, tolerant=True)
    by_id = {g.game_id: g for g in all_games}
    c32 = [by_id[g] for g in CORPUS32 if g in by_id]
    en21_ids = set(games)
    def summary(gs):
        per = [bd.game_dynamics(g, MODE) for g in gs]
        return bd.corpus_summary(per, ["all"], n_boot=2000)["all"]
    s32, sen = summary(c32), summary([g for g in c32 if g.game_id in en21_ids])

    out = dict(
        design=dict(
            corpus="corpus32 EN subset (21 games with state.jsonl snapshots)",
            n_points=len(point_rows), k_repeats=K_REPEATS, seed=SEED,
            model="deepseek-chat", sampling="API defaults (no temperature/top_p sent), "
            "max_tokens=300 — identical to the game backend request",
            parse_mode=MODE,
            probe="suspicion_ranking, exact logged question text, exact snapshot context",
        ),
        parse=dict(n_calls=n_calls, n_parse_ok=n_parsed, n_usable_vec=n_vec,
                   parse_rate=n_parsed / n_calls if n_calls else None,
                   usable_rate=n_vec / n_calls if n_calls else None),
        within_state=dict(
            flip_prob=flip_pt, flip_ci=flip_ci,
            flip_mean_of_points=mean_of_points,
            volatility=vol_pt, volatility_ci=vol_ci,
            orig_vs_repeat_flip=orig_pt, orig_vs_repeat_ci=orig_ci,
            n_pairs=sum(c["flip_pairs"] for c in cells),
            n_games=len(cells),
        ),
        between_step=dict(
            corpus32_all=dict(flip=s32["flip_rate"], volatility=s32["volatility"]),
            corpus32_en21=dict(flip=sen["flip_rate"], volatility=sen["volatility"]),
            paper=dict(flip=0.487, flip_ci=[0.451, 0.528], volatility=0.300,
                       volatility_ci=[0.284, 0.318]),
        ),
        points=point_rows,
    )
    out_path = "/home/ki/repos/mafia2/analysis/test_retest_2026_07_15.json"
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=1))

    print(f"\nparse rate: {n_parsed}/{n_calls} = {n_parsed/n_calls:.3f}; usable vec {n_vec}/{n_calls}")
    print(f"WITHIN-STATE flip prob: {flip_pt:.3f}  CI {flip_ci}  (mean-of-points {mean_of_points:.3f}, {sum(c['flip_pairs'] for c in cells)} pairs)")
    print(f"WITHIN-STATE volatility: {vol_pt:.3f}  CI {vol_ci}")
    print(f"orig-vs-repeat flip: {orig_pt:.3f}  CI {orig_ci}")
    print(f"between-step corpus32 (recomputed): flip {s32['flip_rate']['point']:.3f} CI {s32['flip_rate']['ci']}, vol {s32['volatility']['point']:.3f}")
    print(f"between-step EN-21 subset:          flip {sen['flip_rate']['point']:.3f} CI {sen['flip_rate']['ci']}, vol {sen['volatility']['point']:.3f}")
    print(f"saved -> {out_path}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("sample", "all"):
        build_sample()
    if cmd in ("run", "all"):
        run_calls()
    if cmd in ("analyze", "all"):
        analyze()
