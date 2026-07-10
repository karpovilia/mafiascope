"""
Player — wraps an LLM backend with game identity and conversation state.

The player keeps a *persistent* message list (system + game turns) that
grows as the game progresses.  Introspection probes are deliberately
excluded from this list — the engine builds throw-away message lists
from a snapshot of the context.
"""

from __future__ import annotations

import re
from typing import Any

from llm_backend import LLMBackend
from prompts import SYSTEM_PROMPTS, ACTION_PATTERNS, build_personality_block


class Player:
    def __init__(
        self,
        *,
        player_name: str,
        player_idx: int,
        role: str,           # "Mafia" | "Doctor" | "Villager"
        backend: LLMBackend,
        backend_name: str,   # config key, e.g. "deepseek" or "local"
        model_label: str,    # human-readable, e.g. "deepseek/deepseek-chat"
        language: str = "en",
        mafia_partners: list[str] | None = None,
        personality: dict | None = None,
    ):
        self.player_name = player_name
        self.player_idx = player_idx
        self.role = role
        self.backend = backend
        self.backend_name = backend_name
        self.model_label = model_label
        self.language = language
        self.personality = personality or {}
        self.alive = True

        # Build the system prompt (set once, never changes)
        partners = ", ".join(mafia_partners) if mafia_partners else "none"
        tpl = SYSTEM_PROMPTS[language][role]
        self.system_prompt: str = tpl.format(
            player_name=player_name,
            mafia_partners=partners,
        )
        # Append personality block
        self.system_prompt += build_personality_block(self.personality, language)

        # Persistent conversation context (system + game messages only)
        self._messages: list[dict[str, str]] = [
            {"role": "system", "content": self.system_prompt},
        ]

    # ── context management ───────────────────

    @property
    def context_messages(self) -> list[dict[str, str]]:
        """Read-only snapshot for introspection — no system prompt."""
        return list(self._messages[1:])

    def add_observation(self, content: str) -> None:
        """Add a game event that the player observes (as a 'user' turn)."""
        self._messages.append({"role": "user", "content": content})

    def full_messages(self) -> list[dict[str, str]]:
        """Full context INCLUDING the system prompt — for state snapshots."""
        return [dict(m) for m in self._messages]

    def restore_context(self, messages: list[dict[str, str]]) -> None:
        """Replace the whole conversation context (counterfactual replay)."""
        self._messages = [dict(m) for m in messages]
        if self._messages and self._messages[0]["role"] == "system":
            self.system_prompt = self._messages[0]["content"]

    # ── generation ───────────────────────────

    def respond(self, prompt: str) -> str:
        """
        Send *prompt* as the next user message, get the assistant reply,
        and commit both to the persistent context.
        """
        self._messages.append({"role": "user", "content": prompt})
        answer = self.backend.generate(self._messages)
        # strip <think> tags
        clean = re.sub(r"<[tT][hH][iI][nN][kK]>.*?</[tT][hH][iI][nN][kK]>", "", answer, flags=re.DOTALL)
        clean = re.sub(r"<[tT][hH][iI][nN][kK]>.*$", "", clean, flags=re.DOTALL)
        clean = clean.strip()
        self._messages.append({"role": "assistant", "content": clean})
        return clean

    # ── parsing helpers ──────────────────────

    def parse_action(self, text: str, action: str) -> str | None:
        """Extract a target name from ACTION/VOTE patterns.  action = 'kill'|'protect'|'vote'."""
        pat = ACTION_PATTERNS.get(self.language, ACTION_PATTERNS["en"]).get(action)
        if not pat:
            return None
        m = re.search(pat, text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    def __repr__(self) -> str:
        return f"<Player {self.player_name} role={self.role} model={self.model_label} alive={self.alive}>"
