"""
Introspection engine — private "mind-reading" probes.

After each public message (or at round end) the engine sends a probe
to the model, logs the answer to JSONL, and **discards** it from the
game context so it never bloats the conversation history.

Composite key per record:
    round / public_msg_seq / player_idx / probe_seq
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from llm_backend import LLMBackend


# ────────────────────────────────────────────
#  Data structures
# ────────────────────────────────────────────

@dataclass
class ProbeConfig:
    id: str
    question: str          # may contain {player}, {players}
    max_tokens: int = 300  # per-probe token limit
    when: str = "always"   # "always" | "own_turn" — when to fire this probe


@dataclass
class IntrospectionConfig:
    enabled: bool = True
    after_each_message: bool = True
    after_each_round: bool = True
    probes: list[ProbeConfig] = field(default_factory=list)
    log_file: str = "logs/introspection.jsonl"
    # Experimental arm ("second-order feedback"): if True, each agent's own
    # latest probe answers (role_assessment + social_map) are injected into
    # its private prompt before each of its own moves.  Invasive by design;
    # strictly opt-in — False reproduces the baseline engine bit-for-bit.
    feedback_to_context: bool = False


@dataclass
class ProbeRecord:
    """One row in the JSONL log."""
    timestamp: float
    game_id: str
    round: int
    public_msg_seq: int       # 0-based within the round; -1 = round-end summary
    player_idx: int           # index in the players list
    player_name: str
    probe_seq: int            # 0-based within this probe batch
    probe_id: str
    question: str
    answer_raw: str           # raw LLM text
    answer_parsed: Any        # structured JSON (dict/list) or None if parse failed
    answer_parse_ok: bool     # True if JSON was extracted successfully
    model: str
    role: str
    phase: str                # night / day_discuss / day_vote / round_end
    latency_ms: int = 0
    # True if the value passed forward for probe chaining ({prev_*}/{last_*})
    # had to fall back to the raw answer text because parse+repair failed.
    chain_used_raw_fallback: bool = False


# ────────────────────────────────────────────
#  JSONL writer
# ────────────────────────────────────────────

class IntrospectionLogger:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, record: ProbeRecord) -> None:
        self._fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


# ────────────────────────────────────────────
#  Engine
# ────────────────────────────────────────────

class IntrospectionEngine:
    """
    Sends probes to a player's backend and logs the answers.

    Key guarantee: the probe messages are NEVER appended to the player's
    persistent conversation context — they are built ad-hoc, sent, logged,
    and thrown away.
    """

    def __init__(self, cfg: IntrospectionConfig, game_id: str):
        self.cfg = cfg
        self.game_id = game_id
        self.logger = IntrospectionLogger(cfg.log_file) if cfg.enabled else None
        # Per-player memory of last probe answers (persists across steps),
        # used for chaining via {prev_*}/{last_*} placeholders.
        # {player_name: {probe_id: chain_value}} where chain_value is the
        # PARSED (repaired if needed) JSON compactly re-serialized; the raw
        # answer text is used only as a logged fallback when parse+repair
        # failed (see _chain_value).
        self._last_answers: dict[str, dict[str, str]] = {}

    # ── public API ───────────────────────────

    def after_message(
        self,
        *,
        backend: "LLMBackend",
        system_prompt: str,
        round_num: int,
        public_msg_seq: int,
        player_idx: int,
        player_name: str,
        model_name: str,
        role: str,
        phase: str,
        alive_names: list[str],
        context_messages: list[dict[str, str]],
        is_speaker: bool = False,
    ) -> None:
        """Run probes after a single public message."""
        if not self.cfg.enabled or not self.cfg.after_each_message:
            return
        self._run_probes(
            backend=backend,
            system_prompt=system_prompt,
            round_num=round_num,
            public_msg_seq=public_msg_seq,
            player_idx=player_idx,
            player_name=player_name,
            model_name=model_name,
            role=role,
            phase=phase,
            alive_names=alive_names,
            context_messages=context_messages,
            is_speaker=is_speaker,
        )

    def after_round(
        self,
        *,
        backend: "LLMBackend",
        system_prompt: str,
        round_num: int,
        player_idx: int,
        player_name: str,
        model_name: str,
        role: str,
        alive_names: list[str],
        context_messages: list[dict[str, str]],
    ) -> None:
        """Run probes at round end."""
        if not self.cfg.enabled or not self.cfg.after_each_round:
            return
        self._run_probes(
            backend=backend,
            system_prompt=system_prompt,
            round_num=round_num,
            public_msg_seq=-1,
            player_idx=player_idx,
            player_name=player_name,
            model_name=model_name,
            role=role,
            phase="round_end",
            alive_names=alive_names,
            context_messages=context_messages,
        )

    def close(self) -> None:
        if self.logger:
            self.logger.close()

    def feedback_block(self, player_name: str) -> str | None:
        """Second-order-feedback arm (introspection.feedback_to_context).

        Returns a short private block with THIS player's own latest probe
        answers (role_assessment + social_map), to be prepended to the
        player's next action prompt by the game loop.  Returns None when
        the flag is off (default) or no answers exist yet — in that case
        the game loop leaves the prompt byte-identical to baseline.
        Never touches other players and never alters the probes themselves.
        """
        if not self.cfg.enabled or not self.cfg.feedback_to_context:
            return None
        last = self._last_answers.get(player_name)
        if not last:
            return None
        parts: list[str] = []
        ra = last.get("role_assessment")
        if ra and ra != "N/A":
            parts.append(f"role beliefs: {ra}")
        sm = last.get("social_map")
        if sm and sm != "N/A":
            parts.append(f"how others see you: {sm}")
        if not parts:
            return None
        return (
            "Your latest private self-assessment (not visible to others): "
            + "; ".join(parts)
        )

    # ── JSON extraction ─────────────────────

    @staticmethod
    def _extract_json(text: str) -> tuple[Any, bool]:
        """
        Try to extract a JSON object or array from LLM output.
        Handles markdown fences, leading text, trailing junk.
        Returns (parsed_value, success).
        """
        # strip markdown code fences
        cleaned = re.sub(r"```(?:json)?\s*", "", text)
        cleaned = re.sub(r"```", "", cleaned)
        cleaned = cleaned.strip()

        # Try the whole string first
        for candidate in [cleaned, text.strip()]:
            try:
                return json.loads(candidate), True
            except (json.JSONDecodeError, ValueError):
                pass

        # Truncated output (max_tokens cut-off) or trailing junk: keep
        # complete elements, close the open brackets and re-parse.
        starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
        if starts:
            repaired = IntrospectionEngine._repair_truncated_json(cleaned[min(starts):])
            if repaired is not None:
                try:
                    return json.loads(repaired), True
                except (json.JSONDecodeError, ValueError):
                    pass

        # Last resort: first { or [ greedily matched to last } or ]
        for open_ch, close_ch in [("{", "}"), ("[", "]")]:
            start = cleaned.find(open_ch)
            if start == -1:
                continue
            end = cleaned.rfind(close_ch)
            if end == -1 or end <= start:
                continue
            try:
                return json.loads(cleaned[start : end + 1]), True
            except (json.JSONDecodeError, ValueError):
                pass

        return None, False

    @staticmethod
    def _repair_truncated_json(fragment: str) -> str | None:
        """
        Repair a JSON fragment cut off mid-generation: keep everything up
        to the last fully closed nested element, then close the brackets
        that are still open.  Returns None if nothing repairable.
        """
        stack: list[str] = []          # expected closing chars, innermost last
        in_string = False
        escape = False
        cut: tuple[int, list[str]] | None = None
        for i, ch in enumerate(fragment):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]":
                if not stack or stack[-1] != ch:
                    return None  # malformed, not just truncated
                stack.pop()
                if stack:
                    cut = (i + 1, list(stack))
                else:
                    return fragment[: i + 1]  # actually complete
        if cut is None:
            return None
        pos, open_closers = cut
        return fragment[:pos] + "".join(reversed(open_closers))

    # ── internals ────────────────────────────

    def _run_probes(
        self,
        *,
        backend: "LLMBackend",
        system_prompt: str,
        round_num: int,
        public_msg_seq: int,
        player_idx: int,
        player_name: str,
        model_name: str,
        role: str,
        phase: str,
        alive_names: list[str],
        context_messages: list[dict[str, str]],
        is_speaker: bool = False,
    ) -> None:
        players_str = ", ".join(alive_names)

        # Answers from the PREVIOUS step for this player
        last = self._last_answers.get(player_name, {})
        # Accumulate answers within THIS step (for chaining probes)
        current_answers: dict[str, str] = {}

        for probe_seq, probe in enumerate(self.cfg.probes):
            # Skip "own_turn" probes when this player is not the speaker
            if probe.when == "own_turn" and not is_speaker:
                continue

            # Build interpolation context:
            # {prev_X} = answer from THIS step's earlier probe (or fallback to last step)
            # {last_X} = answer from PREVIOUS step
            interp = {}
            for pid in [p.id for p in self.cfg.probes]:
                val = current_answers.get(pid) or last.get(pid, "N/A")
                interp[f"prev_{pid}"] = val
                interp[f"last_{pid}"] = last.get(pid, "N/A")

            try:
                question = probe.question.format(
                    player=player_name,
                    players=players_str,
                    **interp,
                )
            except KeyError:
                # If template references a var we don't have, fill with N/A
                question = probe.question
                for k, v in {**interp, "player": player_name, "players": players_str}.items():
                    question = question.replace("{" + k + "}", str(v))

            # Build a *throw-away* message list:
            # system prompt + game context so far + the probe question.
            # This is NOT added to the player's persistent history.
            probe_messages = [
                {"role": "system", "content": system_prompt},
                *context_messages,
                {"role": "user", "content": f"[PRIVATE PROBE — answer honestly]\n{question}"},
            ]

            tag = f"R{round_num}/msg{public_msg_seq}/p{player_idx}/{probe.id}"
            print(f"    🔍 probe {tag} ...", end="", flush=True)

            t0 = time.monotonic()
            # generate_probe isolates probe generations from game-call RNG
            # on local backends (no-op for stateless API backends).
            answer_raw = backend.generate_probe(probe_messages, max_tokens=probe.max_tokens)
            latency_ms = int((time.monotonic() - t0) * 1000)

            answer_parsed, parse_ok = self._extract_json(answer_raw)

            # Chaining value: splice the PARSED (repaired if needed) JSON,
            # compactly serialized — NOT the raw answer text.  Raw text is
            # a last-resort fallback (parse+repair failed) and is logged.
            if parse_ok:
                chain_value = json.dumps(
                    answer_parsed, ensure_ascii=False, separators=(",", ":")
                )
                chain_used_raw_fallback = False
            else:
                chain_value = answer_raw.strip()
                chain_used_raw_fallback = True

            status = "✓" if parse_ok else "✗ (raw)"
            fallback_note = " [chain: raw-text fallback]" if chain_used_raw_fallback else ""
            print(f" {latency_ms}ms {status}{fallback_note}", flush=True)

            # Store for chaining within this step and for next step
            current_answers[probe.id] = chain_value

            record = ProbeRecord(
                timestamp=time.time(),
                game_id=self.game_id,
                round=round_num,
                public_msg_seq=public_msg_seq,
                player_idx=player_idx,
                player_name=player_name,
                probe_seq=probe_seq,
                probe_id=probe.id,
                question=question,
                answer_raw=answer_raw,
                answer_parsed=answer_parsed,
                answer_parse_ok=parse_ok,
                model=model_name,
                role=role,
                phase=phase,
                latency_ms=latency_ms,
                chain_used_raw_fallback=chain_used_raw_fallback,
            )
            if self.logger:
                self.logger.write(record)

        # Persist this step's answers as "last" for next step
        self._last_answers[player_name] = current_answers


# ────────────────────────────────────────────
#  Helper: build config from YAML dict
# ────────────────────────────────────────────

def load_introspection_config(raw: dict[str, Any]) -> IntrospectionConfig:
    probes = [ProbeConfig(**p) for p in raw.get("probes", [])]
    return IntrospectionConfig(
        enabled=raw.get("enabled", True),
        after_each_message=raw.get("after_each_message", True),
        after_each_round=raw.get("after_each_round", True),
        probes=probes,
        log_file=raw.get("log_file", "logs/introspection.jsonl"),
        feedback_to_context=raw.get("feedback_to_context", False),
    )
