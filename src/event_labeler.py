#!/usr/bin/env python3
"""LLM judge that labels agent reasoning sentences with cognitive events.

Step 2 of the state pipeline (after state_clustering.py discovery), modeled
on ~/repos/reasoning/internal_signals_poc/qwen_dual_label.py: a fixed MECE
event vocabulary, batched sentence labeling by an LLM judge, JSON output,
one file per game. Judge backend is provider-agnostic (OpenAI-compatible);
default deepseek-chat, later the same traces get relabeled by other models
for inter-judge agreement.

Input: units.jsonl produced by state_clustering.py --unit sentence
Output: <out>/<game_id>.json = {"game_id", "judge", "labels": [
    {"idx": unit index in units.jsonl, "event": EVENT}]}

Usage:
    python src/event_labeler.py --units ../analysis/states_events/units.jsonl \
        --out ../analysis/event_labels/deepseek --games 3          # pilot
    python src/event_labeler.py --units ... --out ... --all
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

EVENTS = {
    "NO_INFO": "refuses to judge for lack of data ('hasn't spoken yet', 'no info')",
    "SELF_ROLE_ANCHOR": "anchors on own role knowledge ('I know my own role / I'm innocent')",
    "TEAM_AWARENESS": "private knowledge of a Mafia partner ('my teammate, confirmed Mafia')",
    "PRIVATE_NIGHT_EVIDENCE": "inference from the agent's OWN night actions (saves made, kills coordinated)",
    "NIGHT_OUTCOME_INFERENCE": "inference from night outcomes ('Mafia tried to kill X, so X is a threat to them / innocent')",
    "TRUST_READ": "positive sincerity reading of another player (calm, cooperative, genuine)",
    "SINCERITY_FROM_ERRORS": "reads sincerity from mistakes/hesitation ('admits errors, wavered - honest villager')",
    "HIDDEN_MAFIA_HYPOTHESIS": "two-sided hypothesis that a behaviour may hide Mafia ('could be Mafia blending in')",
    "OVERLOGIC_SUSPICION": "suspicion because behaviour is TOO polished ('flawless logic may be a mask')",
    "BEHAVIOUR_STEREOTYPE": "typing by genre stereotype ('quiet = cautious villager or lurking Mafia')",
    "CONTRADICTION_NOTICED": "catches a contradiction or inconsistency in someone's words or moves",
    "DEFLECTION_NOTICED": "notices suspicion-shifting / narrative manipulation by another player",
    "DECEPTION_PLANNING": "plans own deception: cover story, image management, blending in, framing someone",
    "THREAT_TARGETING": "ranks players by threat to pick a kill/protect/vote target",
    "DELIBERATION_OPENING": "meta opener that starts an analysis ('let me think this through')",
    "ARTEFACT_DISCOURSE": "talks about connection errors / technical artefacts of the game",
    "OTHER": "none of the above fits",
}

PROMPT = """You label sentences from the private reasoning of LLM agents playing Mafia.
Assign EXACTLY ONE event code to each numbered sentence. Codes:

{vocab}

Reply ONLY with JSON: [{{"i": <number>, "e": "<CODE>"}}, ...] covering every sentence.

Sentences (speaker role in brackets):
{body}"""


def call_judge(api_url: str, api_key: str, model: str, content: str,
               retries: int = 4) -> str:
    req_body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.0,
        "max_tokens": 2000,
    }).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                api_url, data=req_body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {api_key}"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def extract_json(text: str):
    import re
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--api-url", default="https://api.deepseek.com/chat/completions")
    ap.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    ap.add_argument("--games", type=int, default=None, help="pilot: first N games")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--batch", type=int, default=30, help="sentences per judge call")
    args = ap.parse_args()

    api_key = os.environ.get(args.api_key_env) or _read_env(args.api_key_env)
    if not api_key:
        raise SystemExit(f"{args.api_key_env} not set (env or ../.env)")

    units = [json.loads(l) for l in open(args.units)]
    by_game: dict[str, list[int]] = {}
    for i, u in enumerate(units):
        by_game.setdefault(u["game_id"], []).append(i)
    games = list(by_game)
    if not args.all:
        games = games[:args.games or 3]

    vocab = "\n".join(f"{k}: {v}" for k, v in EVENTS.items())
    os.makedirs(args.out, exist_ok=True)
    total_calls = 0
    for gid in games:
        out_path = os.path.join(args.out, f"{gid}.json")
        if os.path.exists(out_path):
            print(f"{gid[:8]} exists, skip")
            continue
        idxs = by_game[gid]
        labels = []
        for s in range(0, len(idxs), args.batch):
            chunk = idxs[s:s + args.batch]
            body = "\n".join(
                f"{j}. [{units[i]['role']}] {units[i]['text'][:300]}"
                for j, i in enumerate(chunk))
            raw = call_judge(args.api_url, api_key, args.model,
                             PROMPT.format(vocab=vocab, body=body))
            total_calls += 1
            parsed = extract_json(raw)
            if not parsed:
                print(f"  {gid[:8]} batch@{s}: unparsed, skipped")
                continue
            for item in parsed:
                try:
                    j, e = int(item["i"]), str(item["e"])
                except (KeyError, TypeError, ValueError):
                    continue
                if 0 <= j < len(chunk) and e in EVENTS:
                    labels.append({"idx": chunk[j], "event": e})
        json.dump({"game_id": gid, "judge": args.model, "labels": labels},
                  open(out_path, "w"))
        print(f"{gid[:8]}: {len(labels)}/{len(idxs)} labeled")
    print(f"done: {len(games)} games, {total_calls} judge calls")


def _read_env(key: str) -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    envp = os.path.join(here, "..", ".env")
    if os.path.exists(envp):
        for line in open(envp):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


if __name__ == "__main__":
    main()
