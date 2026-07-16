#!/usr/bin/env python3
"""
Reference bus worker — serves a model to the MafiaScope bus hub.

Any external engine can replace this file: the protocol is four plain HTTP
calls (docs/bus_protocol.md).  This reference client ships three adapters:

    # any OpenAI-compatible API (ChatGPT, vLLM, Ollama, LM Studio, ...)
    python src/bus_client.py --adapter api --model gpt-4o-mini \
        --api-url https://api.openai.com/v1/chat/completions --api-key-env OPENAI_API_KEY

    # a local HuggingFace transformers model
    python src/bus_client.py --adapter transformers --model Qwen/Qwen2.5-1.5B-Instruct

    # fixed-reply echo (transport smoke test)
    python src/bus_client.py --adapter echo --model echo

The --model value is the routing key: game configs reference it via
`backends: {name: {type: bus, model: <that value>}}`.
"""

from __future__ import annotations

import argparse
import os
import time

import requests


def make_adapter(args: argparse.Namespace):
    """Return fn(messages, max_tokens, kind) -> str."""
    if args.adapter == "echo":
        def echo(messages, max_tokens, kind):
            return f"(echo) I heard {len(messages)} messages. I vote for nobody."
        return echo

    if args.adapter == "api":
        api_key = os.environ.get(args.api_key_env, "")
        url = args.api_url

        def api(messages, max_tokens, kind):
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": args.model, "messages": messages, "max_tokens": max_tokens},
                timeout=args.timeout,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        return api

    if args.adapter == "transformers":
        # Reuse the engine's backend so probe RNG isolation matches local runs.
        from llm_backend import TransformersBackend
        backend = TransformersBackend({"model": args.model, "device": args.device,
                                       "torch_dtype": args.torch_dtype})

        def hf(messages, max_tokens, kind):
            if kind == "probe":
                return backend.generate_probe(messages, max_tokens)
            return backend.generate(messages, max_tokens)
        return hf

    raise SystemExit(f"unknown adapter: {args.adapter}")


def main() -> None:
    ap = argparse.ArgumentParser(description="MafiaScope bus worker")
    ap.add_argument("--bus-url", default=os.environ.get("MAFIA_BUS_URL", "http://127.0.0.1:8765"))
    ap.add_argument("--worker", default=None, help="worker id (default: adapter-model)")
    ap.add_argument("--adapter", choices=["api", "transformers", "echo"], default="echo")
    ap.add_argument("--model", required=True,
                    help="model id; also the bus routing key game configs point at")
    ap.add_argument("--api-url", default="https://api.openai.com/v1/chat/completions")
    ap.add_argument("--api-key-env", default="OPENAI_API_KEY")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--torch-dtype", default="bfloat16")
    ap.add_argument("--timeout", type=float, default=120)
    args = ap.parse_args()

    bus = args.bus_url.rstrip("/")
    token = os.environ.get("MAFIA_BUS_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    worker = args.worker or f"{args.adapter}-{args.model.split('/')[-1]}"
    generate = make_adapter(args)

    def register() -> None:
        requests.post(f"{bus}/register", headers=headers, json={
            "worker": worker,
            "models": [args.model],
            "meta": {"adapter": args.adapter},
        }, timeout=10).raise_for_status()

    register()
    print(f"[worker {worker}] serving '{args.model}' on {bus}")

    while True:
        try:
            r = requests.get(f"{bus}/work", headers=headers,
                             params={"worker": worker, "wait": 25}, timeout=40)
            if r.status_code == 409:   # hub restarted and forgot us
                register()
                continue
            if r.status_code != 200:   # 204 = no work this poll
                continue
            job = r.json()
            t0 = time.monotonic()
            try:
                text = generate(job["messages"], job.get("max_tokens", 400),
                                job.get("kind", "generate"))
            except Exception as exc:
                text = f"ERROR: worker adapter failed: {exc}"
            requests.post(f"{bus}/result", headers=headers, json={
                "request_id": job["request_id"], "text": text,
            }, timeout=10)
            print(f"[worker {worker}] {job.get('kind','generate')} "
                  f"({len(job['messages'])} msgs) in {time.monotonic()-t0:.1f}s")
        except KeyboardInterrupt:
            return
        except requests.RequestException as exc:
            print(f"[worker {worker}] bus unreachable ({exc}); retrying in 3s")
            time.sleep(3)


if __name__ == "__main__":
    main()
