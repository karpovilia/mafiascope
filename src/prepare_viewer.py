#!/usr/bin/env python3
"""
Reads game.jsonl + introspection.jsonl and produces viewer_data.json
for the HTML viewer.

Usage:
    python prepare_viewer.py
    python prepare_viewer.py -g logs/game.jsonl -i logs/introspection.jsonl
    python prepare_viewer.py --game-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import os
import re

_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")

PROBE_IDS = ("role_beliefs", "role_assessment", "planned_action",
             "suspicion_ranking", "social_map", "personality_profile")


def load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ── Bifurcation points (idea 06: 500 samples / 20 diverse forks per point) ──
# Source: mafia2/data/bifurcation/{points.json, <point_id>/selected.json,
# fork_results.jsonl}. Soft degradation: missing files → no panel in the viewer.
BIF_TEXT_MAX = 320  # chars of the alternative utterance kept per variant


def load_bifurcation(bif_dir: str) -> dict[str, list[dict]]:
    """Return {parent_game_id: [point, ...]} or {} when data is absent."""
    points_path = os.path.join(bif_dir, "points.json")
    if not os.path.isfile(points_path):
        return {}
    try:
        with open(points_path, "r", encoding="utf-8") as f:
            points = json.load(f).get("points", [])
    except Exception as e:
        print(f"  bifurcation: cannot read {points_path}: {e}")
        return {}

    # Fork outcomes: (point_id, variant_idx) -> {winner, rounds, fork_game_id}
    forks: dict[tuple[str, int], dict] = {}
    fr_path = os.path.join(bif_dir, "fork_results.jsonl")
    if os.path.isfile(fr_path):
        for row in load_jsonl(fr_path):
            forks[(row["point_id"], row["variant_idx"])] = row

    by_game: dict[str, list[dict]] = {}
    for pt in points:
        pid = pt.get("point_id")
        sel_path = os.path.join(bif_dir, pid or "", "selected.json")
        if not pid or not os.path.isfile(sel_path):
            continue
        try:
            with open(sel_path, "r", encoding="utf-8") as f:
                sel = json.load(f)
        except Exception as e:
            print(f"  bifurcation: cannot read {sel_path}: {e}")
            continue

        # Behavioural lock-in: share of the modal vote target in the 500 samples
        dist = sel.get("target_dist_samples", {}) or {}
        n_samples = sum(dist.values())
        modal_target, modal_count = None, 0
        for tgt, cnt in dist.items():
            if cnt > modal_count:
                modal_target, modal_count = tgt, cnt

        parent_winner = pt.get("winner")
        variants = []
        n_flips = 0
        for v in sel.get("variants", []):
            fk = forks.get((pid, v["variant_idx"]), {})
            winner = fk.get("winner")
            # flip = the fork changes the parent outcome to a Villagers win
            flip = winner == "Villagers" and parent_winner != "Villagers"
            if flip:
                n_flips += 1
            text = (v.get("text") or "").strip()
            variants.append({
                "variant_idx": v["variant_idx"],       # = dissimilarity rank
                "source": v.get("source"),             # factual | sampled
                "stage": v.get("stage"),               # factual | target_coverage | maxmin
                "vote_target": v.get("vote_target"),
                "text_short": text[:BIF_TEXT_MAX] + ("…" if len(text) > BIF_TEXT_MAX else ""),
                "fork_game_id": fk.get("fork_game_id"),
                "winner": winner,
                "rounds": fk.get("rounds"),
                "flip": flip,
            })
        n_forks = sum(1 for v in variants if v["winner"])

        by_game.setdefault(pt["game_id"], []).append({
            "point_id": pid,
            "quadrant": pt.get("quadrant"),            # policy_gap | perception_gap
            "round": pt.get("round"),
            "msg_seq": pt.get("vote_seq"),             # timeline step of the actual vote
            "voter": pt.get("voter"),
            "factual_target": pt.get("target"),
            "parent_winner": parent_winner,
            "lock_in": {
                "modal_target": modal_target,
                "modal_count": modal_count,
                "n_samples": n_samples,
                "share": round(modal_count / n_samples, 3) if n_samples else None,
                "factual_is_modal": modal_target == pt.get("target"),
            },
            "n_forks": n_forks,
            "n_flips": n_flips,
            "flip_share": round(n_flips / n_forks, 3) if n_forks else None,
            "variants": variants,
        })
    for pts in by_game.values():
        pts.sort(key=lambda p: (p["round"], p["msg_seq"]))
    return by_game


def build_viewer_data(game_log: str, intro_log: str, game_id: str | None = None,
                      bif_points: list[dict] | None = None) -> dict:
    events = load_jsonl(game_log)
    # Probes are optional: bifurcation forks were run with introspection off,
    # but they must still replay in the viewer (message log + ground truth).
    probes = load_jsonl(intro_log) if os.path.isfile(intro_log) else []

    # Find available game IDs
    game_ids = sorted(set(e.get("game_id", "") for e in events if e.get("kind") == "setup"))
    if not game_ids:
        raise ValueError("No games found in game log")

    if game_id:
        if game_id not in game_ids:
            raise ValueError(f"Game {game_id} not found. Available: {game_ids}")
    else:
        game_id = game_ids[-1]
        print(f"Using latest game: {game_id}")

    events = [e for e in events if e.get("game_id") == game_id]
    probes = [p for p in probes if p.get("game_id") == game_id]

    # Extract setup
    setup_event = next(e for e in events if e["kind"] == "setup")
    players = setup_event["players"]
    all_names = [p["name"] for p in players]
    truth = {p["name"]: {"role": p["role"], "model": p["model"], "backend": p["backend"]} for p in players}

    # ── Build ordered timeline of death events ──
    # Each has a timestamp so we can compare with probe timestamps.
    death_events = []  # [(timestamp, player_name)]
    for e in events:
        if e["kind"] == "night_kill":
            death_events.append((e.get("ts", 0), e["victim"]))
        elif e["kind"] == "day_eliminate":
            death_events.append((e.get("ts", 0), e["eliminated"]))
    death_events.sort(key=lambda x: x[0])

    # ── Collect unique steps from probes ──
    # Sort: (round, msg_seq) but treat msg_seq=-1 as AFTER all positive msg_seq in that round
    step_keys_set: set[tuple[int, int]] = set()
    for p in probes:
        step_keys_set.add((p["round"], p["public_msg_seq"]))

    def step_sort_key(k: tuple[int, int]) -> tuple[int, int]:
        rnd, seq = k
        return (rnd, 999999 if seq == -1 else seq)

    step_keys = sorted(step_keys_set, key=step_sort_key)

    # ── Index probes ──
    probe_index: dict[tuple, dict] = {}
    for p in probes:
        key = (p["round"], p["public_msg_seq"], p["player_name"], p["probe_id"])
        probe_index[key] = p

    # ── Timestamp of each step (min timestamp of probes at that step) ──
    step_timestamps: dict[tuple[int, int], float] = {}
    for p in probes:
        k = (p["round"], p["public_msg_seq"])
        ts = p["timestamp"]
        if k not in step_timestamps or ts < step_timestamps[k]:
            step_timestamps[k] = ts

    # ── Index game events by (round, msg_seq) for message text ──
    MSG_KINDS = ("intro", "night_mafia", "night_doctor", "night_sheriff",
                 "day_discuss", "day_vote", "night_analysis",
                 # non-mafia game families (werewolf reuses the mafia kinds)
                 "team_proposal", "team_vote", "mission_card")
    msg_list = [e for e in events
                if e.get("msg_seq") is not None and e["kind"] in MSG_KINDS]
    # Transcript-only logs (e.g. Resistance) may reuse (round, msg_seq) across
    # proposal attempts within a mission round; re-key sequentially by ts so
    # every utterance gets its own timeline step. Only safe without probes —
    # probes are addressed by the original (round, public_msg_seq).
    raw_keys = [(e.get("round", 0), e["msg_seq"]) for e in msg_list]
    if not probes and len(set(raw_keys)) < len(raw_keys):
        msg_list = [dict(e) for e in sorted(msg_list, key=lambda e: e.get("ts", 0))]
        counters: dict[int, int] = {}
        for e in msg_list:
            rnd_e = e.get("round", 0)
            counters[rnd_e] = counters.get(rnd_e, 0) + 1
            e["msg_seq"] = counters[rnd_e]
    msg_events: dict[tuple[int, int], dict] = {}
    for e in msg_list:
        rnd_e = e.get("round", 0)
        msg_events[(rnd_e, e["msg_seq"])] = e
        # Also add to step_keys so game events without probes still appear
        step_keys_set.add((rnd_e, e["msg_seq"]))

    # Re-sort after adding game event keys
    step_keys = sorted(step_keys_set, key=step_sort_key)

    # Number Resistance proposal attempts (game-wide) so repeated identical
    # team/tally cards survive the per-step text dedup below.
    _attempt_no: dict[int, int] = {}
    for _kind in ("team_final", "team_vote_tally"):
        for _i, _e in enumerate(sorted((e for e in events if e["kind"] == _kind),
                                       key=lambda e: e.get("ts", 0)), 1):
            _attempt_no[id(_e)] = _i

    # ── Build steps ──
    steps = []
    for step_idx, (rnd, msg_seq) in enumerate(step_keys):
        # Phase
        sample_probe = None
        for pname in all_names:
            key = (rnd, msg_seq, pname, "role_beliefs")
            if key in probe_index:
                sample_probe = probe_index[key]
                break
        if sample_probe:
            phase = sample_probe["phase"]
        else:
            # No probe data — infer phase from game event
            ev = msg_events.get((rnd, msg_seq))
            if ev:
                kind_to_phase = {"intro": "intro", "night_mafia": "night", "night_doctor": "night",
                                 "night_sheriff": "night", "day_discuss": "day_discuss",
                                 "day_vote": "day_vote", "night_analysis": "night_analysis",
                                 # Resistance: proposals read as discussion, team votes as
                                 # votes, secret mission cards as night (private) steps
                                 "team_proposal": "day_discuss", "team_vote": "day_vote",
                                 "mission_card": "night"}
                phase = kind_to_phase.get(ev["kind"], "unknown")
            else:
                phase = "unknown"

        # Label
        label = f"R{rnd} end" if msg_seq == -1 else f"R{rnd}.{msg_seq}"

        # Speaker + message text from game events
        speaker = None
        message_text = ""
        msg_ev = msg_events.get((rnd, msg_seq))
        if msg_ev:
            speaker = msg_ev.get("player") or msg_ev.get("leader")
            message_text = msg_ev.get("response", "")
            if msg_ev["kind"] == "night_analysis":
                speaker = None
                message_text = msg_ev.get("night_result", "")
        else:
            # Fallback: get speaker from probe data
            step_probes_list = [p for p in probes if p["round"] == rnd and p["public_msg_seq"] == msg_seq]
            if step_probes_list and msg_seq >= 0:
                # With probe-all, all players are probed. Use the one with smallest player_idx
                # to identify speaker — but we now need game events for that.
                # Just leave speaker as None if no game event matched.
                pass

        # Who is alive?
        step_ts = step_timestamps.get((rnd, msg_seq), 0)
        # Fallback: use game event timestamp if no probe timestamp
        if step_ts == 0:
            ev = msg_events.get((rnd, msg_seq))
            if ev:
                step_ts = ev.get("ts", 0)
        dead_before = set()
        for death_ts, dead_name in death_events:
            if death_ts < step_ts:
                dead_before.add(dead_name)
        alive = [n for n in all_names if n not in dead_before]

        # Per-player beliefs (now ALL players have data per step)
        player_beliefs = {}
        for pname in all_names:
            entry = {"has_data": False}
            for probe_id in PROBE_IDS:
                key = (rnd, msg_seq, pname, probe_id)
                if key in probe_index:
                    p = probe_index[key]
                    # A failed parse must not write probe_id: None — the None key
                    # would mask an older value during the forward-fill merge.
                    if p["answer_parse_ok"]:
                        entry["has_data"] = True
                        entry[probe_id] = p["answer_parsed"]
            player_beliefs[pname] = entry

        # Collect ALL public events that happened before this step's timestamp
        # These are things every player would know about.
        game_events = []
        for e in events:
            if e.get("round") != rnd:
                continue
            ets = e.get("ts", 0)
            if ets >= step_ts:
                continue
            if e["kind"] == "night_kill":
                game_events.append({"type": "kill", "text": f"{e['victim']} was killed by the Mafia"})
            elif e["kind"] == "night_save":
                game_events.append({"type": "save", "text": f"The Doctor saved someone tonight"})
            elif e["kind"] == "day_eliminate":
                game_events.append({"type": "eliminate",
                                    "text": f"{e['eliminated']} was eliminated by vote ({e['votes']} votes)"})
            elif e["kind"] == "vote_tally":
                tally = e.get("tally", {})
                if tally:
                    parts = [f"{name}: {cnt}" for name, cnt in sorted(tally.items(), key=lambda x: -x[1])]
                    game_events.append({"type": "vote_tally", "text": f"Votes: {', '.join(parts)}"})
            # ── Resistance events: teams and aggregate mission results ──
            elif e["kind"] == "team_final":
                team = e.get("team") or []
                game_events.append({"type": "team",
                                    "text": f"Proposal #{_attempt_no.get(id(e), '?')} — leader "
                                            f"{e.get('leader', '?')} finalizes the mission team: "
                                            f"{', '.join(team)}"})
            elif e["kind"] == "team_vote_tally":
                votes = e.get("votes") or {}
                marks = ", ".join(f"{n} {'✓' if v == 'Approve' else '✗'}"
                                  for n, v in votes.items())
                verdict = "APPROVED" if e.get("approved") else "rejected"
                game_events.append({"type": "team_vote",
                                    "text": f"Team vote #{_attempt_no.get(id(e), '?')}: "
                                            f"{e.get('approvals', '?')}/{len(votes) or '?'} "
                                            f"approve ({marks}) — {verdict}"})
            elif e["kind"] == "mission_result":
                team = e.get("team") or []
                verdict = "SUCCESS" if e.get("success") else "FAILURE"
                game_events.append({"type": "mission",
                                    "text": f"Mission {e.get('mission', '?')}: {verdict} "
                                            f"({e.get('sabotages', 0)} sabotage). "
                                            f"Team: {', '.join(team)}"})

        # Keep only unique events (deduplicate by text)
        seen_texts = set()
        unique_events = []
        for ev in game_events:
            if ev["text"] not in seen_texts:
                seen_texts.add(ev["text"])
                unique_events.append(ev)

        steps.append({
            "idx": step_idx,
            "round": rnd,
            "msg_seq": msg_seq,
            "phase": phase,
            "label": label,
            "speaker": speaker,
            "message": message_text,
            "events": unique_events,
            "alive": alive,
            "dead": list(dead_before),
            "player_beliefs": player_beliefs,
        })

    # ── Forward-fill beliefs ──
    # Carry forward each player's last known beliefs into steps where
    # they have no fresh probe data. This keeps graphs populated.
    last_known: dict[str, dict] = {}
    for step in steps:
        for pname in all_names:
            entry = step["player_beliefs"][pname]
            prev = last_known.get(pname)
            if entry["has_data"]:
                # Merge per probe: a partial battery on this step must not
                # drop probes recorded earlier (e.g. keep old social_map).
                if prev:
                    for pid in PROBE_IDS:
                        if pid not in entry and pid in prev:
                            entry[pid] = prev[pid]
                last_known[pname] = {pid: entry[pid] for pid in PROBE_IDS if pid in entry}
            elif prev:
                carried = dict(prev)
                carried["has_data"] = True
                carried["carried_forward"] = True
                step["player_beliefs"][pname] = carried

    # Game result
    game_over = next((e for e in events if e["kind"] == "game_over"), None)
    winner = game_over["winner"] if game_over else "Pending"

    # Game start timestamp (from setup event)
    started_at = setup_event.get("ts", 0)

    # Probe parse statistics — instrument honesty: how many probe answers
    # actually yielded machine-readable JSON.
    parse_stats = {"total": 0, "ok": 0, "by_probe": {}}
    for p in probes:
        s = parse_stats["by_probe"].setdefault(p["probe_id"], {"total": 0, "ok": 0})
        s["total"] += 1
        parse_stats["total"] += 1
        if p["answer_parse_ok"]:
            s["ok"] += 1
            parse_stats["ok"] += 1

    return {
        "game_id": game_id,
        "started_at": started_at,
        "winner": winner,
        # Game family (mafia default); werewolf skins carry setup.skin
        "game_type": setup_event.get("game_type", "mafia"),
        "skin": setup_event.get("skin"),
        "players": players,
        "truth": truth,
        "steps": steps,
        "total_steps": len(steps),
        "parse_stats": parse_stats,
        # Search/filter facets for the games sidebar
        "language": "ru" if _CYRILLIC_RE.search(
            json.dumps(steps[:6], ensure_ascii=False)) else "en",
        "models": sorted({(p.get("model") or "?") for p in players}),
        # Counterfactual-replay branch metadata (absent for root games)
        "forked_from": setup_event.get("forked_from"),
        "fork_point": setup_event.get("fork_point"),
        "fork_batch_id": setup_event.get("fork_batch_id"),
        "replica_idx": setup_event.get("replica_idx"),
        "intervention": setup_event.get("intervention"),
        # Bifurcation-point structure (idea 06); empty for ordinary games
        "bifurcation_points": bif_points or [],
    }


def _is_branch_game(game_log: str) -> bool:
    """Cheap check: does the setup line carry forked_from?"""
    try:
        with open(game_log, "r", encoding="utf-8") as f:
            setup = json.loads(f.readline())
        return bool(setup.get("forked_from"))
    except Exception:
        return False


def _is_completed_game(game_log: str) -> bool:
    """Cheap check: does the log end with a game_over event (tail scan)?"""
    try:
        with open(game_log, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode("utf-8", errors="ignore")
        return '"kind": "game_over"' in tail or '"kind":"game_over"' in tail
    except Exception:
        return False


def scan_game_dirs(logs_root: str, bif_data: dict[str, list[dict]] | None = None) -> list[dict]:
    """Scan logs/<game_id>/ directories and build viewer data for each."""
    all_games = []
    if not os.path.isdir(logs_root):
        return all_games
    bif_data = bif_data or {}

    for entry in sorted(os.listdir(logs_root)):
        game_dir = os.path.join(logs_root, entry)
        game_log = os.path.join(game_dir, "game.jsonl")
        intro_log = os.path.join(game_dir, "introspection.jsonl")
        if not os.path.isdir(game_dir) or not os.path.isfile(game_log):
            continue
        if not os.path.isfile(intro_log) and not _is_branch_game(game_log) \
                and not _is_completed_game(game_log):
            # Probe-less games are included when they are branches (e.g.
            # bifurcation forks) or completed transcript-only runs (e.g. the
            # werewolf/resistance showcase); aborted probe-less runs stay out.
            continue
        try:
            data = build_viewer_data(game_log, intro_log, game_id=entry,
                                     bif_points=bif_data.get(entry))
            if not data["steps"]:
                print(f"  {entry[:8]}.. SKIP: no steps (aborted run?)")
                continue
            all_games.append(data)
        except Exception as e:
            print(f"  {entry[:8]}.. SKIP: {e}")
    return all_games


def main():
    parser = argparse.ArgumentParser(description="Prepare viewer data from game logs")
    parser.add_argument("-d", "--logs-dir", default="../logs",
                        help="Root logs directory containing per-game folders")
    parser.add_argument("-o", "--output", default="viewer_data.json")
    parser.add_argument("--game-id", default=None, help="Process single game")
    parser.add_argument("--bifurcation-dir", default="../../mafia2/data/bifurcation",
                        help="Bifurcation experiment data (points.json etc.); "
                             "missing dir degrades softly to no panel")
    args = parser.parse_args()

    bif_data = load_bifurcation(args.bifurcation_dir)
    if bif_data:
        n_pts = sum(len(v) for v in bif_data.values())
        print(f"Bifurcation data: {n_pts} points across {len(bif_data)} games")

    if args.game_id:
        game_dir = os.path.join(args.logs_dir, args.game_id)
        data = build_viewer_data(
            os.path.join(game_dir, "game.jsonl"),
            os.path.join(game_dir, "introspection.jsonl"),
            game_id=args.game_id,
            bif_points=bif_data.get(args.game_id),
        )
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Wrote {args.output} — {data['total_steps']} steps")
    else:
        all_games = scan_game_dirs(args.logs_dir, bif_data=bif_data)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(all_games, f, indent=2, ensure_ascii=False)
        print(f"Wrote {args.output} — {len(all_games)} games")


if __name__ == "__main__":
    main()
