"""Temporal belief-dynamics metrics over introspection probe logs.

Quantifies how much agents' beliefs "thrash" over a game — the
foundation for the TGN (temporal graph network) subsection of the
MafiaScope paper.  Built on top of ``metrics_lib`` (corpus selection,
parse modes, game-level cluster bootstrap).

Belief time series (per game, per agent, ordered by
(round, public_msg_seq, probe_seq)):

* **suspicion vector** — from ``suspicion_ranking``: scores over players
  alive at probe time (self excluded), clipped at 0 and L1-normalised to
  a probability-like vector.  Probes whose scores are all zero /
  missing are skipped.
* **top suspect** — argmax of that vector (list order breaks ties, i.e.
  the agent's own ranking order).

Metrics ("метания"), per agent, pooled per game and per corpus with a
game-level cluster bootstrap CI:

* **suspicion volatility** — mean L1 distance between consecutive
  suspicion vectors of the same agent.  Both vectors are re-normalised
  on their *common* support (players alive & scored at both probes) so
  that deaths do not mechanically inflate the shift; range [0, 2].
* **top-suspect flip rate** — share of consecutive probe pairs where the
  top suspect changed.  Pairs where the previous top suspect died in
  between are excluded (a forced flip is not a belief change).
* **return rate** — among flips, the share that *return* to a suspect
  the agent had already held as top earlier and then abandoned
  (circling behaviour); range [0, 1].

Temporal-graph export (``--export-graph``): JSONL of timestamped edges
usable directly as a TGN input stream —
``{game_id, src, dst, type, confidence, ts, round, msg_seq, source[, guessed_role]}``
with ``type`` in {suspects, trusts, neutral, role_guess}:

* ``suspicion_ranking`` -> ``suspects`` edges, confidence = normalised
  suspicion mass in [0, 1];
* ``role_assessment``   -> one attitude edge (``suspects``/``trusts``/
  ``neutral`` via the canonical ``guess_to_attitude``) and one
  ``role_guess`` edge (with ``guessed_role``), confidence in [0, 1].

``source`` (the probe_id) lets a consumer keep a single edge family.

CLI examples::

    python src/belief_dynamics.py --corpus ru_clean --mode repaired --boot 1000
    python src/belief_dynamics.py --corpus en_demo --export-graph graph.jsonl
    python src/belief_dynamics.py --corpus my_ids.txt   # file with game ids
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any, Callable, Sequence

from metrics_lib import (
    Game,
    bootstrap_ci,
    default_logs_dir,
    get_answer,
    guess_to_attitude,
    load_games,
    norm_role,
    select_corpus,
)

# ────────────────────────────────────────────
#  Belief time-series extraction
# ────────────────────────────────────────────

def _tkey(r: dict) -> tuple:
    return (r["round"], r["public_msg_seq"], r.get("probe_seq", 0))


def suspicion_vector(record: dict, game: Game, mode: str) -> dict[str, float] | None:
    """Normalised suspicion vector over alive others, or None if unusable."""
    ap = get_answer(record, mode)
    if not isinstance(ap, list):
        return None
    obs = record["player_name"]
    alive = game.alive_at(record.get("timestamp", float("inf")))
    vec: dict[str, float] = {}
    for item in ap:
        if not isinstance(item, dict):
            continue
        tgt = item.get("player")
        if tgt == obs or tgt not in game.gt or tgt not in alive:
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


def belief_series(game: Game, mode: str) -> dict[str, list[dict]]:
    """player -> ordered list of {tkey, ts, round, vec, top} probe states."""
    series: dict[str, list[dict]] = defaultdict(list)
    for r in game.probes:
        if r["probe_id"] != "suspicion_ranking":
            continue
        vec = suspicion_vector(r, game, mode)
        if vec is None:
            continue
        # argmax; dict preserves the agent's own list order for ties
        top = max(vec, key=vec.get)
        series[r["player_name"]].append(dict(
            tkey=_tkey(r), ts=r.get("timestamp"), round=r["round"],
            vec=vec, top=top,
        ))
    for lst in series.values():
        lst.sort(key=lambda x: x["tkey"])
    return series


def l1_common_support(a: dict[str, float], b: dict[str, float]) -> float | None:
    """L1 distance after re-normalising both vectors on their common keys."""
    common = set(a) & set(b)
    sa = sum(a[k] for k in common)
    sb = sum(b[k] for k in common)
    if not common or sa <= 0 or sb <= 0:
        return None
    return sum(abs(a[k] / sa - b[k] / sb) for k in common)


# ────────────────────────────────────────────
#  Per-agent / per-game dynamics cells
# ────────────────────────────────────────────

def agent_dynamics(states: Sequence[dict], game: Game) -> dict:
    """Sufficient statistics for one agent's probe sequence.

    Returns {l1_sum, l1_n, flips, flip_pairs, returns} — pooled ratios
    give volatility (l1_sum/l1_n), flip rate (flips/flip_pairs) and
    return rate (returns/flips).
    """
    l1_sum, l1_n = 0.0, 0
    flips = flip_pairs = returns = 0
    seen_tops: set = set()
    for prev, cur in zip(states, states[1:]):
        d = l1_common_support(prev["vec"], cur["vec"])
        if d is not None:
            l1_sum += d
            l1_n += 1
        # flip accounting: skip pairs where the previous top died in between
        alive_now = game.alive_at(cur["ts"] if cur["ts"] is not None else float("inf"))
        if prev["top"] in alive_now:
            flip_pairs += 1
            if cur["top"] != prev["top"]:
                flips += 1
                if cur["top"] in seen_tops:
                    returns += 1
        seen_tops.add(prev["top"])
    return dict(l1_sum=l1_sum, l1_n=l1_n,
                flips=flips, flip_pairs=flip_pairs, returns=returns)


def game_dynamics(game: Game, mode: str) -> dict[str, dict]:
    """group ('Mafia'/'non-Mafia' and exact role) -> pooled cell dict."""
    cells: dict[str, dict] = defaultdict(
        lambda: dict(l1_sum=0.0, l1_n=0, flips=0, flip_pairs=0, returns=0))
    for player, states in belief_series(game, mode).items():
        if len(states) < 2:
            continue
        a = agent_dynamics(states, game)
        role = game.gt.get(player, "?")
        for grp in (role, "Mafia" if role == "Mafia" else "non-Mafia", "all"):
            c = cells[grp]
            for k, v in a.items():
                c[k] += v
    return dict(cells)


# pooled-ratio stat functions for bootstrap_ci ------------------------------

def _stat_ratio(group: str, num: str, den: str) -> Callable[[Sequence[dict]], float | None]:
    def fn(sample: Sequence[dict]) -> float | None:
        ns = ds = 0.0
        for per_game in sample:
            c = per_game.get(group)
            if c:
                ns += c[num]
                ds += c[den]
        return ns / ds if ds else None
    return fn


METRICS = [
    ("volatility", "l1_sum", "l1_n"),
    ("flip_rate", "flips", "flip_pairs"),
    ("return_rate", "returns", "flips"),
]


def corpus_summary(per_game: Sequence[dict], groups: Sequence[str],
                   n_boot: int = 1000) -> dict[str, dict]:
    """group -> {metric: (point, lo, hi, n_pairs)} pooled over games."""
    out: dict[str, dict] = {}
    for grp in groups:
        row = {}
        for name, num, den in METRICS:
            fn = _stat_ratio(grp, num, den)
            point = fn(per_game)
            ci = bootstrap_ci(per_game, fn, n_boot=n_boot) if point is not None else None
            n = sum(c.get(grp, {}).get(den, 0) for c in per_game)
            row[name] = dict(point=point, ci=ci, n=int(n))
        out[grp] = row
    return out


# ────────────────────────────────────────────
#  Temporal-graph export (TGN input)
# ────────────────────────────────────────────

def iter_graph_edges(game: Game, mode: str):
    """Yield timestamped belief edges for one game (TGN event stream)."""
    for r in sorted(game.probes, key=_tkey):
        pid = r["probe_id"]
        base = dict(game_id=game.game_id, src=r["player_name"],
                    ts=r.get("timestamp"), round=r["round"],
                    msg_seq=r["public_msg_seq"], source=pid)
        if pid == "suspicion_ranking":
            vec = suspicion_vector(r, game, mode)
            if vec is None:
                continue
            for dst, w in vec.items():
                yield dict(base, dst=dst, type="suspects",
                           confidence=round(w, 6))
        elif pid == "role_assessment":
            ap = get_answer(r, mode)
            if not isinstance(ap, list):
                continue
            for item in ap:
                if not isinstance(item, dict):
                    continue
                dst = item.get("player")
                guess = norm_role(item.get("guessed_role"))
                if dst == r["player_name"] or dst not in game.gt or guess is None:
                    continue
                try:
                    conf = float(item.get("confidence", 0))
                except (TypeError, ValueError):
                    conf = 0.0
                conf = min(max(conf, 0.0), 100.0) / 100.0
                yield dict(base, dst=dst, type=guess_to_attitude(guess, conf * 100),
                           confidence=round(conf, 6))
                yield dict(base, dst=dst, type="role_guess",
                           confidence=round(conf, 6), guessed_role=guess)


def export_graph(games: Sequence[Game], mode: str, path: str) -> int:
    n = 0
    with open(path, "w") as f:
        for g in games:
            for edge in iter_graph_edges(g, mode):
                f.write(json.dumps(edge, ensure_ascii=False) + "\n")
                n += 1
    return n


# ────────────────────────────────────────────
#  CLI
# ────────────────────────────────────────────

def resolve_corpus(games: Sequence[Game], corpus: str) -> list[Game]:
    """Named corpus, or a text file with one game id per line."""
    if os.path.exists(corpus):
        with open(corpus) as f:
            ids = {line.strip() for line in f if line.strip()}
        return [g for g in games if g.game_id in ids and g.probes]
    return select_corpus(games, corpus)


def _fmt(v: float | None) -> str:
    return f"{v:.3f}" if v is not None else "  —  "


def _fmt_ci(ci) -> str:
    return f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "        —       "


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default="ru_clean",
                    help="main30|en_demo|ablation_demand|ru_clean|paper32 "
                         "or a path to a file with game ids")
    ap.add_argument("--mode", default="repaired", choices=["repaired", "raw"])
    ap.add_argument("--boot", type=int, default=1000,
                    help="bootstrap resamples for the corpus CIs")
    ap.add_argument("--logs-dir", default=default_logs_dir())
    ap.add_argument("--export-graph", metavar="PATH",
                    help="write a TGN-ready JSONL edge stream to PATH")
    args = ap.parse_args(argv)

    games = resolve_corpus(load_games(args.logs_dir), args.corpus)
    if not games:
        raise SystemExit(f"no games for corpus {args.corpus!r}")
    print(f"corpus={args.corpus}  games={len(games)}  mode={args.mode}  "
          f"boot={args.boot}")

    if args.export_graph:
        n = export_graph(games, args.mode, args.export_graph)
        print(f"exported {n} temporal edges -> {args.export_graph}")

    per_game = [game_dynamics(g, args.mode) for g in games]
    roles = sorted({r for g in games for r in g.gt.values()})
    groups = ["Mafia", "non-Mafia"] + [r for r in roles if r != "Mafia"] + ["all"]
    summary = corpus_summary(per_game, groups, n_boot=args.boot)

    hdr = (f"{'group':<12}"
           + "".join(f"{name:>13} {'95% CI':>17} {'n':>6}"
                     for name, _, _ in METRICS))
    print("\n" + hdr)
    print("-" * len(hdr))
    for grp in groups:
        row = summary[grp]
        line = f"{grp:<12}"
        for name, _, _ in METRICS:
            m = row[name]
            line += f"{_fmt(m['point']):>13} {_fmt_ci(m['ci']):>17} {m['n']:>6}"
        print(line)


if __name__ == "__main__":
    main()
