#!/usr/bin/env python3
"""
Download HuggingFace models for offline use on HPC.

Usage:
    # Download model from config:
    python download_models.py --from-config config_local.yaml

    # Download specific model:
    python download_models.py --model Qwen/Qwen2.5-1.5B-Instruct

    # Custom destination:
    python download_models.py --model Qwen/Qwen2.5-1.5B-Instruct --dest /data/models
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import yaml
from huggingface_hub import snapshot_download


def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def collect_models_from_config(config_path: str) -> list[str]:
    """Extract unique model names from game config backends."""
    cfg = load_yaml(config_path)
    models: set[str] = set()
    for name, backend_cfg in cfg.get("backends", {}).items():
        model = backend_cfg.get("model", "")
        btype = backend_cfg.get("type", "")
        if btype in ("transformers", "transformers_batched") and model:
            models.add(model)
    return sorted(models)


def download_model(repo_id: str, dest_root: str, token: str | None = None) -> None:
    """Download a single model with resume support."""
    local_dir = os.path.join(dest_root, repo_id.replace("/", "_"))
    os.makedirs(local_dir, exist_ok=True)

    print(f"\n[INFO] Downloading {repo_id} -> {local_dir}")

    snapshot_download(
        repo_id=repo_id,
        local_dir=local_dir,
        local_dir_use_symlinks=False,
        resume_download=True,
        token=token,
    )

    config_json = os.path.join(local_dir, "config.json")
    if os.path.isfile(config_json):
        print(f"[OK] {repo_id}: ready for offline use.")
    else:
        print(f"[WARN] {repo_id}: no config.json found — check model.", file=sys.stderr)

    return local_dir


def main():
    parser = argparse.ArgumentParser(description="Download HF models for offline inference")
    parser.add_argument("--model", action="append", default=[],
                        help="HF repo id (can specify multiple)")
    parser.add_argument("--from-config", type=str, default=None,
                        help="Game config YAML to extract model names from")
    parser.add_argument("--dest", type=str, default=None,
                        help="Download destination (default: ./models)")
    parser.add_argument("--token", type=str, default=None,
                        help="HF token (or set HF_TOKEN env var)")
    args = parser.parse_args()

    models: list[str] = list(args.model)
    if args.from_config:
        models.extend(collect_models_from_config(args.from_config))
    models = sorted(set(m.strip() for m in models if m.strip()))

    if not models:
        print("[ERROR] No models specified. Use --model or --from-config.", file=sys.stderr)
        sys.exit(1)

    dest = args.dest or "/home/iakarpov/hf_mirror"
    dest = os.path.expanduser(dest)
    os.makedirs(dest, exist_ok=True)

    token = args.token or os.environ.get("HF_TOKEN")

    print(f"[INFO] Models: {models}")
    print(f"[INFO] Destination: {dest}")

    for repo_id in models:
        try:
            download_model(repo_id, dest, token)
        except Exception as e:
            print(f"[ERROR] Failed to download {repo_id}: {e}", file=sys.stderr)

    print("\n[DONE] All models processed.")


if __name__ == "__main__":
    main()
