"""
ResistanceGame — a Resistance/Avalon-lite loop for the multigame study.

Group dynamics WITHOUT night elimination: a rotating Leader proposes a team,
everyone publicly approves/rejects it, and (if approved) the team runs a
mission where Spies may secretly sabotage. Three successful missions win for
the Resistance; three sabotaged missions (or five consecutive rejected teams)
win for the Spies. Nobody is ever killed, so every player is alive the whole
game (suspicion_ranking / social_map probes stay valid; recall-by-round
metrics need a different normalization — flagged in the report, not folded
into the shared Mafia metrics).

Design constraints honored (idea 02, §2 and pitfalls):
- Emits the SAME JSONL shape as MafiaGame: one `logs/<game_id>/` dir with
  game.jsonl (setup carries `game_type: resistance`), introspection.jsonl
  (identical composite key round/public_msg_seq/player_idx/probe_seq and the
  SAME probe battery — untouched for comparability), and state.jsonl-free.
- Hidden roles never leak to the public channel: Spies know each other only
  privately; sabotage choices are secret and only the AGGREGATE mission
  outcome (success / #fails) is announced.
- This module is fully self-contained; it imports engine helpers but changes
  nothing in game.py / player.py, so Mafia traces are untouched.
"""

from __future__ import annotations

import os
import random
import re
import uuid
from typing import Any

from llm_backend import get_backend
from introspection import IntrospectionEngine, load_introspection_config
from game import GameEventLogger

# ── roles (minority = Spy) ──────────────────────────────────────
MINORITY_ROLE = "Spy"

_SYSTEM_PROMPTS = {
    "Spy": (
        "You are {player_name}, a member of a small covert crew in the game "
        "The Resistance.\n"
        "Your SECRET role is **Spy**. Fellow spies: {partners}.\n"
        "RULES:\n"
        "- The group runs a series of MISSIONS. Each round a Leader proposes a "
        "team; everyone votes to APPROVE or REJECT it.\n"
        "- If approved, each team member secretly plays SUCCESS or SABOTAGE. "
        "One sabotage FAILS the mission.\n"
        "- Spies win when 3 missions FAIL (or 5 team proposals are rejected in a "
        "row). The Resistance wins when 3 missions SUCCEED.\n"
        "You must HIDE that you are a Spy, get onto teams, and sabotage without "
        "being caught. Keep responses SHORT (2-4 sentences)."
    ),
    "Resistance": (
        "You are {player_name}, a loyal operative in the game The Resistance.\n"
        "Your role is **Resistance** (loyal).\n"
        "RULES:\n"
        "- The group runs a series of MISSIONS. Each round a Leader proposes a "
        "team; everyone votes to APPROVE or REJECT it.\n"
        "- If approved, each team member secretly plays SUCCESS or SABOTAGE. "
        "One sabotage FAILS the mission. Loyal operatives always play SUCCESS.\n"
        "- The Resistance wins when 3 missions SUCCEED. The Spies win when 3 "
        "missions FAIL (or 5 team proposals are rejected in a row).\n"
        "You must deduce who the hidden Spies are and keep them off the teams. "
        "Keep responses SHORT (2-4 sentences)."
    ),
}

_INTRO = (
    "This is the INTRODUCTION ROUND of The Resistance. Players: {alive}\n"
    "Introduce yourself in character. Share a bit about your personality and how "
    "you approach group decisions. Do NOT reveal your role. SHORT (2-3 sentences)."
)
_PROPOSE = (
    "MISSION {mission}. You are the LEADER. Players: {alive}.\n"
    "Mission outcomes so far: {history}\n"
    "Propose a team of EXACTLY {k} players (you may include yourself) to send on "
    "this mission. Argue briefly for your choice.\n"
    "End your message with:  TEAM: <name>, <name>{extra}"
)
_TEAMVOTE = (
    "MISSION {mission}. Proposed team: {team}. Players: {alive}.\n"
    "Do you APPROVE this team or REJECT it? Give one short reason.\n"
    "End your message with:  VOTE: Approve   or   VOTE: Reject"
)
_MISSION = (
    "MISSION {mission}. You are ON the team ({team}).\n"
    "Play your mission card secretly. Loyal operatives must play Success; only a "
    "Spy can Sabotage. Give one short private reasoning line.\n"
    "End your message with:  MISSION: Success   or   MISSION: Sabotage"
)

_TEAM_RE = re.compile(r"TEAM:\s*(.+)", re.I)
_VOTE_RE = re.compile(r"VOTE:\s*(Approve|Reject)", re.I)
_CARD_RE = re.compile(r"MISSION:\s*(Success|Sabotage)", re.I)

# team sizes per mission (index 0..4); standard 5p schedule, generic fallback
_TEAM_SCHEDULE = {5: [2, 3, 2, 3, 3], 6: [2, 3, 4, 3, 4],
                  7: [2, 3, 3, 4, 4], 8: [3, 4, 4, 5, 5]}


class _Agent:
    """Minimal player wrapper exposing the fields IntrospectionEngine needs."""

    def __init__(self, *, name, idx, role, backend, backend_name, model_label,
                 language, partners, personality):
        self.player_name = name
        self.player_idx = idx
        self.role = role
        self.backend = backend
        self.backend_name = backend_name
        self.model_label = model_label
        self.language = language
        self.personality = personality or {}
        self.alive = True  # nobody dies in Resistance
        partners_str = ", ".join(partners) if partners else "none"
        self.system_prompt = _SYSTEM_PROMPTS[role].format(
            player_name=name, partners=partners_str)
        self._messages: list[dict[str, str]] = [
            {"role": "system", "content": self.system_prompt}]

    @property
    def context_messages(self) -> list[dict[str, str]]:
        return list(self._messages[1:])

    def add_observation(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def respond(self, prompt: str) -> str:
        self._messages.append({"role": "user", "content": prompt})
        ans = self.backend.generate(self._messages)
        ans = re.sub(r"<[tT][hH][iI][nN][kK]>.*?</[tT][hH][iI][nN][kK]>", "",
                     ans, flags=re.DOTALL)
        ans = re.sub(r"<[tT][hH][iI][nN][kK]>.*$", "", ans, flags=re.DOTALL).strip()
        self._messages.append({"role": "assistant", "content": ans})
        return ans


class ResistanceGame:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.game_id = str(uuid.uuid4())
        g = cfg["game"]
        self.language = g.get("language", "en")
        self.intro_round = g.get("intro_round", False)
        self.game_type = g.get("game_type", "resistance")
        self._seed = g.get("seed")
        self.players: list[_Agent] = []
        self.round_num = 0
        self._msg_seq = 0
        self._wins = {"Resistance": 0, "Spy": 0}
        self._reject_streak = 0
        self._history: list[str] = []

        log_cfg = cfg.get("logging", {})
        log_dir = os.path.join(log_cfg.get("log_dir", "logs"), self.game_id)
        os.makedirs(log_dir, exist_ok=True)
        self.game_log_dir = log_dir
        self.console_level = log_cfg.get("console_level", "info")
        self.event_log = GameEventLogger(os.path.join(log_dir, "game.jsonl"))
        icfg = load_introspection_config(cfg.get("introspection", {}))
        icfg.log_file = os.path.join(log_dir, "introspection.jsonl")
        self.introspection = IntrospectionEngine(icfg, self.game_id)

    # ── helpers ──────────────────────────────
    def _print(self, m, level="info"):
        order = {"debug": 0, "info": 1, "warn": 2}
        if order.get(level, 1) >= order.get(self.console_level, 1):
            print(m)

    def _log(self, kind, data):
        self.event_log.log({"game_id": self.game_id, "kind": kind,
                            "round": self.round_num, **data})

    def _names(self):
        return [p.player_name for p in self.players]

    def _find(self, name):
        low = (name or "").strip().lower()
        for p in self.players:
            if low and low in p.player_name.lower():
                return p
        return None

    def _probe_all(self, phase, speaker=None):
        for p in self.players:
            self.introspection.after_message(
                backend=p.backend, system_prompt=p.system_prompt,
                round_num=self.round_num, public_msg_seq=self._msg_seq,
                player_idx=p.player_idx, player_name=p.player_name,
                model_name=p.model_label, role=p.role, phase=phase,
                alive_names=self._names(), context_messages=p.context_messages,
                is_speaker=(speaker is not None and p is speaker))

    def _broadcast(self, text, exclude=None):
        for p in self.players:
            if p is not exclude:
                p.add_observation(text)

    # ── setup ────────────────────────────────
    def setup(self):
        backends_cfg = self.cfg["backends"]
        roster = self.cfg["players"]
        from prompts import PLAYER_NAMES
        rng = random.Random(self._seed) if self._seed is not None else random
        names = rng.sample(PLAYER_NAMES, len(roster))
        idx_roster = list(enumerate(roster))
        rng.shuffle(idx_roster)
        data = [{"name": names[i], "role": s["role"], "backend": s["backend"],
                 "personality": s.get("personality"), "idx": i}
                for i, (_, s) in enumerate(idx_roster)]
        spies = [d["name"] for d in data if d["role"] == MINORITY_ROLE]
        for d in data:
            backend = get_backend(d["backend"], backends_cfg)
            partners = [n for n in spies if n != d["name"]] \
                if d["role"] == MINORITY_ROLE else None
            self.players.append(_Agent(
                name=d["name"], idx=d["idx"], role=d["role"], backend=backend,
                backend_name=d["backend"],
                model_label=backends_cfg[d["backend"]]["model"],
                language=self.language, partners=partners,
                personality=d.get("personality")))
        self._log("setup", {
            "game_type": self.game_type,
            "players": [{"name": p.player_name, "role": p.role,
                         "model": p.model_label, "backend": p.backend_name,
                         "personality": p.personality} for p in self.players],
            "backends": {name: get_backend(name, backends_cfg).describe()
                         for name in sorted({p.backend_name for p in self.players})},
        })
        self._print(f"Resistance {self.game_id}  players={len(self.players)}  "
                    f"spies={len(spies)}", "info")

    # ── rounds ───────────────────────────────
    def _team_size(self, mission_idx):
        sched = _TEAM_SCHEDULE.get(len(self.players))
        if sched:
            return sched[min(mission_idx, len(sched) - 1)]
        return max(2, len(self.players) // 2)

    def _intro(self):
        prompt = _INTRO.format(alive=", ".join(self._names()))
        for p in self.players:
            resp = p.respond(prompt)
            self._msg_seq += 1
            self._log("intro", {"player": p.player_name, "response": resp,
                                "msg_seq": self._msg_seq})
            self._broadcast(f"{p.player_name} introduces: {resp}", exclude=p)
            self._probe_all("intro", speaker=p)

    def _run_mission(self, mission_idx, leader):
        k = self._team_size(mission_idx)
        alive_str = ", ".join(self._names())
        extra = ", ..." if k > 2 else ""
        # 1) leader proposes
        prompt = _PROPOSE.format(mission=mission_idx + 1, alive=alive_str,
                                 history="; ".join(self._history) or "none",
                                 k=k, extra=extra)
        resp = leader.respond(prompt)
        self._msg_seq += 1
        self._log("team_proposal", {"leader": leader.player_name,
                  "response": resp, "msg_seq": self._msg_seq})
        m = _TEAM_RE.search(resp)
        team: list[_Agent] = []
        if m:
            for tok in re.split(r"[,\s]+", m.group(1)):
                pl = self._find(tok)
                if pl and pl not in team:
                    team.append(pl)
                if len(team) >= k:
                    break
        # fill/repair invalid proposals so the mission can proceed
        if len(team) < k:
            for p in [leader] + self.players:
                if p not in team:
                    team.append(p)
                if len(team) >= k:
                    break
        team = team[:k]
        team_names = [p.player_name for p in team]
        self._log("team_final", {"leader": leader.player_name, "team": team_names})
        self._broadcast(f"{leader.player_name} proposes the team: "
                        f"{', '.join(team_names)}.", exclude=leader)
        self._probe_all("team_proposal", speaker=leader)

        # 2) everyone votes approve/reject
        approvals = 0
        votes = {}
        for p in self.players:
            resp = p.respond(_TEAMVOTE.format(mission=mission_idx + 1,
                             team=", ".join(team_names), alive=alive_str))
            self._msg_seq += 1
            self._log("team_vote", {"player": p.player_name, "response": resp,
                      "msg_seq": self._msg_seq})
            vm = _VOTE_RE.search(resp)
            vote = vm.group(1).capitalize() if vm else "Reject"
            votes[p.player_name] = vote
            approvals += 1 if vote == "Approve" else 0
            self._broadcast(f"{p.player_name} voted to {vote} the team.", exclude=p)
            self._probe_all("team_vote", speaker=p)
        approved = approvals * 2 > len(self.players)
        self._log("team_vote_tally", {"votes": votes, "approvals": approvals,
                  "approved": approved})

        if not approved:
            self._reject_streak += 1
            self._history.append(f"M{mission_idx+1}: team rejected")
            self._broadcast(f"The team was REJECTED ({approvals}/"
                            f"{len(self.players)} approved). "
                            f"Consecutive rejections: {self._reject_streak}.")
            self._print(f"  >> Mission {mission_idx+1} team rejected "
                        f"({self._reject_streak} in a row)", "info")
            return False  # no mission run

        self._reject_streak = 0
        # 3) team runs the mission (secret cards)
        sabotage = 0
        for p in team:
            resp = p.respond(_MISSION.format(mission=mission_idx + 1,
                             team=", ".join(team_names)))
            self._msg_seq += 1
            # NB: card responses are private — logged for ground truth but NOT
            # broadcast to other players (only the aggregate is public).
            cm = _CARD_RE.search(resp)
            card = cm.group(1).capitalize() if cm else "Success"
            if p.role != MINORITY_ROLE:
                card = "Success"  # loyalists cannot sabotage
            if card == "Sabotage":
                sabotage += 1
            self._log("mission_card", {"player": p.player_name, "card": card,
                      "role": p.role, "response": resp, "msg_seq": self._msg_seq,
                      "private": True})
            self._probe_all("mission", speaker=p)
        success = sabotage == 0
        winner = "Resistance" if success else "Spy"
        self._wins[winner] += 1
        self._history.append(
            f"M{mission_idx+1}: {'SUCCESS' if success else 'FAIL'} "
            f"({sabotage} sabotage)")
        self._msg_seq += 1
        self._log("mission_result", {"mission": mission_idx + 1,
                  "success": success, "sabotages": sabotage, "team": team_names,
                  "msg_seq": self._msg_seq})
        self._broadcast(f"MISSION {mission_idx+1} RESULT: "
                        f"{'SUCCESS' if success else 'FAILURE'} "
                        f"with {sabotage} sabotage card(s). Team was "
                        f"{', '.join(team_names)}.")
        self._probe_all("mission_result")
        self._print(f"  >> Mission {mission_idx+1}: "
                    f"{'SUCCESS' if success else 'FAIL'} ({sabotage} sab)", "info")
        return True

    def run(self, resume=None):
        self.setup()
        if self.intro_round:
            self._intro()
        winner = None
        leader_i = 0
        mission_idx = 0
        max_missions = self.cfg["game"].get("max_rounds", 5)
        while mission_idx < max_missions:
            self.round_num = mission_idx + 1
            self._msg_seq = 0
            leader = self.players[leader_i % len(self.players)]
            leader_i += 1
            ran = self._run_mission(mission_idx, leader)
            # win checks
            if self._wins["Resistance"] >= 3:
                winner = "Resistance"; break
            if self._wins["Spy"] >= 3:
                winner = "Spy"; break     # Spy = the minority team (cf. Mafia)
            if self._reject_streak >= 5:
                winner = "Spy"; break
            if ran:
                mission_idx += 1
        if winner is None:
            winner = "Resistance" if self._wins["Resistance"] > self._wins["Spy"] \
                else "Spy"
        self._log("game_over", {"winner": winner, "wins": self._wins,
                  "backends": {p.backend_name: p.backend.describe()
                               for p in self.players}})
        self.event_log.close()
        self.introspection.close()
        self._print(f"\n  GAME OVER — {winner} win  "
                    f"(R{self._wins['Resistance']}/S{self._wins['Spy']})", "info")
        return {"game_id": self.game_id, "winner": winner,
                "rounds": self.round_num, "game_type": self.game_type,
                "players": [{"name": p.player_name, "role": p.role,
                             "model": p.model_label, "alive": p.alive}
                            for p in self.players]}
