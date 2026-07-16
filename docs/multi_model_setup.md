# Running several models across servers and services

This guide shows how to field a Mafia game whose seats are served by
different models living in different places: hosted APIs, a local GPU,
a GPU box elsewhere on the network, an inference server, or an HPC
cluster. It complements [bus_protocol.md](bus_protocol.md) (the wire
protocol) and the Arena UI (`python src/bus_server.py`, then open
`http://localhost:8765/`).

## The one idea

A config has a named `backends:` registry, and every player slot points
into it:

```yaml
backends:
  deepseek: { type: deepseek, model: deepseek-chat, api_key: ${DEEPSEEK_API_KEY} }
  chatgpt:  { type: openai,   model: gpt-4o-mini,   temperature: 0.7 }
  qwen:     { type: bus,      model: Qwen/Qwen2.5-14B-Instruct }

players:
  - { role: Mafia,    backend: deepseek, personality: { O: 70, C: 40, E: 85, A: 30, N: 60 } }
  - { role: Mafia,    backend: chatgpt,  personality: { O: 50, C: 75, E: 40, A: 65, N: 30 } }
  - { role: Doctor,   backend: qwen,     personality: { O: 60, C: 80, E: 50, A: 90, N: 45 } }
  # ... any mix, any count
```

Where each backend actually runs is that backend's business. The engine
machine itself needs no GPU. A worked three-model example ships as
[`configs/config_mixed_trio.yaml`](../configs/config_mixed_trio.yaml).

Every trace records what played: the `setup` and `game_over` events in
`logs/<game_id>/game.jsonl` carry each backend's effective settings
(model id, sampling parameters, token budget, library versions, and for
API backends the exact served model version). API keys are never
written, only an `api_key_set` flag.

## Recipe 1 — hosted APIs (zero infrastructure)

Put keys in `.env` at the repo root (gitignored, loaded automatically):

```
DEEPSEEK_API_KEY=sk-...
OPENAI_API_KEY=sk-...
```

Backend types: `deepseek`, `openai`, `openrouter`. OpenRouter alone
already gives you dozens of distinct models under one key (set
`model: anthropic/claude-3.5-haiku`, `qwen/qwen-2.5-72b-instruct`, ...),
which is the cheapest way to a many-model grid. Optional knobs per
backend: `temperature`, `top_p`, `max_tokens`, `timeout`,
`reasoning_effort`, and `extra_body:` (merged verbatim into the request
for any provider-specific setting).

```bash
cd src && python main.py -c ../configs/config_chatgpt.yaml -n 2
```

## Recipe 2 — an inference server (vLLM, Ollama, LM Studio, TGI)

Any OpenAI-compatible server is just an `openai` backend with an
`api_url`. The server can be on any host you can reach:

```bash
# on the GPU host
vllm serve Qwen/Qwen2.5-7B-Instruct --port 8000       # or: ollama serve
```

```yaml
backends:
  qwen_vllm:
    type: openai
    model: Qwen/Qwen2.5-7B-Instruct       # vLLM: HF id; Ollama: e.g. qwen2.5:7b
    api_url: http://gpu-host:8000/v1/chat/completions  # Ollama: port 11434
    api_key: none                          # local servers accept any token
```

This is the right recipe when the remote host can be reached directly
from the engine machine and already runs a serving stack.

## Recipe 3 — a GPU box as a bus worker (works behind NAT)

When the GPU host has no serving stack, or cannot accept inbound
connections, run a **bus worker** there. The worker makes only
*outbound* HTTP calls to the hub, so NAT and firewalls on the worker
side do not matter.

On the engine machine:

```bash
python src/bus_server.py            # hub + Arena UI on :8765
```

On the GPU box (only `bus_client.py`, `llm_backend.py`, plus
`torch`/`transformers`/`requests` are needed — no repo checkout):

```bash
scp src/bus_client.py src/llm_backend.py gpu-box:~/mafia_bus/
ssh gpu-box
cd ~/mafia_bus
CUDA_VISIBLE_DEVICES=0 python3 bus_client.py \
    --adapter transformers --model Qwen/Qwen2.5-14B-Instruct \
    --bus-url http://ENGINE_HOST:8765
```

If the worker cannot reach the engine host directly (different subnet,
NAT), bridge with a reverse SSH tunnel from the engine machine and point
the worker at localhost:

```bash
# engine machine: expose local hub port 8765 as port 18765 on the GPU box
ssh -f -N -R 18765:localhost:8765 -o ServerAliveInterval=30 gpu-box
# worker on the GPU box then uses:  --bus-url http://127.0.0.1:18765
```

Config on the engine side (the `model` field is the routing key and must
match the worker's `--model`):

```yaml
backends:
  qwen:
    type: bus
    model: Qwen/Qwen2.5-14B-Instruct
    bus_url: http://127.0.0.1:8765
    timeout: 300
```

Check `http://localhost:8765/status` — the worker must be listed before
you launch. Several workers can serve the same model name for
throughput, or different models each; one hub serves many concurrent
games. To secure a hub on an open network, start both sides with
`MAFIA_BUS_TOKEN=<secret>` in the environment.

A worker does not have to be our reference client: anything that speaks
the four HTTP calls of [bus_protocol.md](bus_protocol.md) can play seats
(another agent framework, a different language, a human proxy).

## Recipe 4 — same machine, local GPU

No bus needed: `type: transformers` (one request at a time) or
`type: transformers_batched` (batches concurrent games into single GPU
batches; pair with `python main.py -n 10 --parallel`). Knobs:
`torch_dtype` (use `float16` on pre-Ampere GPUs like V100 — no bf16),
`device`, `batch_size`, `temperature`, `top_p`, `do_sample`.

## Recipe 5 — SLURM / offline HPC

See `src/run_v100_qwen7b.sbatch` + `configs/config_v100_qwen7b.yaml` for
a complete worked pair: offline HF env (`HF_HUB_OFFLINE=1`), a local
model mirror path as `model:`, fp16 on V100, batched backend, N parallel
games in one job. Download models to the mirror beforehand with
`src/download_models.py`. Traces land in `logs/<game_id>/` on the
cluster; `rsync` them back and they are immediately viewable in the
viewer.

## Mixing it all, from the browser

With a hub running, the Arena UI (`http://localhost:8765/`) shows every
backend from the base config, everything added via the form (any
OpenAI-compatible endpoint), and one auto-registered entry per model
served by a live bus worker. Pick a role and a backend per seat, set the
number of games, press Launch: the UI writes the config to
`configs/generated/` and starts the engine; run status and log tails
update live.

## Troubleshooting

- `503 no live worker serves model 'X'` — worker not registered (still
  loading the model), died, or its `--model` does not exactly match the
  backend's `model:` string. Check `/status`.
- Engine hangs on API calls — inspect proxy env vars (`HTTPS_PROXY`);
  unset them for LAN hubs and servers.
- `ERROR: ...` as a player's reply — the engine keeps playing (the turn
  degrades gracefully); fix the backend and re-run the game.
- Bus latency — one long-poll hand-off (≤1 s) per generation on top of
  inference; irrelevant for game pacing.
- Sampling settings for bus seats are owned by the *worker* (its
  adapter flags), not by the engine config; the worker's settings are
  captured in the trace via the hub's worker metadata.
