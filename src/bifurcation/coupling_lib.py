#!/usr/bin/env python3
"""Pre-vote belief-vote coupling + transcript-only predictors.

Reuses metrics_lib (repo src/) for loading, parse modes and bootstrap.
One record per (game, round, voter) day vote where the target is alive.
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, '/home/ki/repos/mafia/src')
import metrics_lib as M  # noqa: E402

PUBLIC_KINDS = {"intro", "day_discuss", "day_vote"}

ACCUSE_RE = re.compile(
    r"suspect|suspici|mafia|vote|accus|lynch|guilty|"
    r"подозр|мафи|голос|обвин|виновн|линчев",
    re.IGNORECASE,
)
SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def load_corpus(farm_dir):
    return M.load_games(farm_dir, tolerant=True)


SR_WRAPPER_KEYS = ("players", "rankings", "ranking", "suspicion_ranking",
                   "suspects", "scores")


def sr_items(ap, sort_by_score=True):
    """Normalize a suspicion_ranking answer to an ordered list of player names.

    Handles the observed format zoo: plain list of {player, score};
    wrapper dicts ({"players": [...]}, {"rankings": [...]}, ...); items
    keyed 'player' or 'name'; optional numeric score (sorted desc,
    stable, when every item has one)."""
    if isinstance(ap, dict):
        inner = None
        for k in SR_WRAPPER_KEYS:
            if isinstance(ap.get(k), list):
                inner = ap[k]
                break
        if inner is None:
            lists = [v for v in ap.values() if isinstance(v, list)]
            if len(lists) == 1:
                inner = lists[0]
        if inner is None:
            return None
        ap = inner
    if not isinstance(ap, list):
        return None
    items = []
    for x in ap:
        if isinstance(x, dict):
            name = x.get("player") or x.get("name")
            if not isinstance(name, str):
                continue
            score = None
            for sk in ("score", "suspicion", "suspicion_score", "rank"):
                if isinstance(x.get(sk), (int, float)):
                    score = float(x[sk])
                    break
            items.append((name, score))
        elif isinstance(x, str):
            items.append((x, None))
    if not items:
        return None
    if sort_by_score and all(s is not None for _, s in items):
        items.sort(key=lambda t: -t[1])
    return [n for n, _ in items]


def ra_items(ap):
    """Normalize a role_assessment answer to a list of item dicts."""
    if isinstance(ap, dict):
        if "player" in ap and "guessed_role" in ap:
            return [ap]
        for k in ("players", "guessed_roles", "assessments", "roles"):
            if isinstance(ap.get(k), list):
                return ap[k]
        lists = [v for v in ap.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
        return None
    if isinstance(ap, list):
        return ap
    return None


def game_events(game):
    evs = []
    with open(os.path.join(game.dir, "game.jsonl")) as f:
        for line in f:
            try:
                evs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return evs


# Cyrillic transliterations observed in the RU logs (agents transliterate
# the Latin seat names and inflect them; e.g. "Грей", "Финли слишком тихий").
CYR_FORMS = {
    "Alex": ["Алекс"], "Bailey": ["Бейли"], "Casey": ["Кейси"],
    "Dana": ["Дан"], "Ellis": ["Эллис"], "Finley": ["Финли"],
    "Gray": ["Грей", "Грэй"], "Harper": ["Харпер"], "Indigo": ["Индиго"],
    "Jordan": ["Джордан"], "Kennedy": ["Кеннеди"], "Logan": ["Логан"],
}
_PAT_CACHE = {}


def name_pattern(name):
    """Latin exact form OR Cyrillic transliteration stem + case suffix."""
    if name not in _PAT_CACHE:
        alts = [re.escape(name)]
        for stem in CYR_FORMS.get(name, []):
            alts.append(re.escape(stem) + r"[а-яё]{0,3}")
        _PAT_CACHE[name] = re.compile(
            r"(?<![A-Za-zА-Яа-яЁё])(?:" + "|".join(alts) + r")(?![A-Za-zА-Яа-яЁё])")
    return _PAT_CACHE[name]


def name_mentions(text, names):
    """Count occurrences of each player name (Latin or Cyrillic form)."""
    c = Counter()
    for n in names:
        c[n] = len(name_pattern(n).findall(text))
    return c


def accusation_mentions(text, names):
    """Name counts restricted to sentences that contain an accusation cue."""
    c = Counter()
    for sent in SENT_SPLIT.split(text):
        if not sent or not ACCUSE_RE.search(sent):
            continue
        for n in names:
            c[n] += len(name_pattern(n).findall(sent))
    return c


def argmax_recent(counter, order):
    """Argmax of counter; ties broken by most recent mention (later in `order`)."""
    if not counter:
        return None
    best = max(counter.values())
    if best <= 0:
        return None
    cands = {n for n, v in counter.items() if v == best}
    for n in reversed(order):
        if n in cands:
            return n
    return sorted(cands)[0]


def extract_votes(game, mode="repaired"):
    """One dict per countable day vote of this game."""
    evs = game_events(game)
    gt = game.gt
    mafia = {n for n, r in gt.items() if r == "Mafia"}

    # per-round voter -> own day_vote ts / msg_seq
    vote_ev = {}
    public = []  # (msg_seq, ts, round, speaker, text)
    tallies = []  # (round, ts, votes)
    for e in evs:
        k = e.get("kind")
        if k in PUBLIC_KINDS:
            public.append((e.get("msg_seq", -1), e.get("ts", 0.0), e.get("round"),
                           e.get("player"), e.get("response") or ""))
        if k == "day_vote":
            vote_ev[(e.get("round"), e.get("player"))] = (e.get("ts", 0.0), e.get("msg_seq", -1))
        if k == "vote_tally":
            tallies.append((e.get("round"), e.get("ts", 0.0), e.get("votes", {})))
    tallies.sort(key=lambda x: x[1])

    # probe index: voter -> chronological list (probes file is chronological)
    probes_by_player = defaultdict(list)
    for r in game.probes:
        probes_by_player[r["player_name"]].append(r)

    out = []
    prev_votes = None
    for rnd, tally_ts, votes in tallies:
        alive = set(votes.keys())
        # previous-round plurality target
        prev_target = None
        if prev_votes:
            cnt = Counter(prev_votes.values())
            top = max(cnt.values())
            winners = sorted([t for t, v in cnt.items() if v == top])
            prev_target = winners[0]
        prev_alive_target = None
        if prev_votes:
            cnt_alive = Counter(t for t in prev_votes.values() if t in alive)
            if cnt_alive:
                top = max(cnt_alive.values())
                prev_alive_target = sorted([t for t, v in cnt_alive.items()
                                            if v == top])[0]

        for voter, target in sorted(votes.items()):
            if target not in alive:
                continue
            own = vote_ev.get((rnd, voter))
            if own is None:
                continue
            vote_ts, vote_seq = own
            others = sorted(alive - {voter})
            rec = dict(game_id=game.game_id, round=rnd, voter=voter,
                       target=target, group="mafia" if voter in mafia else "innocent",
                       n_alive=len(alive), chance=1.0 / max(1, len(others)))

            # --- probe predictors: EXACT published F4 rule (verified to
            # reproduce corpus32 headline cells 48/74, 57/80, 21/30, 8/27):
            # keep the latest probe per probe_id with answer_parse_ok before
            # the voter's own day_vote ts; answer_parsed used as stored (no
            # wrapper repair, no scan-back past a degenerate answer); ranked
            # list in stored order filtered to alive others; committed set =
            # players with exact guessed_role == 'Mafia' among alive others;
            # empty filtered list -> the vote is not counted for that metric.
            mine = [p for p in probes_by_player.get(voter, [])
                    if p["timestamp"] < vote_ts and p.get("answer_parse_ok")]
            last = {}
            for p in mine:
                last[p["probe_id"]] = p

            rec["sr_pred"] = rec["sr_round"] = None
            sr = last.get("suspicion_ranking")
            if sr and isinstance(sr["answer_parsed"], list) and sr["answer_parsed"]:
                ranked = [x.get("player") for x in sr["answer_parsed"]
                          if isinstance(x, dict) and x.get("player") in others]
                if ranked:
                    rec["sr_pred"] = ranked[0]
                    rec["sr_round"] = sr["round"]

            rec["ra_set"] = rec["ra_round"] = None
            ra = last.get("role_assessment")
            if ra and isinstance(ra["answer_parsed"], list):
                mset = sorted({x.get("player") for x in ra["answer_parsed"]
                               if isinstance(x, dict)
                               and x.get("guessed_role") == "Mafia"
                               and x.get("player") in others})
                if mset:
                    rec["ra_set"] = mset
                    rec["ra_round"] = ra["round"]

            # post-vote check (tally-anchor rule behind the published 95.1%):
            # latest parse_ok sr before the vote_tally ts, then filter once
            rec["sr_tally_pred"] = None
            sr_t = None
            for p in probes_by_player.get(voter, []):
                if (p["probe_id"] == "suspicion_ranking"
                        and p["timestamp"] < tally_ts and p.get("answer_parse_ok")):
                    sr_t = p
            if sr_t and isinstance(sr_t["answer_parsed"], list) and sr_t["answer_parsed"]:
                ranked = [x.get("player") for x in sr_t["answer_parsed"]
                          if isinstance(x, dict) and x.get("player") in others]
                if ranked:
                    rec["sr_tally_pred"] = ranked[0]

            # --- transcript-only predictors (public messages before own vote) ---
            pre_msgs = [(seq, spk, txt) for (seq, ts, r2, spk, txt) in public
                        if r2 == rnd and seq < vote_seq]
            # mention order for tie-breaks: names in message order, last mention wins
            order = []
            mc, ac = Counter(), Counter()
            for seq, spk, txt in sorted(pre_msgs):
                m = name_mentions(txt, others)
                a = accusation_mentions(txt, others)
                mc.update(m)
                ac.update(a)
                for n in others:
                    if m.get(n):
                        order.append(n)
            rec["mention_pred"] = argmax_recent(mc, order)
            rec["accuse_pred"] = argmax_recent(ac, order)
            rec["prev_pred"] = prev_target if prev_target in alive else None
            rec["prev_pred_strict"] = prev_target
            rec["prev_alive_pred"] = prev_alive_target
            out.append(rec)
        prev_votes = votes
    return out


def hit(pred, target):
    return None if pred is None else int(pred == target)
