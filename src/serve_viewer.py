#!/usr/bin/env python3
"""
Viewer server: scans logs/<game_id>/ folders, builds viewer data, serves HTML
and exposes the counterfactual-replay fork API.

Usage:
    python serve_viewer.py                   # http://localhost:8080
    python serve_viewer.py --port 3000
    python serve_viewer.py --game-id <uuid>  # single game only

Fork API (used by the "⑂ Branch" button in the viewer):
    POST /api/fork {game_id, round, seq, n_replays, backend_override?}
        → {fork_batch_id, total}             # runs in a background thread
    GET  /api/fork/<fork_batch_id>/status
        → {done, failed, total, running, results: [...]}
    Data files (all_games.json / viewer_data.json) are rebuilt automatically
    when a batch finishes; the viewer just re-fetches them.
"""

import argparse
import http.server
import json
import os
import threading
import uuid
import webbrowser
from pathlib import Path

import yaml
from dotenv import load_dotenv

from prepare_viewer import scan_game_dirs, build_viewer_data

# ── shared state ────────────────────────────
FORK_BATCHES: dict[str, dict] = {}
_BATCH_LOCK = threading.Lock()
ARGS = None
CFG = None


def rebuild_data_files(logs_dir: str) -> int:
    """Regenerate all_games.json / viewer_data.json in the served directory."""
    all_games = scan_game_dirs(logs_dir)
    if all_games:
        Path("viewer_data.json").write_text(
            json.dumps(all_games[-1], ensure_ascii=False), encoding="utf-8")
        Path("all_games.json").write_text(
            json.dumps(all_games, ensure_ascii=False), encoding="utf-8")
    return len(all_games)


def start_fork_batch(payload: dict) -> dict:
    """Validate the request and launch the batch in a background thread."""
    from replay import run_fork_batch  # imports game engine lazily

    game_id = payload["game_id"]
    round_num = int(payload["round"])
    msg_seq = int(payload["seq"])
    n = max(1, min(int(payload.get("n_replays", 3)), 50))
    backend_override = payload.get("backend_override") or None

    parent_log_dir = os.path.join(ARGS.logs_dir, game_id)
    state_path = os.path.join(parent_log_dir, "state.jsonl")
    if not os.path.isfile(state_path):
        raise ValueError("Parent game has no state.jsonl — it was recorded "
                         "before snapshots existed; re-run it with game.snapshots: true")

    batch_id = str(uuid.uuid4())
    with _BATCH_LOCK:
        FORK_BATCHES[batch_id] = {
            "fork_batch_id": batch_id, "game_id": game_id,
            "fork_point": [round_num, msg_seq],
            "total": n, "done": 0, "failed": 0, "running": True, "results": [],
        }

    def on_result(result: dict) -> None:
        with _BATCH_LOCK:
            b = FORK_BATCHES[batch_id]
            if "error" in result:
                b["failed"] += 1
            else:
                b["done"] += 1
            b["results"].append({k: result.get(k) for k in
                                 ("game_id", "winner", "rounds", "replica_idx", "error")})

    def worker() -> None:
        try:
            run_fork_batch(
                CFG, parent_log_dir, round_num, msg_seq, n,
                backend_override=backend_override,
                fork_batch_id=batch_id, on_result=on_result,
            )
        finally:
            with _BATCH_LOCK:
                FORK_BATCHES[batch_id]["running"] = False
            rebuild_data_files(ARGS.logs_dir)
            print(f"[fork] batch {batch_id[:8]} finished, data files rebuilt")

    threading.Thread(target=worker, daemon=True).start()
    return {"fork_batch_id": batch_id, "total": n}


class ViewerHandler(http.server.SimpleHTTPRequestHandler):
    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/fork/") and self.path.endswith("/status"):
            batch_id = self.path.split("/")[3]
            with _BATCH_LOCK:
                b = FORK_BATCHES.get(batch_id)
                self._json(dict(b) if b else {"error": "unknown batch"}, 200 if b else 404)
            return
        if self.path == "/api/batches":
            with _BATCH_LOCK:
                self._json(list(FORK_BATCHES.values()))
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/fork":
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                self._json(start_fork_batch(payload))
            except Exception as exc:
                self._json({"error": str(exc)}, 400)
            return
        self.send_error(404)

    def log_message(self, fmt, *args):  # quieter console
        if "/api/" in (args[0] if args else ""):
            return
        super().log_message(fmt, *args)


def main():
    global ARGS, CFG
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("-d", "--logs-dir", default="../logs",
                        help="Root logs directory with per-game folders")
    parser.add_argument("-c", "--config", default="../configs/config.yaml",
                        help="Config used for fork replays")
    parser.add_argument("--game-id", default=None, help="Single game (default: all)")
    parser.add_argument("--no-open", action="store_true")
    ARGS = parser.parse_args()

    with open(ARGS.config, "r", encoding="utf-8") as f:
        CFG = yaml.safe_load(f)
    # forks write next to the other games so the viewer picks them up
    CFG.setdefault("logging", {})["log_dir"] = ARGS.logs_dir

    print("Preparing viewer data...")
    os.chdir(Path(__file__).parent)

    if ARGS.game_id:
        game_dir = os.path.join(ARGS.logs_dir, ARGS.game_id)
        data = build_viewer_data(
            os.path.join(game_dir, "game.jsonl"),
            os.path.join(game_dir, "introspection.jsonl"),
            game_id=ARGS.game_id,
        )
        Path("viewer_data.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
        Path("all_games.json").write_text(
            json.dumps([data], ensure_ascii=False), encoding="utf-8")
        n = 1
    else:
        n = rebuild_data_files(ARGS.logs_dir)

    if not n:
        print("No games found!")
        return
    print(f"Serving {n} game(s)")

    server = http.server.ThreadingHTTPServer(("", ARGS.port), ViewerHandler)
    url = f"http://localhost:{ARGS.port}/viewer.html"
    print(f"Serving at {url}")

    if not ARGS.no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
