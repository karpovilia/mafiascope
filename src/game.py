"""
MafiaGame — core game loop with introspection hooks.

Flow per round:
    Night  →  mafia kills, doctor protects, sheriff checks
    Day    →  discussion  →  voting  →  elimination
After every public utterance AND at round end, the IntrospectionEngine
fires private probes (if enabled) and logs them to JSONL.
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any

from llm_backend import get_backend
from player import Player
from prompts import (
    PLAYER_NAMES,
    NIGHT_ACTION,
    DAY_DISCUSS,
    DAY_DISCUSS_DETAILED,
    DAY_VOTE,
    DAY_VOTE_DETAILED,
    INTRO_PROMPT,
)
from introspection import (
    IntrospectionEngine,
    load_introspection_config,
)


# ────────────────────────────────────────────
#  Lightweight JSONL game-event logger
# ────────────────────────────────────────────

class GameEventLogger:
    def __init__(self, path: str):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(p, "a", encoding="utf-8")

    def log(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", time.time())
        self._fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


# ────────────────────────────────────────────
#  MafiaGame
# ────────────────────────────────────────────

class MafiaGame:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.game_id = str(uuid.uuid4())

        game_cfg = cfg["game"]
        self.max_rounds: int = game_cfg["max_rounds"]
        self.language: str = game_cfg.get("language", "en")
        self.detailed_reasoning: bool = game_cfg.get("detailed_reasoning", False)
        self.intro_round: bool = game_cfg.get("intro_round", False)
        self.snapshots_enabled: bool = game_cfg.get("snapshots", True)

        self.players: list[Player] = []
        self.round_num = 0
        self._round_msg_seq = 0
        # Resume point restored by from_snapshot (None for fresh games)
        self._pending: dict[str, Any] | None = None

        # Sheriff's check history: {player_name: "Mafia" | "Not Mafia"}
        self._sheriff_checks: dict[str, str] = {}

        # Loggers — each game gets its own directory: logs/<game_id>/
        log_cfg = cfg.get("logging", {})
        log_root = log_cfg.get("log_dir", "logs")
        game_log_dir = os.path.join(log_root, self.game_id)
        os.makedirs(game_log_dir, exist_ok=True)

        self.game_log_dir = game_log_dir
        self.event_log = GameEventLogger(os.path.join(game_log_dir, "game.jsonl"))
        self.console_level = log_cfg.get("console_level", "info")
        # Full-state snapshots for counterfactual replay ("branch from here")
        self.state_log = GameEventLogger(os.path.join(game_log_dir, "state.jsonl")) \
            if self.snapshots_enabled else None

        intro_cfg = load_introspection_config(cfg.get("introspection", {}))
        intro_cfg.log_file = os.path.join(game_log_dir, "introspection.jsonl")
        self.introspection = IntrospectionEngine(intro_cfg, self.game_id)

    # ── counterfactual replay: restore from snapshot ──

    @classmethod
    def from_snapshot(
        cls,
        cfg: dict[str, Any],
        parent_log_dir: str,
        round_num: int,
        msg_seq: int,
        *,
        backend_override: str | None = None,
        fork_meta: dict[str, Any] | None = None,
    ) -> "MafiaGame":
        """
        Fork a recorded game at composite key (round, msg_seq).

        Restores every player's full conversation context from
        logs/<parent>/state.jsonl; the new game continues from the step
        AFTER the snapshot.  LLM sampling is non-deterministic by design —
        the snapshot restores the information state, not the randomness.
        """
        state_path = os.path.join(parent_log_dir, "state.jsonl")
        rec: dict[str, Any] | None = None
        available: list[tuple[int, int]] = []
        with open(state_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                available.append((r["round"], r["msg_seq"]))
                if r["round"] == round_num and r["msg_seq"] == msg_seq:
                    rec = r
        if rec is None:
            raise ValueError(
                f"No snapshot at round={round_num} msg_seq={msg_seq}. "
                f"Available: {available}"
            )

        game = cls(cfg)
        game.round_num = rec["round"]
        game._round_msg_seq = rec["msg_seq"]
        game._sheriff_checks = dict(rec.get("sheriff_checks", {}))

        backends_cfg = cfg["backends"]
        mafia_names = [ps["name"] for ps in rec["players"] if ps["role"] == "Mafia"]
        for ps in rec["players"]:
            backend_name = backend_override or ps["backend_name"]
            backend = get_backend(backend_name, backends_cfg)
            model_label = backends_cfg[backend_name]["model"]
            partners = [n for n in mafia_names if n != ps["name"]] if ps["role"] == "Mafia" else None
            p = Player(
                player_name=ps["name"],
                player_idx=ps.get("idx", 0),
                role=ps["role"],
                backend=backend,
                backend_name=backend_name,
                model_label=model_label,
                language=ps.get("language", game.language),
                mafia_partners=partners,
                personality=ps.get("personality"),
            )
            p.restore_context(ps["messages"])
            p.alive = ps["alive"]
            game.players.append(p)

        fork_setup: dict[str, Any] = {
            "players": [
                {"name": p.player_name, "role": p.role, "model": p.model_label,
                 "backend": p.backend_name, "personality": p.personality}
                for p in game.players
            ],
            "forked_from": rec["game_id"],
            "fork_point": [round_num, msg_seq],
            **(fork_meta or {}),
        }
        if game.introspection.cfg.feedback_to_context:
            fork_setup["feedback_to_context"] = True
        game._log_event("setup", fork_setup)
        game._pending = dict(rec.get("pending") or {})
        game._pending.setdefault("night_result", rec.get("night_result"))
        game._print(
            f"Forked {rec['game_id'][:8]} @ R{round_num}.{msg_seq} → {game.game_id}", "info")
        return game

    # ── setup from explicit roster ───────────

    def setup(self) -> None:
        backends_cfg = self.cfg["backends"]
        roster = self.cfg["players"]  # list of {role, backend}

        names = random.sample(PLAYER_NAMES, len(roster))
        random.shuffle(names)  # shuffle so role order isn't predictable

        # Shuffle the roster too so roles aren't in config order
        indexed_roster = list(enumerate(roster))
        random.shuffle(indexed_roster)

        # First pass: collect mafia names
        player_data: list[dict[str, Any]] = []
        for new_idx, (_, slot) in enumerate(indexed_roster):
            player_data.append({
                "name": names[new_idx],
                "role": slot["role"],
                "backend_name": slot["backend"],
                "personality": slot.get("personality"),
                "idx": new_idx,
            })

        mafia_names = [d["name"] for d in player_data if d["role"] == "Mafia"]

        # Second pass: create Player objects
        for d in player_data:
            backend = get_backend(d["backend_name"], backends_cfg)
            model_label = backends_cfg[d["backend_name"]]["model"]
            partners = [n for n in mafia_names if n != d["name"]] if d["role"] == "Mafia" else None
            p = Player(
                player_name=d["name"],
                player_idx=d["idx"],
                role=d["role"],
                backend=backend,
                backend_name=d["backend_name"],
                model_label=model_label,
                language=self.language,
                mafia_partners=partners,
                personality=d.get("personality"),
            )
            self.players.append(p)

        setup_data: dict[str, Any] = {
            "players": [
                {"name": p.player_name, "role": p.role, "model": p.model_label,
                 "backend": p.backend_name, "personality": p.personality}
                for p in self.players
            ],
            # full sanitized model settings per backend (temperature, token
            # budgets, reasoning knobs, versions, ...) — trace provenance
            "backends": {
                name: get_backend(name, backends_cfg).describe()
                for name in sorted({p.backend_name for p in self.players})
            },
        }
        # Corpus-selector marker for the second-order-feedback arm
        # (analogous to forked_from: present only when the arm is active).
        if self.introspection.cfg.feedback_to_context:
            setup_data["feedback_to_context"] = True
            setup_data["feedback_order"] = self.introspection.cfg.feedback_order
            if self.introspection.cfg.feedback_players is not None:
                setup_data["feedback_players"] = list(self.introspection.cfg.feedback_players)
        self._log_event("setup", setup_data)
        num_players = len(self.players)
        mafia_count = sum(1 for p in self.players if p.role == "Mafia")
        self._print(f"Game {self.game_id}  players={num_players}  mafia={mafia_count}", "info")
        for p in self.players:
            self._print(f"  {p.player_name:10s}  {p.role:10s}  [{p.model_label}]", "info")

    # ── helpers ──────────────────────────────

    def alive(self) -> list[Player]:
        return [p for p in self.players if p.alive]

    def alive_names(self) -> list[str]:
        return [p.player_name for p in self.alive()]

    def _find_player(self, name: str) -> Player | None:
        low = name.lower()
        for p in self.alive():
            if low in p.player_name.lower():
                return p
        return None

    def _find_any_player(self, name: str) -> Player | None:
        """Find player by name even if dead (for sheriff check results)."""
        low = name.lower()
        for p in self.players:
            if low in p.player_name.lower():
                return p
        return None

    def _print(self, msg: str, level: str = "info") -> None:
        levels = {"debug": 0, "info": 1, "warn": 2}
        if levels.get(level, 1) >= levels.get(self.console_level, 1):
            print(msg)

    def _log_event(self, kind: str, data: dict[str, Any]) -> None:
        self.event_log.log({"game_id": self.game_id, "kind": kind, "round": self.round_num, **data})

    def _snapshot(self, phase: str, pending: dict[str, Any],
                  night_result: str | None = None, msg_seq: int | None = None) -> None:
        """Serialize the full game state at the current composite key
        (round, msg_seq) — enough to fork the game from this exact step."""
        if not self.state_log:
            return
        self.state_log.log({
            "game_id": self.game_id,
            "round": self.round_num,
            "msg_seq": self._round_msg_seq if msg_seq is None else msg_seq,
            "phase": phase,
            "pending": pending,
            "night_result": night_result,
            "sheriff_checks": dict(self._sheriff_checks),
            "alive": self.alive_names(),
            "players": [
                {"name": p.player_name, "idx": p.player_idx, "role": p.role,
                 "backend_name": p.backend_name, "model_label": p.model_label,
                 "personality": p.personality, "language": p.language,
                 "alive": p.alive, "messages": p.full_messages()}
                for p in self.players
            ],
        })

    # ── introspection shortcuts ────────────────

    def _feedback_prefix(self, player: Player) -> str:
        """Second-order-feedback arm (introspection.feedback_to_context).

        When the flag is ON, returns the player's own latest probe answers
        as a private block to prepend to their next action prompt.
        When OFF (default), returns "" and the prompt stays byte-identical
        to baseline.  Only the acting player's own answers, only for them.
        """
        block = self.introspection.feedback_block(player.player_name, player.player_idx)
        return f"{block}\n\n" if block else ""

    def _probe_one(self, player: Player, phase: str) -> None:
        """Probe a single player (for night actions)."""
        self.introspection.after_message(
            backend=player.backend,
            system_prompt=player.system_prompt,
            round_num=self.round_num,
            public_msg_seq=self._round_msg_seq,
            player_idx=player.player_idx,
            player_name=player.player_name,
            model_name=player.model_label,
            role=player.role,
            phase=phase,
            alive_names=self.alive_names(),
            context_messages=player.context_messages,
            is_speaker=True,
        )

    def _probe_all(self, phase: str, speaker: Player | None = None) -> None:
        """Probe ALL alive players. speaker gets own_turn probes too."""
        for p in self.alive():
            self.introspection.after_message(
                backend=p.backend,
                system_prompt=p.system_prompt,
                round_num=self.round_num,
                public_msg_seq=self._round_msg_seq,
                player_idx=p.player_idx,
                player_name=p.player_name,
                model_name=p.model_label,
                role=p.role,
                phase=phase,
                alive_names=self.alive_names(),
                context_messages=p.context_messages,
                is_speaker=(speaker is not None and p is speaker),
            )

    def _probe_round_end(self) -> None:
        for p in self.alive():
            self.introspection.after_round(
                backend=p.backend,
                system_prompt=p.system_prompt,
                round_num=self.round_num,
                player_idx=p.player_idx,
                player_name=p.player_name,
                model_name=p.model_label,
                role=p.role,
                alive_names=self.alive_names(),
                context_messages=p.context_messages,
            )

    # ── game-over check ──────────────────────

    def check_game_over(self) -> tuple[bool, str | None]:
        mafia_alive = sum(1 for p in self.alive() if p.role == "Mafia")
        innocent_alive = sum(1 for p in self.alive() if p.role != "Mafia")
        if mafia_alive == 0:
            return True, "Villagers"
        if mafia_alive >= innocent_alive:
            return True, "Mafia"
        if self.round_num >= self.max_rounds:
            return True, "Villagers" if innocent_alive > mafia_alive else "Mafia"
        return False, None

    # ── sheriff history string ───────────────

    def _sheriff_history_str(self) -> str:
        if not self._sheriff_checks:
            return "You have not checked anyone yet."
        lines = ["Your previous check results:"]
        for name, result in self._sheriff_checks.items():
            lines.append(f"  - {name}: {result}")
        return "\n".join(lines)

    # ── night phase ──────────────────────────

    def _night(self, resume: dict[str, Any] | None = None) -> str:
        self._print(f"\n{'='*50}\n  NIGHT — round {self.round_num}\n{'='*50}", "info")
        alive_str = ", ".join(self.alive_names())

        # Resume bookkeeping: which sub-stage we re-enter and what was
        # already decided before the snapshot was taken.
        stage = resume.get("stage") if resume else None
        remaining = set(resume.get("remaining", [])) if resume else None
        mafia_target_names: list[str] = list(resume.get("mafia_targets", [])) if resume else []
        protected_name: str | None = resume.get("protected") if resume else None
        stage_order = {"night_mafia": 0, "night_doctor": 1, "night_sheriff": 2}
        entry = stage_order.get(stage, 0) if stage else 0

        # ── Mafia ──
        mafia_players = [p for p in self.alive() if p.role == "Mafia"]
        if entry <= stage_order["night_mafia"]:
            for i, p in enumerate(mafia_players):
                if stage == "night_mafia" and remaining is not None and p.player_name not in remaining:
                    continue
                prompt = NIGHT_ACTION[self.language]["Mafia"].format(
                    round=self.round_num, alive=alive_str,
                )
                self._print(f"  [Mafia] {p.player_name} thinking...", "info")
                resp = p.respond(self._feedback_prefix(p) + prompt)
                self._round_msg_seq += 1
                self._print(f"  [Mafia] {p.player_name}: {resp}", "info")
                self._log_event("night_mafia", {"player": p.player_name, "response": resp, "msg_seq": self._round_msg_seq})
                # Share only the action with other mafia (not the reasoning)
                target_name = p.parse_action(resp, "kill")
                action_summary = f"Kill {target_name}" if target_name else "no valid target"
                for other in mafia_players:
                    if other is not p and other.alive:
                        other.add_observation(f"[Mafia ally] {p.player_name} chose: {action_summary}")
                self._probe_one(p, "night")

                if target_name:
                    t = self._find_player(target_name)
                    if t and t.role != "Mafia":
                        mafia_target_names.append(t.player_name)

                self._snapshot("night", {
                    "stage": "night_mafia",
                    "remaining": [q.player_name for q in mafia_players[i + 1:]],
                    "mafia_targets": list(mafia_target_names),
                })

        # majority target
        kill_target: Player | None = None
        if mafia_target_names:
            counts: dict[str, int] = {}
            for name in mafia_target_names:
                counts[name] = counts.get(name, 0) + 1
            best = max(counts, key=lambda n: counts[n])
            kill_target = self._find_player(best)

        # ── Doctor ──
        doctors = [p for p in self.alive() if p.role == "Doctor"]
        if entry <= stage_order["night_doctor"]:
            for i, p in enumerate(doctors):
                if stage == "night_doctor" and remaining is not None and p.player_name not in remaining:
                    continue
                prompt = NIGHT_ACTION[self.language]["Doctor"].format(
                    round=self.round_num, alive=alive_str,
                )
                self._print(f"  [Doctor] {p.player_name} thinking...", "info")
                resp = p.respond(self._feedback_prefix(p) + prompt)
                self._round_msg_seq += 1
                self._print(f"  [Doctor] {p.player_name}: {resp}", "info")
                self._log_event("night_doctor", {"player": p.player_name, "response": resp, "msg_seq": self._round_msg_seq})
                self._probe_one(p, "night")

                target_name = p.parse_action(resp, "protect")
                if target_name:
                    protected_name = target_name

                self._snapshot("night", {
                    "stage": "night_doctor",
                    "remaining": [q.player_name for q in doctors[i + 1:]],
                    "mafia_targets": list(mafia_target_names),
                    "protected": protected_name,
                })
        protected: Player | None = self._find_player(protected_name) if protected_name else None

        # ── Sheriff ──
        sheriffs = [p for p in self.alive() if p.role == "Sheriff"]
        for i, p in enumerate(sheriffs):
            if stage == "night_sheriff" and remaining is not None and p.player_name not in remaining:
                continue
            prompt = NIGHT_ACTION[self.language]["Sheriff"].format(
                round=self.round_num,
                alive=alive_str,
                sheriff_history=self._sheriff_history_str(),
            )
            self._print(f"  [Sheriff] {p.player_name} thinking...", "info")
            resp = p.respond(prompt)
            self._round_msg_seq += 1
            self._print(f"  [Sheriff] {p.player_name}: {resp}", "info")
            self._log_event("night_sheriff", {"player": p.player_name, "response": resp, "msg_seq": self._round_msg_seq})
            self._probe_one(p, "night")

            target_name = p.parse_action(resp, "check")
            if target_name:
                checked = self._find_player(target_name)
                if checked:
                    check_result = "Mafia" if checked.role == "Mafia" else "Not Mafia"
                    self._sheriff_checks[checked.player_name] = check_result
                    # Tell the sheriff the result (added to their context)
                    result_msg = f"CHECK RESULT: {checked.player_name} is {check_result}."
                    p.add_observation(result_msg)
                    self._print(f"  [Sheriff] >> {checked.player_name} is {check_result}", "info")
                    self._log_event("night_sheriff_result", {
                        "sheriff": p.player_name,
                        "checked": checked.player_name,
                        "result": check_result,
                    })

            self._snapshot("night", {
                "stage": "night_sheriff",
                "remaining": [q.player_name for q in sheriffs[i + 1:]],
                "mafia_targets": list(mafia_target_names),
                "protected": protected_name,
            })

        # ── Resolve night ──
        result_parts: list[str] = []
        if kill_target and kill_target != protected:
            kill_target.alive = False
            msg = f"{kill_target.player_name} was killed by the Mafia."
            result_parts.append(msg)
            self._print(f"  >> {msg}", "info")
            self._log_event("night_kill", {"victim": kill_target.player_name})
        elif kill_target and kill_target == protected:
            msg = f"The Doctor saved {kill_target.player_name}!"
            result_parts.append(msg)
            self._print(f"  >> {msg}", "info")
            self._log_event("night_save", {"saved": kill_target.player_name})
        else:
            result_parts.append("No one was killed tonight.")
            self._print("  >> No one was killed tonight.", "info")

        return " ".join(result_parts)

    # ── day phase ────────────────────────────

    def _night_analysis(self, night_result: str) -> None:
        """Probe all alive players about the night result (before discussion)."""
        self._round_msg_seq += 1
        self._log_event("night_analysis", {"night_result": night_result})
        self._print(f"  [Night Analysis] All players react to: {night_result}", "info")
        # Tell all alive players what happened
        for p in self.alive():
            p.add_observation(f"NIGHT RESULT: {night_result}")
        self._probe_all("night_analysis")
        self._snapshot("night_analysis", {
            "stage": "day_discuss",
            "remaining": self.alive_names(),
        }, night_result=night_result)

    def _day(self, night_result: str, resume: dict[str, Any] | None = None) -> None:
        self._print(f"\n{'='*50}\n  DAY — round {self.round_num}\n{'='*50}", "info")

        stage = resume.get("stage") if resume else None
        remaining = set(resume.get("remaining", [])) if resume else None

        # Night analysis step — all players react to night outcome
        if resume is None:
            self._night_analysis(night_result)

        alive_str = ", ".join(self.alive_names())
        alive_players = self.alive()

        # Discussion
        if stage != "day_vote":
            discuss_tpl = DAY_DISCUSS_DETAILED if self.detailed_reasoning else DAY_DISCUSS
            discuss_prompt = discuss_tpl[self.language].format(
                round=self.round_num, alive=alive_str, night_result=night_result,
            )
            for i, p in enumerate(alive_players):
                if stage == "day_discuss" and remaining is not None and p.player_name not in remaining:
                    continue
                self._print(f"  {p.player_name} thinking...", "info")
                resp = p.respond(self._feedback_prefix(p) + discuss_prompt)
                self._round_msg_seq += 1
                self._print(f"  {p.player_name}: {resp}", "info")
                self._log_event("day_discuss", {"player": p.player_name, "response": resp, "msg_seq": self._round_msg_seq})
                # Broadcast FIRST so everyone has the message in context
                for other in alive_players:
                    if other is not p:
                        other.add_observation(f"{p.player_name} said: {resp}")
                # THEN probe all (speaker gets own_turn probes too)
                self._probe_all("day_discuss", speaker=p)
                self._snapshot("day_discuss", {
                    "stage": "day_discuss",
                    "remaining": [q.player_name for q in alive_players[i + 1:]],
                }, night_result=night_result)

        # Voting
        vote_tpl = DAY_VOTE_DETAILED if self.detailed_reasoning else DAY_VOTE
        vote_prompt = vote_tpl[self.language].format(
            round=self.round_num, alive=alive_str,
        )
        votes: dict[str, str] = dict(resume.get("votes", {})) if resume else {}
        for i, p in enumerate(alive_players):
            if stage == "day_vote" and remaining is not None and p.player_name not in remaining:
                continue
            resp = p.respond(self._feedback_prefix(p) + vote_prompt)
            self._round_msg_seq += 1
            self._print(f"  {p.player_name} votes: {resp}", "info")
            self._log_event("day_vote", {"player": p.player_name, "response": resp, "msg_seq": self._round_msg_seq})
            # Broadcast vote to others first
            for other in alive_players:
                if other is not p:
                    other.add_observation(f"{p.player_name} voted: {resp}")
            self._probe_all("day_vote", speaker=p)

            target_name = p.parse_action(resp, "vote")
            if target_name:
                t = self._find_player(target_name)
                if t:
                    votes[p.player_name] = t.player_name

            self._snapshot("day_vote", {
                "stage": "day_vote",
                "remaining": [q.player_name for q in alive_players[i + 1:]],
                "votes": dict(votes),
            }, night_result=night_result)

        # tally
        tally: dict[str, int] = {}
        for target in votes.values():
            tally[target] = tally.get(target, 0) + 1

        self._log_event("vote_tally", {"tally": tally, "votes": votes})

        if not tally:
            self._print("  >> No valid votes. No elimination.", "info")
            return

        max_votes = max(tally.values())
        top = [name for name, cnt in tally.items() if cnt == max_votes]
        if len(top) > 1:
            self._print(f"  >> Tie between {top}. No elimination.", "info")
            return

        eliminated = self._find_player(top[0])
        if eliminated:
            eliminated.alive = False
            self._print(f"  >> {eliminated.player_name} ({eliminated.role}) eliminated with {max_votes} votes.", "info")
            self._log_event("day_eliminate", {
                "eliminated": eliminated.player_name,
                "role": eliminated.role,
                "votes": max_votes,
            })
            for p in self.alive():
                p.add_observation(f"{eliminated.player_name} was eliminated by vote.")

    # ── main loop ────────────────────────────

    def _intro_round(self, resume: dict[str, Any] | None = None) -> None:
        """Introduction round — players meet and present themselves."""
        self._print(f"\n{'='*50}\n  INTRODUCTION ROUND\n{'='*50}", "info")
        alive_str = ", ".join(self.alive_names())
        prompt = INTRO_PROMPT[self.language].format(alive=alive_str)
        remaining = set(resume.get("remaining", [])) if resume else None

        alive_players = self.alive()
        for i, p in enumerate(alive_players):
            if remaining is not None and p.player_name not in remaining:
                continue
            self._print(f"  {p.player_name} introduces...", "info")
            resp = p.respond(prompt)
            self._round_msg_seq += 1
            self._print(f"  {p.player_name}: {resp}", "info")
            self._log_event("intro", {"player": p.player_name, "response": resp, "msg_seq": self._round_msg_seq})
            # Broadcast to others first
            for other in self.alive():
                if other is not p:
                    other.add_observation(f"{p.player_name} introduces: {resp}")
            # Probe all after each introduction
            self._probe_all("intro", speaker=p)
            self._snapshot("intro", {
                "stage": "intro",
                "remaining": [q.player_name for q in alive_players[i + 1:]],
            })

    def run(self, resume: dict[str, Any] | None = None) -> dict[str, Any]:
        """Play the game.  With `resume` (from from_snapshot's pending state),
        skip setup and continue mid-game from the snapshot point."""
        if resume is None:
            resume = self._pending  # set by from_snapshot
        winner: str | None = None

        if resume is None:
            self.setup()
            # Introduction round (round 0)
            if self.intro_round:
                self._intro_round()
        elif resume.get("stage") == "intro":
            self._intro_round(resume=resume)
            resume = None

        while True:
            if resume is not None:
                # Re-enter the current (restored) round mid-flight
                stage = resume.get("stage", "")
                night_result = resume.get("night_result") or ""
                if stage.startswith("night_"):
                    night_result = self._night(resume=resume)
                    over, winner = self.check_game_over()
                    if over:
                        break
                    self._day(night_result)
                elif stage in ("day_discuss", "day_vote"):
                    self._day(night_result, resume=resume)
                elif stage == "round_end":
                    resume = None
                    continue  # round was complete; start the next one normally
                over, winner = self.check_game_over()
                if over:
                    break
                self._probe_round_end()
                self._snapshot("round_end", {"stage": "round_end"}, msg_seq=-1)
                resume = None
                continue

            self.round_num += 1
            self._round_msg_seq = 0

            night_result = self._night()
            over, winner = self.check_game_over()
            if over:
                break

            self._day(night_result)
            over, winner = self.check_game_over()
            if over:
                break

            self._probe_round_end()
            self._snapshot("round_end", {"stage": "round_end"}, msg_seq=-1)

        self._print(f"\n{'#'*50}\n  GAME OVER — {winner} win!  (round {self.round_num})\n{'#'*50}", "info")
        # backends described again at game end: API backends now know the
        # exact served model version (served_model) from real responses
        self._log_event("game_over", {
            "winner": winner,
            "backends": {p.backend_name: p.backend.describe() for p in self.players},
        })

        self.event_log.close()
        self.introspection.close()

        return {
            "game_id": self.game_id,
            "winner": winner,
            "rounds": self.round_num,
            "players": [
                {"name": p.player_name, "role": p.role, "model": p.model_label, "alive": p.alive}
                for p in self.players
            ],
        }
