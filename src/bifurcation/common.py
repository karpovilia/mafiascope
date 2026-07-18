"""Shared plumbing for idea 06 (bifurcation forks): paths, snapshot access,
DeepSeek API, decision/point structures.

Frozen-context contract (established by code reading, same as
analysis/test_retest_2026_07_15_probe.py):
  * state.jsonl snapshot at (round, msg_seq) is written AFTER the event with
    that msg_seq; the acting player's `messages` in the POST-vote snapshot end
    with [user: exact vote prompt, assistant: factual reply].
  * The PRE-vote snapshot (last snapshot of the round with msg_seq < vote seq)
    holds the exact context the agent had before the turn; PRE + [user prompt
    taken verbatim from the POST snapshot] reproduces the game request
    byte-for-byte (verified: pre == post[:-2] on corpus32 EN games).
  * Game turns were requested with max_tokens=400 and NO temperature
    (config_deepseek.yaml -> DeepSeekBackend defaults).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import requests

MAFIA = "/home/ki/repos/mafia"
MAFIA_SRC = os.path.join(MAFIA, "src")
LOGS = os.path.join(MAFIA, "logs")
MAFIA_CORPORA = os.path.join(MAFIA, "docs/corpora.json")
M2 = "/home/ki/repos/mafia2"
# BIF_DATA_DIR overrides the dataset root (random-baseline control run
# uses data/bifurcation_random with its own points.json)
DATA = os.environ.get("BIF_DATA_DIR", os.path.join(M2, "data/bifurcation"))
POINTS_JSON = os.path.join(DATA, "points.json")

if MAFIA_SRC not in sys.path:
    sys.path.insert(0, MAFIA_SRC)

# generation params for variant sampling (logged per record)
MODEL = "deepseek-chat"
TEMPERATURE = 1.2          # raised vs game (game sent none = provider default)
MAX_TOKENS = 400           # identical to the game backend request
N_SAMPLES = 500            # variants per point
N_SELECT = 20              # factual + 19 diverse
API_URL = "https://api.deepseek.com/chat/completions"

VOTE_RE = re.compile(r"VOTE:\s*(\S+)", re.IGNORECASE)  # engine's en pattern


def load_dotenv_key() -> str:
    for line in open(os.path.join(MAFIA, ".env")):
        line = line.strip()
        if line.startswith("DEEPSEEK_API_KEY"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no DEEPSEEK_API_KEY in mafia/.env")


def load_jsonl(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def snapshot_index(game_id: str) -> dict[tuple[int, int], dict]:
    return {(r["round"], r["msg_seq"]): r
            for r in load_jsonl(os.path.join(LOGS, game_id, "state.jsonl"))}


def vote_event(game_id: str, rnd: int, voter: str) -> dict | None:
    for e in load_jsonl(os.path.join(LOGS, game_id, "game.jsonl")):
        if (e.get("kind") == "day_vote" and e.get("round") == rnd
                and e.get("player") == voter):
            return e
    return None


def fork_and_prompt(game_id: str, rnd: int, voter: str,
                    snaps: dict | None = None) -> dict | None:
    """For a day-vote decision return the frozen-context bundle:
    fork point (round, pre_seq), exact vote prompt, factual reply,
    pre-vote messages, alive set. None if any piece is missing."""
    ev = vote_event(game_id, rnd, voter)
    if ev is None or "msg_seq" not in ev:
        return None
    vseq = ev["msg_seq"]
    snaps = snaps or snapshot_index(game_id)
    pre_keys = [s for (r, s) in snaps if r == rnd and s < vseq]
    if not pre_keys or (rnd, vseq) not in snaps:
        return None
    pre = snaps[(rnd, max(pre_keys))]
    post = snaps[(rnd, vseq)]
    try:
        pm = next(p for p in pre["players"] if p["name"] == voter)
        qm = next(p for p in post["players"] if p["name"] == voter)
    except StopIteration:
        return None
    pmsgs, qmsgs = pm["messages"], qm["messages"]
    if (not pm["alive"] or len(qmsgs) != len(pmsgs) + 2
            or qmsgs[-1]["role"] != "assistant" or qmsgs[-2]["role"] != "user"
            or pmsgs != qmsgs[:-2]):
        return None
    return {
        "fork": [rnd, max(pre_keys)],
        "vote_seq": vseq,
        "prompt": qmsgs[-2]["content"],
        "factual_reply": qmsgs[-1]["content"],
        "pre_messages": pmsgs,
        "alive": sorted(pre["alive"]),
        "language": pm.get("language", "en"),
        "roles": {p["name"]: p["role"] for p in pre["players"] if p["alive"]},
    }


def parse_vote(text: str, legal: set[str]) -> str | None:
    """Engine-compatible vote extraction (first VOTE: match), restricted to
    legal names; strips trailing punctuation like the engine's _find_player
    fuzziness does not — we require an exact legal name after cleanup."""
    m = VOTE_RE.search(text)
    if not m:
        return None
    name = m.group(1).strip().strip(".,!?:;*\"'()[]")
    return name if name in legal else None


def call_deepseek(key: str, messages: list[dict], *, temperature: float,
                  max_tokens: int, timeout: int = 120) -> dict:
    """One chat completion; returns {'content', 'usage'}. Retries on
    transient failures incl. ConnectionReset."""
    body = {"model": MODEL, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = None
    for attempt in range(1, 8):
        try:
            r = requests.post(API_URL, headers=headers, json=body, timeout=timeout)
            if r.status_code in (429, 500, 502, 503) and attempt < 7:
                time.sleep(min(2 ** attempt, 30))
                continue
            r.raise_for_status()
            j = r.json()
            return {"content": j["choices"][0]["message"]["content"],
                    "usage": j.get("usage", {})}
        except Exception as exc:  # ConnectionReset shows up here under requests
            last = exc
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"deepseek API failed after retries: {last}")


def point_dir(point_id: str) -> str:
    d = os.path.join(DATA, point_id)
    os.makedirs(d, exist_ok=True)
    return d
