#!/usr/bin/env python3
"""Cluster agent reasoning states and decisions, ReasonOps-style.

Mirrors the discovery pipeline of ~/repos/reasoning (ReasonOps
discover_operators.py + qwen_dual_label.py): extract reasoning units from
game traces, embed them with a multilingual e5 model, KMeans with a k-sweep,
then characterize clusters (c-TF-IDF terms, medoid examples, role/phase
mixes) for manual naming. The assignments file is the training set for a
downstream state/decision detector (XGB or an LLM judge with the fixed
vocabulary, as in qwen_dual_label).

Units extracted per game:
  - action reasoning: full private response of night_mafia / night_doctor /
    day_vote events (plan + deliberation before the decision), plus the
    decision target parsed from the follow-up engine event;
  - probe reasoning: planned_action.reasoning and role_assessment reasons
    (joined), suspicion rationale carried by social_map reasons.

Usage:
    python src/state_clustering.py --logs ../logs --corpus corpus32 \
        --out ../analysis/states --k-min 4 --k-max 12
    python src/state_clustering.py --logs ../logs --out ../analysis/states --k 8

The corpus filter reads docs/corpora.json game_ids (default: every finished
game under --logs). Outputs in --out:
    units.jsonl        one unit per line (game, round, phase, player, role,
                       kind, text, decision)
    embeddings.npy     float32 [n_units, dim], L2-normalized
    k_sweep.json       silhouette / davies-bouldin per k
    assignments.csv    unit index, cluster id, distance to centroid
    clusters.json      per-cluster: size, top c-TF-IDF terms, medoid examples,
                       role/phase/decision distributions
    report.md          human-readable naming sheet
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

import numpy as np

GAME_DECISION_KINDS = {
    "night_mafia": "kill",
    "night_doctor": "save",
    "day_vote": "vote",
}
RESULT_KINDS = {"night_kill", "day_eliminate", "vote_tally"}


def load_corpus_ids(corpus: str | None) -> set[str] | None:
    if not corpus:
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    reg = json.load(open(os.path.join(here, "..", "docs", "corpora.json")))
    entry = reg[corpus]
    return set(entry["game_ids"])


def finished(game_dir: str) -> bool:
    p = os.path.join(game_dir, "game.jsonl")
    if not os.path.isfile(p):
        return False
    try:
        with open(p, "rb") as f:
            f.seek(-min(4096, os.path.getsize(p)), 2)
            return b'"game_over"' in f.read()
    except OSError:
        return False


_VOTE_RE = re.compile(r"VOTE:\s*(\w+)", re.I)
_ACTION_RE = re.compile(r"ACTION:\s*(?:Kill|Save|Protect)?\s*(\w+)", re.I)


def extract_units(logs_root: str, keep: set[str] | None) -> list[dict]:
    units: list[dict] = []
    for gid in sorted(os.listdir(logs_root)):
        gdir = os.path.join(logs_root, gid)
        if keep is not None and gid not in keep:
            continue
        if not finished(gdir):
            continue
        game = [json.loads(l) for l in open(os.path.join(gdir, "game.jsonl"))]
        setup = next((e for e in game if e["kind"] == "setup"), None)
        if not setup:
            continue
        roles = {p["name"]: p["role"] for p in setup["players"]}
        winner = next((e["winner"] for e in game if e["kind"] == "game_over"), None)

        # 1) action reasoning from private/decision events
        for ev in game:
            kind = ev.get("kind")
            if kind not in GAME_DECISION_KINDS:
                continue
            text = (ev.get("response") or "").strip()
            if len(text) < 40 or text.startswith("ERROR:"):
                continue
            m = _VOTE_RE.search(text) or _ACTION_RE.search(text)
            units.append({
                "game_id": gid, "round": ev.get("round"),
                "phase": kind, "player": ev.get("player"),
                "role": roles.get(ev.get("player")),
                "kind": "action_reasoning",
                "decision": GAME_DECISION_KINDS[kind],
                "decision_target": m.group(1) if m else None,
                "winner": winner,
                "text": text,
            })

        # 2) probe reasoning
        ip = os.path.join(gdir, "introspection.jsonl")
        if os.path.isfile(ip):
            for line in open(ip):
                d = json.loads(line)
                if not d.get("answer_parse_ok"):
                    continue
                pid, parsed = d["probe_id"], d["answer_parsed"]
                text = None
                decision_target = None
                if pid == "planned_action" and isinstance(parsed, dict):
                    text = (parsed.get("reasoning") or "").strip()
                    decision_target = parsed.get("target")
                elif pid == "role_assessment" and isinstance(parsed, list):
                    reasons = [f"{x.get('player')}: {x.get('reason')}"
                               for x in parsed
                               if isinstance(x, dict) and x.get("reason")]
                    text = " | ".join(reasons)
                if text and len(text) >= 40:
                    units.append({
                        "game_id": gid, "round": d.get("round"),
                        "phase": d.get("phase"), "player": d.get("player_name"),
                        "role": d.get("role"),
                        "kind": f"probe_{pid}",
                        "decision": None, "decision_target": decision_target,
                        "winner": winner,
                        "text": text,
                    })
    return units


def embed(texts: list[str], model_name: str, batch_size: int = 256) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)
    # e5 expects a prefix; "query: " is the retrieval-symmetric choice
    prefixed = [f"query: {t[:1500]}" for t in texts]
    return model.encode(prefixed, batch_size=batch_size,
                        show_progress_bar=True, normalize_embeddings=True)


def k_sweep(X: np.ndarray, k_min: int, k_max: int, seed: int = 0) -> dict:
    from sklearn.cluster import KMeans
    from sklearn.metrics import davies_bouldin_score, silhouette_score
    out = {}
    for k in range(k_min, k_max + 1):
        km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(X)
        out[k] = {
            "silhouette": float(silhouette_score(X, km.labels_,
                                                 sample_size=min(5000, len(X)),
                                                 random_state=seed)),
            "davies_bouldin": float(davies_bouldin_score(X, km.labels_)),
            "inertia": float(km.inertia_),
        }
        print(f"  k={k}: silhouette {out[k]['silhouette']:.3f}, "
              f"DB {out[k]['davies_bouldin']:.3f}")
    return out


_WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ]{3,}")

def ctfidf_terms(units: list[dict], labels: np.ndarray, top: int = 12) -> dict[int, list[str]]:
    """Class-based TF-IDF: term freq per cluster vs corpus doc freq."""
    df = Counter()
    tf = defaultdict(Counter)
    for u, lab in zip(units, labels):
        words = set(w.lower() for w in _WORD_RE.findall(u["text"]))
        for w in words:
            df[w] += 1
            tf[int(lab)][w] += 1
    n = len(units)
    out = {}
    for lab, counts in tf.items():
        scored = {w: c * np.log(n / (1 + df[w])) for w, c in counts.items()}
        out[lab] = [w for w, _ in sorted(scored.items(), key=lambda x: -x[1])[:top]]
    return out


_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+|(?<=\|)\s+")

def split_to_sentences(units: list[dict]) -> list[dict]:
    """Explode block units into sentence units (>= 25 chars, numbering stripped)."""
    out = []
    for u in units:
        for sent in _SENT_SPLIT.split(u["text"]):
            sent = re.sub(r"^\s*(?:\d+[.)]\s*|[-*•]\s*)", "", sent).strip(" |")
            if len(sent) < 25:
                continue
            su = dict(u)
            su["text"] = sent
            su["kind"] = u["kind"] + ":sent"
            out.append(su)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--logs", default="../logs")
    ap.add_argument("--corpus", default=None,
                    help="corpora.json key (e.g. corpus32); default = all finished games")
    ap.add_argument("--out", default="../analysis/states")
    ap.add_argument("--model", default="intfloat/multilingual-e5-small")
    ap.add_argument("--k", type=int, default=None, help="skip sweep, use this k")
    ap.add_argument("--k-min", type=int, default=4)
    ap.add_argument("--k-max", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--unit", choices=["block", "sentence"], default="block",
                    help="sentence = split blocks into sentences and cluster "
                         "those (fine-grained cognitive acts, ReasonOps events)")
    ap.add_argument("--kinds", default=None,
                    help="comma-separated unit kinds to keep, e.g. "
                         "action_reasoning,probe_planned_action")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    keep = load_corpus_ids(args.corpus)

    print("extracting units...")
    units = extract_units(args.logs, keep)
    if args.unit == "sentence":
        units = split_to_sentences(units)
    if args.kinds:
        wanted = set(args.kinds.split(","))
        units = [u for u in units if u["kind"] in wanted]
    if not units:
        sys.exit("no units extracted — check --logs / --corpus")
    with open(os.path.join(args.out, "units.jsonl"), "w") as f:
        for u in units:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")
    kinds = Counter(u["kind"] for u in units)
    print(f"  {len(units)} units: {dict(kinds)}")

    print("embedding...")
    X = embed([u["text"] for u in units], args.model)
    np.save(os.path.join(args.out, "embeddings.npy"), X.astype(np.float32))

    from sklearn.cluster import KMeans
    if args.k is None:
        print("k-sweep...")
        sweep = k_sweep(X, args.k_min, args.k_max, args.seed)
        json.dump(sweep, open(os.path.join(args.out, "k_sweep.json"), "w"), indent=1)
        k = max(sweep, key=lambda k: sweep[k]["silhouette"])
        print(f"  chosen k={k} (best silhouette; override with --k)")
    else:
        k = args.k
    km = KMeans(n_clusters=k, n_init=10, random_state=args.seed).fit(X)
    labels = km.labels_
    dists = np.linalg.norm(X - km.cluster_centers_[labels], axis=1)

    with open(os.path.join(args.out, "assignments.csv"), "w") as f:
        f.write("idx,cluster,dist,game_id,round,player,role,kind,decision_target\n")
        for i, (u, lab, d) in enumerate(zip(units, labels, dists)):
            f.write(f"{i},{lab},{d:.4f},{u['game_id'][:8]},{u['round']},"
                    f"{u['player']},{u['role']},{u['kind']},{u['decision_target']}\n")

    terms = ctfidf_terms(units, labels)
    clusters = {}
    report = ["# Agent state clusters — naming sheet\n",
              f"model {args.model}, k={k}, {len(units)} units\n"]
    for lab in range(k):
        idx = np.where(labels == lab)[0]
        medoids = idx[np.argsort(dists[idx])[:5]]
        cl = {
            "size": int(len(idx)),
            "share": round(len(idx) / len(units), 3),
            "top_terms": terms.get(lab, []),
            "roles": dict(Counter(units[i]["role"] for i in idx)),
            "kinds": dict(Counter(units[i]["kind"] for i in idx)),
            "rounds": dict(Counter(str(units[i]["round"]) for i in idx)),
            "medoid_examples": [
                {"game": units[i]["game_id"][:8], "player": units[i]["player"],
                 "role": units[i]["role"], "kind": units[i]["kind"],
                 "text": units[i]["text"][:400]}
                for i in medoids
            ],
        }
        clusters[lab] = cl
        report.append(f"\n## Cluster {lab} — size {cl['size']} ({cl['share']:.0%})\n")
        report.append(f"terms: {', '.join(cl['top_terms'])}\n")
        report.append(f"roles: {cl['roles']}  kinds: {cl['kinds']}\n")
        for ex in cl["medoid_examples"][:3]:
            report.append(f"- [{ex['role']}/{ex['kind']}] {ex['text'][:240]}\n")
    json.dump(clusters, open(os.path.join(args.out, "clusters.json"), "w"),
              ensure_ascii=False, indent=1)
    open(os.path.join(args.out, "report.md"), "w").write("".join(report))
    print(f"done: {args.out}/report.md — назовите кластеры, затем словарь состояний "
          f"фиксируется для LLM-судьи/детектора (шаг 2, как qwen_dual_label)")


if __name__ == "__main__":
    main()
