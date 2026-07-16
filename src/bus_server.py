#!/usr/bin/env python3
"""
MafiaScope bus — hub for external model workers + arena web UI.

The hub lets any external process (any language, any engine) play as one or
more players: a worker registers, long-polls for generation requests and
posts results back.  The game engine reaches the hub through the `bus`
backend type in llm_backend.py.  Protocol spec: docs/bus_protocol.md.
Reference worker: src/bus_client.py.

The web UI at http://host:port/ is a matchup builder: assign a model to each
player slot, launch N games, watch run status.

    python src/bus_server.py                 # 0.0.0.0:8765
    python src/bus_server.py --port 9000
    MAFIA_BUS_TOKEN=secret python src/bus_server.py   # require bearer token

No dependencies beyond the standard library + PyYAML (already required).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import yaml

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SRC_DIR)
GENERATED_DIR = os.path.join(ROOT_DIR, "configs", "generated")
RUN_LOG_DIR = os.path.join(ROOT_DIR, "logs", "arena_runs")

WORKER_TTL = 60.0          # seconds since last poll before a worker is stale
DEFAULT_ENGINE_TIMEOUT = 300

# Personality presets cycled over player slots created in the UI
# (same Big Five presets as configs/config.yaml).
PERSONALITY_PRESETS = [
    {"O": 70, "C": 40, "E": 85, "A": 30, "N": 60},
    {"O": 50, "C": 75, "E": 40, "A": 65, "N": 30},
    {"O": 60, "C": 80, "E": 50, "A": 90, "N": 45},
    {"O": 80, "C": 55, "E": 70, "A": 60, "N": 35},
    {"O": 45, "C": 60, "E": 30, "A": 50, "N": 70},
    {"O": 65, "C": 35, "E": 90, "A": 40, "N": 50},
    {"O": 55, "C": 70, "E": 55, "A": 75, "N": 25},
]


class _Request:
    __slots__ = ("id", "model", "payload", "result", "ready", "assigned_at", "created")

    def __init__(self, model: str, payload: dict):
        self.id = uuid.uuid4().hex
        self.model = model
        self.payload = payload
        self.result: str | None = None
        self.ready = threading.Event()
        self.assigned_at: float | None = None
        self.created = time.monotonic()


class Hub:
    """Shared state: workers, request queue, launched runs."""

    def __init__(self, base_config: str, port: int = 8765):
        self.base_config = base_config
        self.port = port
        self.lock = threading.Lock()
        self.work_available = threading.Condition(self.lock)
        self.workers: dict[str, dict] = {}        # worker_id -> {models, meta, last_seen}
        self.queue: list[_Request] = []           # unassigned requests
        self.inflight: dict[str, _Request] = {}   # request_id -> assigned request
        self.runs: dict[str, dict] = {}           # run_id -> {proc, config, log, ...}
        self.extra_backends: dict[str, dict] = {} # backends added via the UI

    # ── workers ──────────────────────────────
    def register(self, worker: str, models: list[str], meta: dict) -> None:
        with self.lock:
            self.workers[worker] = {"models": models, "meta": meta, "last_seen": time.time()}

    def alive_workers(self) -> dict[str, dict]:
        now = time.time()
        return {w: info for w, info in self.workers.items()
                if now - info["last_seen"] < WORKER_TTL}

    def _worker_serves(self, info: dict, model: str) -> bool:
        return "*" in info["models"] or model in info["models"]

    # ── request flow ─────────────────────────
    def submit(self, model: str, payload: dict, timeout: float) -> str | None:
        """Engine side: enqueue and block until a worker replies (or timeout)."""
        with self.lock:
            serving = [w for w, i in self.alive_workers().items()
                       if self._worker_serves(i, model)]
            if not serving:
                return None  # fail fast: no worker for this model
            req = _Request(model, payload)
            self.queue.append(req)
            self.work_available.notify_all()
        if not req.ready.wait(timeout):
            with self.lock:
                if req in self.queue:
                    self.queue.remove(req)
                self.inflight.pop(req.id, None)
            return None
        return req.result

    def take_work(self, worker: str, wait: float) -> _Request | None:
        """Worker side: long-poll for the oldest request this worker can serve."""
        deadline = time.monotonic() + wait
        with self.work_available:
            info = self.workers.get(worker)
            if info is None:
                return None
            while True:
                info["last_seen"] = time.time()
                for req in self.queue:
                    if self._worker_serves(info, req.model):
                        self.queue.remove(req)
                        req.assigned_at = time.monotonic()
                        self.inflight[req.id] = req
                        return req
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.work_available.wait(timeout=min(remaining, 1.0))

    def complete(self, request_id: str, text: str) -> bool:
        with self.lock:
            req = self.inflight.pop(request_id, None)
        if req is None:
            return False
        req.result = text
        req.ready.set()
        return True

    # ── backends / catalog ───────────────────
    def catalog(self) -> dict:
        """Backends available for matchups: base config + UI-added + live bus workers."""
        with open(self.base_config, "r", encoding="utf-8") as f:
            base = yaml.safe_load(f)
        backends = dict(base.get("backends", {}))
        backends.update(self.extra_backends)
        # every model served by a live worker is available as a bus backend
        bus_models: set[str] = set()
        for info in self.alive_workers().values():
            bus_models.update(m for m in info["models"] if m != "*")
        for m in sorted(bus_models):
            name = "bus_" + "".join(c if c.isalnum() else "_" for c in m).lower()
            backends.setdefault(name, {"type": "bus", "model": m})
        return {"backends": backends, "workers": {
            w: {"models": i["models"], "meta": i["meta"]}
            for w, i in self.alive_workers().items()}}

    # ── game runs ────────────────────────────
    def launch(self, spec: dict) -> dict:
        with open(self.base_config, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        catalog = self.catalog()["backends"]
        players = spec["players"]
        used = {p["backend"] for p in players}
        unknown = used - set(catalog)
        if unknown:
            raise ValueError(f"unknown backends: {sorted(unknown)}")

        cfg["backends"] = {name: dict(catalog[name]) for name in used}
        # launched engines run on this machine: point implicit bus backends at us
        for spec_b in cfg["backends"].values():
            if spec_b.get("type") == "bus":
                spec_b.setdefault("bus_url", f"http://127.0.0.1:{self.port}")
        cfg["players"] = [
            {"role": p["role"], "backend": p["backend"],
             "personality": p.get("personality") or PERSONALITY_PRESETS[i % len(PERSONALITY_PRESETS)]}
            for i, p in enumerate(players)
        ]
        if "game" in spec:
            cfg.setdefault("game", {}).update(spec["game"])
        if spec.get("introspection") is False:
            cfg.setdefault("introspection", {})["enabled"] = False

        run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        os.makedirs(GENERATED_DIR, exist_ok=True)
        os.makedirs(RUN_LOG_DIR, exist_ok=True)
        cfg_path = os.path.join(GENERATED_DIR, f"arena_{run_id}.yaml")
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

        n = int(spec.get("num_games", 1))
        cmd = [sys.executable, "main.py", "-c", cfg_path, "-n", str(n)]
        if spec.get("parallel"):
            cmd.append("--parallel")
        log_path = os.path.join(RUN_LOG_DIR, f"{run_id}.log")
        log_f = open(log_path, "w", encoding="utf-8")
        # main.py resolves ../logs and ../configs relative to cwd → run from src/
        proc = subprocess.Popen(cmd, cwd=SRC_DIR, stdout=log_f, stderr=subprocess.STDOUT)
        with self.lock:
            self.runs[run_id] = {
                "proc": proc, "config": cfg_path, "log": log_path,
                "num_games": n, "started": time.time(),
                "players": [{"role": p["role"], "backend": p["backend"]} for p in players],
            }
        return {"run_id": run_id, "config": cfg_path, "log": log_path}

    def runs_status(self) -> list[dict]:
        out = []
        with self.lock:
            items = list(self.runs.items())
        for run_id, r in sorted(items, reverse=True):
            code = r["proc"].poll()
            status = "running" if code is None else ("done" if code == 0 else f"failed ({code})")
            tail = ""
            try:
                with open(r["log"], "r", encoding="utf-8", errors="replace") as f:
                    tail = "".join(f.readlines()[-15:])
            except OSError:
                pass
            out.append({
                "run_id": run_id, "status": status, "num_games": r["num_games"],
                "players": r["players"], "config": os.path.relpath(r["config"], ROOT_DIR),
                "log_tail": tail, "started": r["started"],
            })
        return out


class Handler(BaseHTTPRequestHandler):
    hub: Hub  # set at startup in main()
    token: str = ""
    protocol_version = "HTTP/1.1"

    # ── plumbing ─────────────────────────────
    def _send(self, code: int, obj: dict | list | None = None,
              body: bytes | None = None, ctype: str = "application/json") -> None:
        if body is None:
            body = (json.dumps(obj, ensure_ascii=False) if obj is not None else "").encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _authed(self) -> bool:
        if not self.token:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {self.token}"

    def log_message(self, format, *args):  # quieter: skip long-poll noise
        if "/work" not in (args[0] if args else ""):
            super().log_message(format, *args)

    # ── routes ───────────────────────────────
    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/":
            ui = os.path.join(SRC_DIR, "bus_ui.html")
            with open(ui, "rb") as f:
                self._send(200, body=f.read(), ctype="text/html")
            return
        if not self._authed():
            self._send(401, {"error": "bad token"})
            return
        if url.path == "/status":
            self._send(200, {
                "workers": self.hub.catalog()["workers"],
                "queue": len(self.hub.queue),
                "inflight": len(self.hub.inflight),
            })
        elif url.path == "/catalog":
            self._send(200, self.hub.catalog())
        elif url.path == "/runs":
            self._send(200, self.hub.runs_status())
        elif url.path == "/work":
            q = parse_qs(url.query)
            worker = q.get("worker", [""])[0]
            wait = min(float(q.get("wait", ["25"])[0]), 55.0)
            if worker not in self.hub.workers:
                self._send(409, {"error": "not registered"})
                return
            req = self.hub.take_work(worker, wait)
            if req is None:
                self._send(204)
            else:
                self._send(200, {"request_id": req.id, "model": req.model, **req.payload})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._authed():
            self._send(401, {"error": "bad token"})
            return
        url = urlparse(self.path)
        try:
            body = self._json_body()
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "bad json"})
            return

        if url.path == "/register":
            worker = body.get("worker") or f"worker-{uuid.uuid4().hex[:8]}"
            models = body.get("models") or ["*"]
            self.hub.register(worker, models, body.get("meta", {}))
            print(f"[bus] worker registered: {worker}  models={models}")
            self._send(200, {"worker": worker, "poll": "/work?worker=" + worker})
        elif url.path == "/generate":
            model = body.get("model")
            if not model or not body.get("messages"):
                self._send(400, {"error": "need model + messages"})
                return
            payload = {
                "messages": body["messages"],
                "max_tokens": body.get("max_tokens", 400),
                "kind": body.get("kind", "generate"),
            }
            text = self.hub.submit(model, payload, float(body.get("timeout", DEFAULT_ENGINE_TIMEOUT)))
            if text is None:
                self._send(503, {"error": f"no live worker serves model '{model}' "
                                          f"(or reply timed out)"})
            else:
                self._send(200, {"text": text})
        elif url.path == "/result":
            ok = self.hub.complete(body.get("request_id", ""), body.get("text", ""))
            self._send(200 if ok else 410, {"ok": ok})
        elif url.path == "/backends":
            name, spec = body.get("name"), body.get("backend")
            if not name or not isinstance(spec, dict) or "type" not in spec:
                self._send(400, {"error": "need name + backend{type,...}"})
                return
            with self.hub.lock:
                self.hub.extra_backends[name] = spec
            self._send(200, {"ok": True})
        elif url.path == "/launch":
            try:
                self._send(200, self.hub.launch(body))
            except (ValueError, KeyError, OSError) as exc:
                self._send(400, {"error": str(exc)})
        else:
            self._send(404, {"error": "not found"})


def main() -> None:
    ap = argparse.ArgumentParser(description="MafiaScope bus hub + arena UI")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--base-config", default=os.path.join(ROOT_DIR, "configs", "config.yaml"),
                    help="Config supplying default game/introspection settings and base backends")
    args = ap.parse_args()

    Handler.hub = Hub(args.base_config, port=args.port)
    Handler.token = os.environ.get("MAFIA_BUS_TOKEN", "")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[bus] hub on http://{args.host}:{args.port}  "
          f"(UI: /, protocol: docs/bus_protocol.md, "
          f"auth: {'token' if Handler.token else 'open'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
