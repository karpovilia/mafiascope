"""
Unified LLM backend — OpenRouter (DeepSeek, etc.) and local transformers.

Every backend exposes one function:  generate(messages, max_tokens) → str

The BatchedTransformersBackend collects requests from multiple game threads
and runs them as a single GPU batch for maximum throughput.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import threading
from abc import ABC, abstractmethod
from typing import Any

import requests


# ────────────────────────────────────────────
#  Abstract base
# ────────────────────────────────────────────
class LLMBackend(ABC):
    @abstractmethod
    def generate(self, messages: list[dict[str, str]], max_tokens: int = 400) -> str:
        """Chat-style generation.  messages = [{"role": ..., "content": ...}]"""

    def describe(self) -> dict[str, Any]:
        """Effective, sanitized settings of this backend (never includes api_key).

        Logged into the game trace (setup event) so every trace records which
        model played and under which parameters.
        """
        return dict(getattr(self, "_settings", {}))

    def generate_probe(self, messages: list[dict[str, str]], max_tokens: int = 400) -> str:
        """
        Generation for introspection probes.

        Contract: a probe call must not perturb the state that game calls
        depend on.  For stateless API backends this is trivially true (each
        HTTP request is independent), so the default implementation just
        delegates to generate().  Local backends override this to isolate
        the torch RNG (and, for the batched backend, to keep probe requests
        out of game batches), so that probing does not shift the sampling
        stream of subsequent game generations.
        """
        return self.generate(messages, max_tokens)

    def shutdown(self) -> None:
        """Called when all games are done. Override to clean up."""


def _torch_rng_devices() -> list[int]:
    """All CUDA devices whose RNG we fork around a probe generation.

    With device_map="auto" sampling may run on any of the visible GPUs,
    so we conservatively fork every CUDA device (cheap: state copy only).
    """
    import torch

    if torch.cuda.is_available():
        return list(range(torch.cuda.device_count()))
    return []


def _local_settings(cfg: dict[str, Any], model_id: str, dtype_name: str,
                    temperature: float, top_p: float, do_sample: bool) -> dict[str, Any]:
    """Sanitized effective-settings dict for local transformers backends."""
    import torch
    import transformers

    out = {k: v for k, v in cfg.items() if k != "api_key"}
    out.update({
        "resolved_model_path": model_id,
        "torch_dtype": dtype_name,
        "temperature": temperature,
        "top_p": top_p,
        "do_sample": do_sample,
        "torch_version": torch.__version__,
        "transformers_version": transformers.__version__,
    })
    return out


def _resolve_model_path(model_id: str) -> str:
    """If models/ directory has a local copy, use it. Otherwise return original HF id."""
    local = os.path.join(os.path.dirname(__file__), "models", model_id.replace("/", "_"))
    if os.path.isdir(local) and os.path.isfile(os.path.join(local, "config.json")):
        print(f"[model] using local: {local}")
        return local
    return model_id


# ────────────────────────────────────────────
#  OpenAI-compatible (OpenRouter, DeepSeek)
# ────────────────────────────────────────────
_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}


class _OpenAICompatibleBackend(LLMBackend):
    """
    Optional sampling / reasoning knobs (all forwarded only when set, so
    provider defaults apply otherwise):
        temperature: 0.7
        top_p: 0.9
        reasoning_effort: low|medium|high     # OpenAI reasoning models
        extra_body: {...}   # merged verbatim into the request body — any
                            # provider-specific knob (reasoning mode, etc.)
    """

    def __init__(self, cfg: dict[str, Any], default_url: str):
        raw_key = cfg.get("api_key", "")
        if raw_key.startswith("${") and raw_key.endswith("}"):
            raw_key = os.environ.get(raw_key[2:-1], "")
        self.api_key = raw_key
        self.api_url = cfg.get("api_url", default_url)
        self.model = cfg["model"]
        self.max_tokens = cfg.get("max_tokens", 400)
        self.timeout = cfg.get("timeout", 60)
        self.temperature = cfg.get("temperature")
        self.top_p = cfg.get("top_p")
        self.reasoning_effort = cfg.get("reasoning_effort")
        self.extra_body: dict[str, Any] = cfg.get("extra_body", {})
        self.served_model: str | None = None  # exact version reported by the API
        self._settings = {k: v for k, v in cfg.items() if k != "api_key"}
        self._settings.update({
            "api_url": self.api_url,
            "api_key_set": bool(self.api_key),
            # None = provider default was used
            "temperature": self.temperature,
            "top_p": self.top_p,
        })

    def describe(self) -> dict[str, Any]:
        out = dict(self._settings)
        if self.served_model:
            out["served_model"] = self.served_model
        return out

    def generate(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.top_p is not None:
            body["top_p"] = self.top_p
        if self.reasoning_effort is not None:
            body["reasoning_effort"] = self.reasoning_effort
        body.update(self.extra_body)
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                r = requests.post(self.api_url, headers=headers, json=body, timeout=self.timeout)
                if r.status_code in _RETRY_STATUSES and attempt < 3:
                    time.sleep(min(2 ** (attempt - 1), 4))
                    continue
                r.raise_for_status()
                data = r.json()
                self.served_model = data.get("model", self.served_model)
                return data["choices"][0]["message"]["content"]
            except Exception as exc:
                last_err = exc
                if attempt < 3:
                    time.sleep(min(2 ** (attempt - 1), 4))
        return f"ERROR: {last_err}"


class OpenRouterBackend(_OpenAICompatibleBackend):
    def __init__(self, cfg: dict[str, Any]):
        super().__init__(cfg, default_url="https://openrouter.ai/api/v1/chat/completions")


class OpenAIBackend(_OpenAICompatibleBackend):
    """OpenAI (ChatGPT) or any OpenAI-compatible server via api_url override
    (vLLM, Ollama, llama.cpp, LM Studio, text-generation-inference, ...)."""

    def __init__(self, cfg: dict[str, Any]):
        cfg = {"api_key": "${OPENAI_API_KEY}", **cfg}
        super().__init__(cfg, default_url="https://api.openai.com/v1/chat/completions")


class DeepSeekBackend(_OpenAICompatibleBackend):
    def __init__(self, cfg: dict[str, Any]):
        super().__init__(cfg, default_url="https://api.deepseek.com/chat/completions")


# ────────────────────────────────────────────
#  Bus — external model processes over the MafiaScope bus
# ────────────────────────────────────────────
class BusBackend(LLMBackend):
    """
    Delegates generation to an external worker connected to the bus hub
    (src/bus_server.py).  Any process in any language can serve a model:
    it registers on the hub and long-polls for work (see docs/bus_protocol.md,
    reference client: src/bus_client.py).

    Config:
        type: bus
        model: <routing key>       # matched against models declared by workers
        bus_url: http://127.0.0.1:8765   # or env MAFIA_BUS_URL
        timeout: 300               # seconds to wait for a worker's reply
    """

    def __init__(self, cfg: dict[str, Any]):
        self.model = cfg["model"]
        self.bus_url = cfg.get("bus_url") or os.environ.get(
            "MAFIA_BUS_URL", "http://127.0.0.1:8765")
        self.bus_url = self.bus_url.rstrip("/")
        self.max_tokens = cfg.get("max_tokens", 400)
        self.timeout = cfg.get("timeout", 300)
        self._settings = {**{k: v for k, v in cfg.items() if k != "api_key"},
                          "bus_url": self.bus_url}

    def describe(self) -> dict[str, Any]:
        out = dict(self._settings)
        # best-effort provenance: which workers currently serve this model
        try:
            r = requests.get(f"{self.bus_url}/status", timeout=3)
            workers = r.json().get("workers", {})
            out["bus_workers"] = {
                w: i.get("meta", {}) for w, i in workers.items()
                if "*" in i.get("models", []) or self.model in i.get("models", [])
            }
        except Exception:
            pass
        return out

    def _call(self, messages: list[dict[str, str]], max_tokens: int | None, kind: str) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "kind": kind,
        }
        try:
            r = requests.post(f"{self.bus_url}/generate", json=body, timeout=self.timeout)
            r.raise_for_status()
            return r.json()["text"]
        except Exception as exc:
            return f"ERROR: bus request failed: {exc}"

    def generate(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> str:
        return self._call(messages, max_tokens, "generate")

    def generate_probe(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> str:
        # The kind flag travels to the worker so stateful workers can isolate
        # probe side effects (e.g. fork their RNG) like local backends do.
        return self._call(messages, max_tokens, "probe")


# ────────────────────────────────────────────
#  Local transformers — single request (original)
# ────────────────────────────────────────────
class TransformersBackend(LLMBackend):
    """Load a HuggingFace model directly into the current process."""

    def __init__(self, cfg: dict[str, Any]):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_id: str = _resolve_model_path(cfg["model"])
        self.max_tokens: int = cfg.get("max_tokens", 400)
        self.temperature: float = cfg.get("temperature", 0.7)
        self.top_p: float = cfg.get("top_p", 0.9)
        self.do_sample: bool = cfg.get("do_sample", True)
        # Optional hybrid-reasoning switch (Qwen3 etc.): when set, forwarded to
        # apply_chat_template. None = don't pass the kwarg (default behaviour).
        self.enable_thinking = cfg.get("enable_thinking")

        dtype_name = cfg.get("torch_dtype", "bfloat16")
        dtype_map = {
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "float32": torch.float32,
        }
        torch_dtype = dtype_map.get(dtype_name, torch.bfloat16)

        device = cfg.get("device", "auto")
        self._settings = _local_settings(cfg, self.model_id, dtype_name,
                                         self.temperature, self.top_p, self.do_sample)

        print(f"[transformers] loading {self.model_id}  dtype={dtype_name}  device={device}")
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id, trust_remote_code=True)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            torch_dtype=torch_dtype,
            device_map=device if device == "auto" else None,
            trust_remote_code=True,
        )
        if device != "auto":
            self.model = self.model.to(device)

        self.device = self.model.device
        print(f"[transformers] {self.model_id} ready on {self.device}")

    def generate(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> str:
        import torch

        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
            **({"enable_thinking": self.enable_thinking}
               if self.enable_thinking is not None else {}))
        inputs = self.tokenizer(text, return_tensors="pt").to(self.device)
        with torch.no_grad():
            out = self.model.generate(
                **inputs,
                max_new_tokens=max_tokens or self.max_tokens,
                do_sample=self.do_sample,
                temperature=self.temperature,
                top_p=self.top_p,
            )
        generated = out[0][inputs["input_ids"].shape[1]:]
        return self.tokenizer.decode(generated, skip_special_tokens=True)

    def generate_probe(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> str:
        """
        Probe generation with RNG isolation (P1-4).

        generate() samples from the GLOBAL torch RNG, so an interleaved
        probe call would advance the generator and change every subsequent
        game token relative to an unprobed run.  Here we fork the CPU and
        CUDA RNG states for the duration of the probe and restore them
        afterwards: the game-side sampling stream is untouched by probes.
        """
        import torch

        with torch.random.fork_rng(devices=_torch_rng_devices()):
            return self.generate(messages, max_tokens)


# ────────────────────────────────────────────
#  Batched transformers — collects from N threads, runs one GPU batch
# ────────────────────────────────────────────
class _BatchRequest:
    __slots__ = ("text", "max_tokens", "result", "ready", "is_probe")

    def __init__(self, text: str, max_tokens: int, is_probe: bool = False):
        self.text = text
        self.max_tokens = max_tokens
        self.result: str = ""
        self.ready = threading.Event()
        self.is_probe = is_probe


class BatchedTransformersBackend(LLMBackend):
    """
    Same model as TransformersBackend, but batches requests from
    multiple game threads for GPU efficiency.

    Config:
        batch_size: 16       # max requests per batch
        batch_timeout: 0.5   # seconds to wait before flushing partial batch
    """

    def __init__(self, cfg: dict[str, Any]):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_id: str = _resolve_model_path(cfg["model"])
        self.max_tokens: int = cfg.get("max_tokens", 400)
        self.batch_size: int = cfg.get("batch_size", 16)
        self.batch_timeout: float = cfg.get("batch_timeout", 0.5)
        self.temperature: float = cfg.get("temperature", 0.7)
        self.top_p: float = cfg.get("top_p", 0.9)
        self.do_sample: bool = cfg.get("do_sample", True)
        # Optional hybrid-reasoning switch (Qwen3 etc.); see TransformersBackend.
        self.enable_thinking = cfg.get("enable_thinking")

        dtype_name = cfg.get("torch_dtype", "bfloat16")
        dtype_map = {
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "float32": torch.float32,
        }
        torch_dtype = dtype_map.get(dtype_name, torch.bfloat16)
        device = cfg.get("device", "auto")
        self._settings = _local_settings(cfg, self.model_id, dtype_name,
                                         self.temperature, self.top_p, self.do_sample)

        print(f"[batched-transformers] loading {self.model_id}  "
              f"dtype={dtype_name}  device={device}  batch_size={self.batch_size}")
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id, trust_remote_code=True)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            torch_dtype=torch_dtype,
            device_map=device if device == "auto" else None,
            trust_remote_code=True,
        )
        if device != "auto":
            self.model = self.model.to(device)
        self.device = self.model.device

        # Queue + worker
        self._queue: list[_BatchRequest] = []
        self._lock = threading.Lock()
        self._has_work = threading.Condition(self._lock)
        self._shutdown = False
        self._worker = threading.Thread(target=self._batch_loop, daemon=True)
        self._worker.start()

        # Stats
        self._total_batches = 0
        self._total_requests = 0

        print(f"[batched-transformers] {self.model_id} ready on {self.device}")

    def generate(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> str:
        """Called from game threads — enqueues request and blocks until done."""
        return self._enqueue(messages, max_tokens, is_probe=False)

    def generate_probe(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> str:
        """
        Probe generation with batch + RNG isolation (P1-4).

        Probe requests are tagged and NEVER mixed with game requests in one
        GPU batch (mixing would change padding, batch composition and the
        shared max_new_tokens of game batches).  Probe-only batches are
        additionally run under a forked torch RNG so they do not advance
        the global generator used for game sampling.

        Honest residual (documented, not hidden): probes still occupy
        worker time, so the *scheduling* of game batches (which game
        requests end up padded together) can differ from an unprobed run.
        Per-request sampling RNG and batch contents are isolated, but a
        token-for-token guarantee does NOT hold for this backend.
        """
        return self._enqueue(messages, max_tokens, is_probe=True)

    def _enqueue(self, messages: list[dict[str, str]], max_tokens: int | None, *, is_probe: bool) -> str:
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
            **({"enable_thinking": self.enable_thinking}
               if self.enable_thinking is not None else {}))
        req = _BatchRequest(text, max_tokens or self.max_tokens, is_probe=is_probe)

        with self._has_work:
            self._queue.append(req)
            self._has_work.notify()

        # Block until this request is processed
        req.ready.wait()
        return req.result

    def shutdown(self) -> None:
        with self._has_work:
            self._shutdown = True
            self._has_work.notify()
        self._worker.join(timeout=10)
        print(f"[batched-transformers] shut down. "
              f"{self._total_batches} batches, {self._total_requests} requests, "
              f"avg {self._total_requests / max(1, self._total_batches):.1f} req/batch")

    def _batch_loop(self) -> None:
        """Worker thread: collects requests, runs batched inference."""
        import torch

        while True:
            # Wait for work
            with self._has_work:
                while not self._queue and not self._shutdown:
                    self._has_work.wait(timeout=self.batch_timeout)

                if self._shutdown and not self._queue:
                    return

                # Grab up to batch_size requests of the SAME KIND as the
                # oldest queued request: probe requests are never batched
                # together with game requests (P1-4 isolation — mixing
                # would let probes alter padding / batch composition /
                # max_new_tokens of game generations).
                batch: list[_BatchRequest] = []
                if self._queue:
                    kind = self._queue[0].is_probe
                    keep: list[_BatchRequest] = []
                    for r in self._queue:
                        if r.is_probe == kind and len(batch) < self.batch_size:
                            batch.append(r)
                        else:
                            keep.append(r)
                    self._queue = keep

            if not batch:
                continue

            is_probe_batch = batch[0].is_probe

            # Find the max_tokens for this batch (use the max across requests)
            max_new = max(r.max_tokens for r in batch)

            # Tokenize all texts with left-padding for batched generation
            self.tokenizer.padding_side = "left"
            texts = [r.text for r in batch]
            inputs = self.tokenizer(
                texts,
                return_tensors="pt",
                padding=True,
                truncation=True,
            ).to(self.device)

            input_lengths = [inputs["attention_mask"][i].sum().item() for i in range(len(batch))]

            n = len(batch)
            self._total_batches += 1
            self._total_requests += n
            t0 = time.monotonic()

            try:
                # Probe-only batches run under a forked RNG so probe
                # sampling never advances the global generator that game
                # batches draw from.
                rng_guard = (
                    torch.random.fork_rng(devices=_torch_rng_devices())
                    if is_probe_batch
                    else contextlib.nullcontext()
                )
                with rng_guard, torch.no_grad():
                    outputs = self.model.generate(
                        **inputs,
                        max_new_tokens=max_new,
                        do_sample=self.do_sample,
                        temperature=self.temperature,
                        top_p=self.top_p,
                        pad_token_id=self.tokenizer.pad_token_id,
                    )

                elapsed = time.monotonic() - t0
                kind_tag = "probe" if is_probe_batch else "game"
                print(f"  [batch:{kind_tag}] {n} requests, {max_new} max_tokens, {elapsed:.1f}s")

                # Extract generated text for each request
                for i, req in enumerate(batch):
                    gen_tokens = outputs[i][inputs["input_ids"].shape[1]:]
                    req.result = self.tokenizer.decode(gen_tokens, skip_special_tokens=True)
                    req.ready.set()

            except Exception as exc:
                print(f"  [batch] ERROR: {exc}")
                for req in batch:
                    req.result = f"ERROR: {exc}"
                    req.ready.set()


# ────────────────────────────────────────────
#  Factory
# ────────────────────────────────────────────
_REGISTRY: dict[str, type[LLMBackend]] = {
    "openrouter": OpenRouterBackend,
    "deepseek": DeepSeekBackend,
    "openai": OpenAIBackend,
    "bus": BusBackend,
    "transformers": TransformersBackend,
    "transformers_batched": BatchedTransformersBackend,
}

_INSTANCES: dict[str, LLMBackend] = {}
_INSTANCES_LOCK = threading.Lock()


def get_backend(name: str, backends_cfg: dict[str, Any]) -> LLMBackend:
    """Return (possibly cached) backend instance by config name.

    Serialized: with --parallel, N game threads race here on first use, and
    without the lock each would load its own copy of a local model onto the
    GPU (meta-tensor / OOM chaos). First caller loads, the rest wait and
    share the instance.
    """
    with _INSTANCES_LOCK:
        if name not in _INSTANCES:
            cfg = backends_cfg[name]
            cls = _REGISTRY[cfg["type"]]
            _INSTANCES[name] = cls(cfg)
        return _INSTANCES[name]


def shutdown_backends() -> None:
    """Shut down all backends (flush batches, print stats)."""
    for backend in _INSTANCES.values():
        backend.shutdown()
    _INSTANCES.clear()
