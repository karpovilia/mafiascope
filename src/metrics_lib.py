"""Shared metric definitions for the MafiaScope case study.

Single source of truth for every quantity reported in the paper
(F1 belief trajectories, F2 calibration, F3 second-order consistency,
corpus accounting, probing cost).  Both ``analyze_metrics.py`` and any
viewer-side re-computation should import from here so that the script
and the viewer can never disagree on definitions again (review P1-2 /
P2-7).

Key design decisions (canonical scoring rules):

* **Parse modes** — ``"raw"`` uses the ``answer_parsed`` field exactly as
  stored in the logs (this reproduces the originally published numbers,
  including their 26.9% parse-rate selection bias on ``role_assessment``);
  ``"repaired"`` re-parses ``answer_raw`` through
  ``IntrospectionEngine._extract_json`` (the same JSON-repair pass the
  paper's F4 describes) and falls back to ``answer_parsed``.
  NOTE on repair bias: the repair pass closes a truncated list and keeps
  only its complete prefix, so players appearing late in the answer list
  are systematically under-represented in recovered answers.

* **Second-order consistency (F3)** — canonical rule: Mafia–Mafia pairs
  are *excluded* (a partner's certainty reflects role knowledge, not
  inference; this matches both §5 of the paper and the viewer).  The
  target's elicited role guess is mapped to an attitude with a
  confidence threshold (default 50); threshold and recency-window
  sensitivity are exposed as parameters.

* **Uncertainty** — game-level cluster bootstrap (games are the units of
  resampling; observations within a game are strongly dependent).
"""
from __future__ import annotations

import json
import math
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from introspection import IntrospectionEngine

# ────────────────────────────────────────────
#  Paths
# ────────────────────────────────────────────

def repo_root() -> str:
    """Repository root, resolved relative to this file (src/..)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_logs_dir() -> str:
    return os.path.join(repo_root(), "logs")


# ────────────────────────────────────────────
#  Corpus loading & audit
# ────────────────────────────────────────────

# LLM-driven public/agent generations in game.jsonl (used for the
# probe-to-game call multiplier in the cost analysis).
GAME_LLM_KINDS = {"intro", "night_mafia", "night_doctor", "day_discuss", "day_vote"}
DEATH_KINDS = {"night_kill": "victim", "day_eliminate": "eliminated"}

# setup timestamps (int part) of the two pilot runs that precede the
# 30-game main batch (see review P1-7 / R1 W5)
MAIN_BATCH_TS = 1774562457

# 2026-07-10 batches (docs/reviews/2026_07_10/revision/runs_2026_07_10.md) —
# excluded from the RU paper corpora, selectable as their own corpora.
EN_DEMO_GAMES = {
    "36594b66-05d1-434c-be65-13360eafca9e",
    "6a66aa14-4f15-471b-9d16-9c808aea7ff6",
    "e6fbee66-7f0a-42c3-9af7-ad582f8153e5",
    "0da78714-88e0-4445-b161-f2eaf029184c",
    "e8dfa6ea-6cff-4a4f-8998-baeb52f81e77",
}
ABLATION_DEMAND_GAMES = {
    "0e2ba387-fe5b-445a-a87d-418cdb346d1c",
    "c7fe57de-1aed-48df-ac6a-da9a82f2f737",
    "797fd017-0bf4-4e73-af05-90a5eed57fbb",
    "c9e66a2a-277d-4183-80b9-2529d2235db3",
    "f2510502-8441-439e-9511-884551eda9fd",
}
# RU batch with the CLEAN social_map wording (config_deepseek.yaml after the
# P1-3a fix) — the matched arm for the demand-phrase comparison: same locale
# and chaining as ABLATION_DEMAND_GAMES, wording is the only difference.
RU_CLEAN_GAMES = {
    "186742a9-6eca-4eed-bb2e-890ba6897020",
    "444a8c24-5471-4d33-a065-b8eb46ede8f9",
    "4a48b59c-8559-4247-9573-0580d24fe36c",
    "532d5f19-00c2-4723-8462-4c9bdcbc1ef9",
    "7b600523-7ea0-4f89-8b5a-948240a4d8fe",
}


@dataclass
class Game:
    game_id: str
    dir: str
    gt: dict[str, str]                      # name -> true role
    probes: list[dict]                      # introspection.jsonl records
    setup_ts: float | None = None
    completed: bool = False
    winner: str | None = None
    n_game_records: int = 0
    game_llm_calls: int = 0
    deaths: list[tuple[float, str]] = field(default_factory=list)  # (ts, name)
    battery: tuple[str, ...] = ()
    forked_from: str | None = None          # parent game_id for replay forks
    final_round: int | None = None          # round of the game_over event

    @property
    def is_main_batch(self) -> bool:
        return self.setup_ts is not None and int(self.setup_ts) == MAIN_BATCH_TS

    def alive_at(self, ts: float) -> set[str]:
        """Players alive at wall-clock time ``ts`` (deaths are logged with ts)."""
        dead = {name for (t, name) in self.deaths if t <= ts}
        return set(self.gt) - dead


def load_games(logs_dir: str) -> list[Game]:
    """Load every log directory that has a game.jsonl (probes may be empty)."""
    games: list[Game] = []
    for d in sorted(os.listdir(logs_dir)):
        gdir = os.path.join(logs_dir, d)
        gp = os.path.join(gdir, "game.jsonl")
        if not os.path.isdir(gdir) or not os.path.exists(gp):
            continue
        g = Game(game_id=d, dir=gdir, gt={}, probes=[])
        with open(gp) as f:
            for line in f:
                r = json.loads(line)
                g.n_game_records += 1
                kind = r.get("kind")
                if kind == "setup":
                    g.setup_ts = r.get("ts")
                    g.forked_from = r.get("forked_from")
                    for p in r.get("players", []):
                        g.gt[p["name"]] = p["role"]
                elif kind == "game_over":
                    g.completed = True
                    g.winner = r.get("winner")
                    g.final_round = r.get("round")
                elif kind in DEATH_KINDS:
                    g.deaths.append((r.get("ts", 0.0), r.get(DEATH_KINDS[kind])))
                if kind in GAME_LLM_KINDS:
                    g.game_llm_calls += 1
        g.deaths.sort()
        ip = os.path.join(gdir, "introspection.jsonl")
        if os.path.exists(ip):
            with open(ip) as f:
                g.probes = [json.loads(line) for line in f]
        g.battery = tuple(sorted({r["probe_id"] for r in g.probes}))
        games.append(g)
    return games


def select_corpus(games: Sequence[Game], corpus: str) -> list[Game]:
    """Inclusion criteria.

    * ``"paper32"`` — every game with >=1 probe record (the set behind the
      published "32 games / 24,245 records", incl. two pilot runs, one of
      which is aborted and uses a different probe battery).
    * ``"main30"``  — recommended canonical set: the single 30-game main
      batch (one config, one battery, all completed).
    * ``"en_demo"`` — the 2026-07-10 EN corpus (config_en_demo.yaml).
    * ``"ablation_demand"`` — the 2026-07-10 old-``social_map``-wording
      ablation batch (config_ablation_demand.yaml).
    * ``"ru_clean"`` — the 2026-07-10 RU batch with the clean wording
      (config_deepseek.yaml post-fix); matched arm for ``ablation_demand``.
    """
    # replay forks (counterfactual branches) are never corpus games
    with_probes = [g for g in games if g.probes and g.forked_from is None]
    if corpus == "paper32":
        return [g for g in with_probes
                if g.game_id not in
                EN_DEMO_GAMES | ABLATION_DEMAND_GAMES | RU_CLEAN_GAMES]
    if corpus == "main30":
        return [g for g in with_probes if g.is_main_batch]
    if corpus == "en_demo":
        return [g for g in with_probes if g.game_id in EN_DEMO_GAMES]
    if corpus == "ablation_demand":
        return [g for g in with_probes if g.game_id in ABLATION_DEMAND_GAMES]
    if corpus == "ru_clean":
        return [g for g in with_probes if g.game_id in RU_CLEAN_GAMES]
    raise ValueError(f"unknown corpus {corpus!r}")


def audit_corpus(games: Sequence[Game]) -> list[dict]:
    """Per-directory audit rows for the game-accounting section (P1-7)."""
    rows = []
    for g in games:
        rows.append(dict(
            game_id=g.game_id,
            setup_ts=g.setup_ts,
            main_batch=g.is_main_batch,
            forked_from=g.forked_from,
            completed=g.completed,
            winner=g.winner,
            n_game_records=g.n_game_records,
            n_probe_records=len(g.probes),
            battery=g.battery,
        ))
    return rows


# ────────────────────────────────────────────
#  Answer parsing (raw vs repaired)
# ────────────────────────────────────────────

def get_answer(record: dict, mode: str) -> Any:
    """Effective parsed answer under a parse mode.

    ``raw``      -> the stored ``answer_parsed`` field only.
    ``repaired`` -> stored field if present, else JSON-repair re-parse of
                    ``answer_raw`` via ``IntrospectionEngine._extract_json``
                    (cached on the record).
    """
    if mode == "raw":
        return record.get("answer_parsed")
    if mode == "repaired":
        ap = record.get("answer_parsed")
        if ap is not None:
            return ap
        if "_repaired" not in record:
            raw = record.get("answer_raw") or ""
            parsed, ok = IntrospectionEngine._extract_json(raw)
            record["_repaired"] = parsed if ok else None
        return record["_repaired"]
    raise ValueError(f"unknown parse mode {mode!r}")


def norm_role(x: Any) -> str | None:
    if not isinstance(x, str):
        return None
    x = x.strip().lower()
    for r in ("mafia", "villager", "doctor", "sheriff"):
        if r in x:
            return r.capitalize()
    if "unknown" in x:
        return "Unknown"
    return None


def parse_stats(games: Sequence[Game], mode: str) -> dict[str, tuple[int, int]]:
    """probe_id -> (parsed_ok, total) under the given mode."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for g in games:
        for r in g.probes:
            out[r["probe_id"]][1] += 1
            if get_answer(r, mode) is not None:
                out[r["probe_id"]][0] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def parse_rate_by_round(games: Sequence[Game], probe_id: str, mode: str) -> dict[int, tuple[int, int]]:
    out: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for g in games:
        for r in g.probes:
            if r["probe_id"] != probe_id:
                continue
            out[r["round"]][1] += 1
            if get_answer(r, mode) is not None:
                out[r["round"]][0] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def repair_bias_stats(games: Sequence[Game], probe_id: str = "role_assessment") -> dict:
    """Direction-of-bias diagnostics for the JSON-repair pass.

    Repair keeps only the complete prefix of a truncated list, so
    recovered answers are systematically *shorter*: players late in the
    answer list drop out.  Returns mean list lengths for originally
    parsed answers vs repair-recovered answers.
    """
    len_raw, len_rec = [], []
    for g in games:
        for r in g.probes:
            if r["probe_id"] != probe_id:
                continue
            ap = r.get("answer_parsed")
            if isinstance(ap, list):
                len_raw.append(len(ap))
            elif ap is None:
                rec = get_answer(r, "repaired")
                if isinstance(rec, list):
                    len_rec.append(len(rec))
    mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")
    return dict(
        n_originally_parsed=len(len_raw),
        n_recovered=len(len_rec),
        mean_items_originally_parsed=mean(len_raw),
        mean_items_recovered=mean(len_rec),
    )


# ────────────────────────────────────────────
#  F1 — first-order beliefs (villager-side observers)
# ────────────────────────────────────────────

@dataclass
class FirstOrderCells:
    """Per-game, per-round sufficient statistics for F1 + baselines."""
    # round -> [correct, committed_total]
    acc: dict[int, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    # round -> [unknown, total_incl_unknown]
    unk: dict[int, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    # round -> [called_mafia, mafia_targets_total]  (Unknown counts as miss)
    recall: dict[int, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    # round -> [sum of chance recall, n]  (n_alive_mafia / n_alive_others per obs)
    recall_chance: dict[int, list[float]] = field(default_factory=lambda: defaultdict(lambda: [0.0, 0]))
    # round -> [sum of permutation-baseline accuracy, n committed]
    acc_chance: dict[int, list[float]] = field(default_factory=lambda: defaultdict(lambda: [0.0, 0]))
    # calibration observations: (confidence 0..100, correct 0/1)
    calib: list[tuple[float, int]] = field(default_factory=list)


def first_order_cells(game: Game, mode: str) -> FirstOrderCells:
    """F1 statistics for one game.

    Observers are villager-side only (Mafia knows its teammates).  The
    chance baselines use the set of players alive at probe time:

    * recall chance    = n_alive_mafia / n_alive_others — expected recall
      if the observer labelled a uniformly random subset of the right
      size as Mafia;
    * accuracy chance  = share of alive others holding the target's true
      role — expected accuracy of a random permutation of the true role
      multiset over alive others.
    """
    cells = FirstOrderCells()
    gt = game.gt
    for r in game.probes:
        if r["probe_id"] != "role_assessment":
            continue
        ap = get_answer(r, mode)
        if not isinstance(ap, list):
            continue
        obs = r["player_name"]
        if gt.get(obs) == "Mafia":
            continue
        rnd = r["round"]
        alive = game.alive_at(r.get("timestamp", float("inf")))
        others = [p for p in alive if p != obs]
        n_others = len(others)
        n_mafia_alive = sum(1 for p in others if gt.get(p) == "Mafia")
        for item in ap:
            if not isinstance(item, dict):
                continue
            tgt = item.get("player")
            if tgt == obs or tgt not in gt:
                continue
            guess = norm_role(item.get("guessed_role"))
            if guess is None:
                continue
            true = gt[tgt]
            cells.unk[rnd][1] += 1
            if guess == "Unknown":
                cells.unk[rnd][0] += 1
            else:
                cells.acc[rnd][1] += 1
                if guess == true:
                    cells.acc[rnd][0] += 1
                if n_others:
                    n_same = sum(1 for p in others if gt.get(p) == true)
                    cells.acc_chance[rnd][0] += n_same / n_others
                    cells.acc_chance[rnd][1] += 1
                try:
                    c = float(item.get("confidence", -1))
                    if 0 <= c <= 100:
                        cells.calib.append((c, 1 if guess == true else 0))
                except (TypeError, ValueError):
                    pass
            if true == "Mafia":
                cells.recall[rnd][1] += 1
                if guess == "Mafia":
                    cells.recall[rnd][0] += 1
                if n_others:
                    cells.recall_chance[rnd][0] += n_mafia_alive / n_others
                    cells.recall_chance[rnd][1] += 1
    return cells


def _sum_cells(per_game: Sequence[FirstOrderCells], attr: str) -> dict[int, list[float]]:
    tot: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for c in per_game:
        for rnd, (a, b) in getattr(c, attr).items():
            tot[rnd][0] += a
            tot[rnd][1] += b
    return tot


def first_order_summary(per_game: Sequence[FirstOrderCells]) -> dict[int, dict]:
    """round -> {acc, acc_n, unk, unk_n, recall, recall_n, recall_chance, acc_chance}."""
    acc = _sum_cells(per_game, "acc")
    unk = _sum_cells(per_game, "unk")
    rec = _sum_cells(per_game, "recall")
    rch = _sum_cells(per_game, "recall_chance")
    ach = _sum_cells(per_game, "acc_chance")
    rounds = sorted(set(acc) | set(unk) | set(rec))
    out = {}
    for rnd in rounds:
        a, an = acc.get(rnd, [0, 0])
        u, un = unk.get(rnd, [0, 0])
        m, mn = rec.get(rnd, [0, 0])
        rc, rcn = rch.get(rnd, [0, 0])
        ac, acn = ach.get(rnd, [0, 0])
        out[rnd] = dict(
            acc=a / an if an else None, acc_n=int(an),
            unk=u / un if un else None, unk_n=int(un),
            recall=m / mn if mn else None, recall_n=int(mn),
            recall_chance=rc / rcn if rcn else None,
            acc_chance=ac / acn if acn else None,
        )
    return out


# ────────────────────────────────────────────
#  F2 — calibration (bins, ECE, Brier)
# ────────────────────────────────────────────

def calibration_bins(calib: Iterable[tuple[float, int]], width: int = 20) -> dict[int, dict]:
    """bin index -> {lo, hi, n, acc, mean_conf}; conf==100 folds into the top bin."""
    bins: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])  # correct, n, conf_sum
    nb = 100 // width
    for c, ok in calib:
        b = min(int(c // width), nb - 1)
        bins[b][0] += ok
        bins[b][1] += 1
        bins[b][2] += c
    out = {}
    for b, (ok, n, cs) in sorted(bins.items()):
        out[b] = dict(lo=b * width, hi=b * width + width - 1, n=int(n),
                      acc=ok / n if n else None, mean_conf=cs / n if n else None)
    return out


def wilson_ci(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (NR 6.4)."""
    MAXIT, EPS, FPMIN = 200, 3e-12, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def betainc_reg(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(ln_beta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def beta_ppf(q: float, a: float, b: float) -> float:
    """Quantile of Beta(a, b) by bisection on I_x(a, b)."""
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if betainc_reg(a, b, mid) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def binomial_estimates(k: int, n: int) -> dict:
    """Frequentist + Bayesian summary for k successes out of n games.

    Wilson 95% CI and a Beta-Jeffreys posterior (prior Beta(0.5, 0.5)):
    posterior mean and equal-tailed 95% credible interval.
    """
    w_lo, w_hi = wilson_ci(k, n)
    a, b = k + 0.5, n - k + 0.5
    return dict(
        k=k, n=n, rate=(k / n if n else None),
        wilson95=(w_lo, w_hi),
        post_mean=a / (a + b),
        cred95=(beta_ppf(0.025, a, b), beta_ppf(0.975, a, b)),
    )


def ece_brier(calib: Sequence[tuple[float, int]], width: int = 20) -> dict:
    """Expected Calibration Error (over `width`-wide bins) and Brier score,
    treating confidence/100 as the predicted probability that the
    committed role guess is correct."""
    n = len(calib)
    if not n:
        return dict(ece=None, brier=None, n=0)
    bins = calibration_bins(calib, width)
    ece = sum(d["n"] / n * abs(d["acc"] - d["mean_conf"] / 100.0) for d in bins.values())
    brier = sum((c / 100.0 - ok) ** 2 for c, ok in calib) / n
    return dict(ece=ece, brier=brier, n=n)


# ────────────────────────────────────────────
#  F3 — second-order consistency
# ────────────────────────────────────────────

ATTITUDES = ("trusts", "neutral", "suspects")


def guess_to_attitude(guess: str, conf: float, threshold: float = 50) -> str:
    """Map an elicited role guess about player A to the attitude it implies
    toward A.  Canonical mapping (shared with the viewer, plus the
    confidence threshold used by the paper)."""
    if guess == "Mafia" and conf >= threshold:
        return "suspects"
    if guess in ("Villager", "Doctor", "Sheriff") and conf >= threshold:
        return "trusts"
    return "neutral"


def _tkey(r: dict) -> tuple:
    return (r["round"], r["public_msg_seq"], r.get("probe_seq", 0))


def second_order_pairs(
    game: Game,
    mode: str,
    *,
    threshold: float = 50,
    exclude_mafia_pairs: bool = True,
    recency_rounds: int | None = None,
) -> list[tuple[str, str]]:
    """(predicted, actual) attitude pairs for one game.

    ``exclude_mafia_pairs``: drop pairs where both the predictor A and the
    target-of-prediction B are Mafia (B knows A's role by construction) —
    the canonical rule (§5 of the paper; matches the viewer).
    ``recency_rounds``: if set, B's matched role_assessment must come from
    round >= probe_round - recency_rounds (staleness window); if None,
    the latest available assessment is used regardless of age.
    """
    gt = game.gt
    # index: player -> sorted [(tkey, round, {target: (guess, conf)})]
    ra: dict[str, list] = defaultdict(list)
    for r in game.probes:
        if r["probe_id"] != "role_assessment":
            continue
        ap = get_answer(r, mode)
        if not isinstance(ap, list):
            continue
        beliefs = {}
        for item in ap:
            if isinstance(item, dict) and item.get("player") in gt:
                g = norm_role(item.get("guessed_role"))
                try:
                    c = float(item.get("confidence", 0))
                except (TypeError, ValueError):
                    c = 0.0
                if g:
                    beliefs[item["player"]] = (g, c)
        ra[r["player_name"]].append((_tkey(r), r["round"], beliefs))
    for lst in ra.values():
        lst.sort(key=lambda x: x[0])

    pairs: list[tuple[str, str]] = []
    for r in game.probes:
        if r["probe_id"] != "social_map":
            continue
        ap = get_answer(r, mode)
        if not isinstance(ap, dict):
            continue
        tm = ap.get("toward_me")
        if not isinstance(tm, list):
            continue
        A = r["player_name"]
        t = _tkey(r)
        rnd = r["round"]
        for item in tm:
            if not isinstance(item, dict):
                continue
            B = item.get("player")
            att = item.get("attitude")
            if B not in gt or B == A or att not in ATTITUDES:
                continue
            if exclude_mafia_pairs and gt.get(A) == "Mafia" and gt.get(B) == "Mafia":
                continue
            cand = None
            for tt, trnd, beliefs in ra.get(B, []):
                if tt > t:
                    break
                if A in beliefs and (recency_rounds is None or trnd >= rnd - recency_rounds):
                    cand = beliefs[A]
            if cand is None:
                continue
            actual = guess_to_attitude(cand[0], cand[1], threshold)
            pairs.append((att, actual))
    return pairs


def second_order_summary(pairs: Sequence[tuple[str, str]]) -> dict:
    """Accuracy, majority baseline (on the same set), class counts and the
    predicted-vs-actual 'suspects' ratio."""
    n = len(pairs)
    if not n:
        return dict(n=0)
    correct = sum(1 for p, a in pairs if p == a)
    actual_counts = Counter(a for _, a in pairs)
    pred_counts = Counter(p for p, _ in pairs)
    maj_class, maj_n = actual_counts.most_common(1)[0]
    ps, as_ = pred_counts.get("suspects", 0), actual_counts.get("suspects", 0)
    return dict(
        n=n,
        accuracy=correct / n,
        majority_class=maj_class,
        majority_baseline=maj_n / n,
        diff=correct / n - maj_n / n,
        pred_counts=dict(pred_counts),
        actual_counts=dict(actual_counts),
        suspects_ratio=(ps / as_) if as_ else None,
        pred_suspects=ps,
        actual_suspects=as_,
    )


# ────────────────────────────────────────────
#  Game-level cluster bootstrap
# ────────────────────────────────────────────

def bootstrap_ci(
    per_game: Sequence[Any],
    stat_fn: Callable[[Sequence[Any]], float | None],
    n_boot: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float] | None:
    """95% percentile CI of ``stat_fn`` over game-level resamples.

    ``per_game`` holds one aggregate object per game; ``stat_fn`` maps a
    list of such objects to a scalar (may return None for degenerate
    resamples, which are skipped).
    """
    rng = random.Random(seed)
    m = len(per_game)
    if m == 0:
        return None
    vals = []
    for _ in range(n_boot):
        sample = [per_game[rng.randrange(m)] for _ in range(m)]
        v = stat_fn(sample)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    vals.sort()
    lo = vals[max(0, int(alpha / 2 * len(vals)))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return (lo, hi)


# convenience stat functions ------------------------------------------------

def stat_round_ratio(attr: str, rnd: int) -> Callable[[Sequence[FirstOrderCells]], float | None]:
    """Pooled ratio (sum num / sum den) of a FirstOrderCells field at a round."""
    def fn(sample: Sequence[FirstOrderCells]) -> float | None:
        num = den = 0.0
        for c in sample:
            a, b = getattr(c, attr).get(rnd, (0, 0))
            num += a
            den += b
        return num / den if den else None
    return fn


# --- calibration aggregates (per game), for fast bootstrap ---------------

def calib_aggregate(calib: Sequence[tuple[float, int]], width: int = 20) -> dict:
    """Per-game sufficient statistics for calibration bootstrap:
    bins: b -> [correct, n, conf_sum]; plus Brier numerator and n."""
    bins: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    brier_sum = 0.0
    nb = 100 // width
    for c, ok in calib:
        b = min(int(c // width), nb - 1)
        bins[b][0] += ok
        bins[b][1] += 1
        bins[b][2] += c
        brier_sum += (c / 100.0 - ok) ** 2
    return dict(bins={k: list(v) for k, v in bins.items()}, brier_sum=brier_sum, n=len(calib))


def stat_calib_bin(b: int) -> Callable[[Sequence[dict]], float | None]:
    def fn(sample: Sequence[dict]) -> float | None:
        ok = n = 0.0
        for agg in sample:
            v = agg["bins"].get(b)
            if v:
                ok += v[0]
                n += v[1]
        return ok / n if n else None
    return fn


def stat_ece(sample: Sequence[dict]) -> float | None:
    bins: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    n = 0.0
    for agg in sample:
        n += agg["n"]
        for b, v in agg["bins"].items():
            bins[b][0] += v[0]
            bins[b][1] += v[1]
            bins[b][2] += v[2]
    if not n:
        return None
    return sum(v[1] / n * abs(v[0] / v[1] - v[2] / v[1] / 100.0) for v in bins.values() if v[1])


def stat_brier(sample: Sequence[dict]) -> float | None:
    n = sum(agg["n"] for agg in sample)
    return sum(agg["brier_sum"] for agg in sample) / n if n else None


# --- second-order aggregates (per game), for fast bootstrap --------------

def so_aggregate(pairs: Sequence[tuple[str, str]]) -> dict:
    return dict(
        n=len(pairs),
        correct=sum(1 for p, a in pairs if p == a),
        actual=Counter(a for _, a in pairs),
        pred=Counter(p for p, _ in pairs),
    )


def _so_pool(sample: Sequence[dict]) -> dict | None:
    n = sum(a["n"] for a in sample)
    if not n:
        return None
    correct = sum(a["correct"] for a in sample)
    actual: Counter = Counter()
    pred: Counter = Counter()
    for a in sample:
        actual.update(a["actual"])
        pred.update(a["pred"])
    maj = actual.most_common(1)[0][1]
    return dict(n=n, acc=correct / n, baseline=maj / n,
                pred_s=pred.get("suspects", 0), act_s=actual.get("suspects", 0))


def stat_so_acc(sample: Sequence[dict]) -> float | None:
    s = _so_pool(sample)
    return s["acc"] if s else None


def stat_so_diff(sample: Sequence[dict]) -> float | None:
    """F3 accuracy minus majority baseline, both recomputed in-resample."""
    s = _so_pool(sample)
    return s["acc"] - s["baseline"] if s else None


def stat_so_ratio(sample: Sequence[dict]) -> float | None:
    s = _so_pool(sample)
    if not s or not s["act_s"]:
        return None
    return s["pred_s"] / s["act_s"]


# ────────────────────────────────────────────
#  Probing cost (P2-9)
# ────────────────────────────────────────────

def probe_cost(games: Sequence[Game]) -> dict:
    """Probe volume / latency / size statistics.

    Token counts are not logged, so we report exact character counts and
    a rough token estimate (~1 token per 3 characters for the mixed
    Russian/JSON payloads of this corpus) — flag it as an estimate.
    """
    n_games = len(games)
    n_probes = sum(len(g.probes) for g in games)
    game_calls = sum(g.game_llm_calls for g in games)
    lat = [r["latency_ms"] for g in games for r in g.probes if isinstance(r.get("latency_ms"), (int, float))]
    q_chars = sum(len(r.get("question") or "") for g in games for r in g.probes)
    a_chars = sum(len(r.get("answer_raw") or "") for g in games for r in g.probes)
    per_game_wall_s = [sum(r.get("latency_ms", 0) for r in g.probes) / 1000.0 for g in games]
    mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")
    return dict(
        n_games=n_games,
        n_probes=n_probes,
        probes_per_game=n_probes / n_games if n_games else None,
        game_llm_calls=game_calls,
        game_llm_calls_per_game=game_calls / n_games if n_games else None,
        call_multiplier=(n_probes + game_calls) / game_calls if game_calls else None,
        mean_latency_ms=mean(lat),
        probe_wall_clock_s_per_game=mean(per_game_wall_s),
        prompt_chars_per_probe=q_chars / n_probes if n_probes else None,
        answer_chars_per_probe=a_chars / n_probes if n_probes else None,
        est_tokens_per_probe=(q_chars + a_chars) / n_probes / 3.0 if n_probes else None,
        est_tokens_per_game=(q_chars + a_chars) / n_games / 3.0 if n_games else None,
    )
