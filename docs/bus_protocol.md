# MafiaScope bus protocol

The bus decouples the game engine from model inference: **any external
process, in any language, on any machine** can play one or more seats by
serving generation requests over plain HTTP. This is how you benchmark
heterogeneous line-ups (e.g. ChatGPT mafia vs. a local Qwen town) or plug in
a completely different agent framework without touching the engine.

```
┌────────────┐   type: bus     ┌─────────────┐   long-poll    ┌──────────────┐
│ MafiaGame  │ ──POST /generate──▶  bus hub  ◀──GET /work──── │ worker        │
│ (main.py)  │ ◀──── text ────── (bus_server) ──POST /result─▶│ (any engine) │
└────────────┘                  └─────────────┘               └──────────────┘
```

- Hub: `python src/bus_server.py` (default `0.0.0.0:8765`; web UI at `/`).
- Reference worker: `src/bus_client.py` (~100 lines; adapters for
  OpenAI-compatible APIs, HuggingFace transformers, and a test echo).
- Engine side: a backend of `type: bus` in the config's `backends:` registry;
  its `model` field is the **routing key** matched against models declared by
  workers.

All bodies are JSON. If the hub was started with `MAFIA_BUS_TOKEN`, every
request must carry `Authorization: Bearer <token>`.

## Worker lifecycle

### 1. `POST /register`

```json
{ "worker": "my-qwen-box", "models": ["Qwen/Qwen2.5-7B-Instruct"], "meta": {"gpu": "V100"} }
```

`models` lists the routing keys this worker serves; `["*"]` serves anything.
Response: `{"worker": "...", "poll": "/work?worker=..."}`. Workers are
considered live while they keep polling (60 s TTL). Re-register on HTTP 409
from `/work` (means the hub restarted).

### 2. `GET /work?worker=<id>&wait=25` (long-poll)

Blocks up to `wait` seconds (max 55). `204 No Content` = nothing yet, poll
again. `200`:

```json
{
  "request_id": "9f2c…",
  "model": "Qwen/Qwen2.5-7B-Instruct",
  "messages": [{"role": "system", "content": "…"}, {"role": "user", "content": "…"}],
  "max_tokens": 400,
  "kind": "generate"
}
```

`messages` is a standard chat transcript — the full private context of one
player at this turn. `kind` is `"generate"` for game moves and `"probe"` for
introspection probes; stateful workers should isolate probe side effects
(e.g. fork their sampler RNG) so probing does not perturb game generations —
see `generate_probe` in `src/llm_backend.py` for the contract.

### 3. `POST /result`

```json
{ "request_id": "9f2c…", "text": "I think Elena is suspicious. I vote for Elena." }
```

`410 Gone` means the engine already timed out waiting — drop the result.

## Engine side (done for you by `type: bus`)

`POST /generate` with `{model, messages, max_tokens, kind, timeout?}` blocks
until a worker replies and returns `{"text": "…"}`. `503` if no live worker
serves `model` or the reply timed out. A config seat then looks like:

```yaml
backends:
  qwen_remote:
    type: bus
    model: Qwen/Qwen2.5-7B-Instruct   # routing key
    bus_url: http://127.0.0.1:8765    # or env MAFIA_BUS_URL
players:
  - role: Villager
    backend: qwen_remote
    personality: { O: 55, C: 70, E: 55, A: 75, N: 25 }
```

## Introspection & control

- `GET /status` — live workers, queue depth, in-flight count.
- `GET /catalog` — backends available for matchups (base config ∪ UI-added ∪
  one auto-generated `bus_*` entry per model served by a live worker).
- `POST /backends` — `{name, backend: {type, model, api_url?, api_key?}}`
  adds an API backend to the catalog (session-scoped).
- `POST /launch` — `{players: [{role, backend}], num_games, parallel?,
  introspection?}`; writes a config to `configs/generated/` and spawns
  `main.py`. `GET /runs` — statuses + log tails. The web UI at `/` is a thin
  client over exactly these endpoints.

## Semantics & caveats

- **At-most-once dispatch, no re-queue**: if a worker takes a request and
  dies, the engine call waits out its timeout and the player's turn degrades
  the same way as an API error (`ERROR: …` reply). Run one worker per model
  or several — the hub routes each request to the first free worker that
  serves the key.
- The hub holds no game state; it is a stateless request router. One hub can
  serve many concurrent games (`--parallel`) and many workers.
- Latency: one long-poll round-trip (≤1 s hand-off) per generation on top of
  inference time.
