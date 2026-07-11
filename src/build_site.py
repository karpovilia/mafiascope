#!/usr/bin/env python3
"""
Build a self-contained static site for the live demo (GitHub Pages / HF Spaces).

    python build_site.py                     # -> ../site/ with all games
    python build_site.py --max-games 8       # only the 8 newest root games (+their branches)
    python build_site.py -o /path/to/out

The result is fully static: viewer.html + dashboard.html + bundled d3 + JSON.
The fork API is not available on a static host — the viewer degrades
gracefully (the ⑂ Branch dialog explains how to run serve_viewer.py).

Deploy (GitHub Pages):
    git checkout --orphan gh-pages && git rm -rf . && cp -r site/* . \
      && git add -A && git commit -m "deploy" && git push -f origin gh-pages
or simply upload the site/ directory to any static host.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil

from prepare_viewer import scan_game_dirs

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_FILES = ["viewer.html", "dashboard.html", "d3.v7.min.js",
                "fonts/onest-latin.woff2", "fonts/onest-cyrillic.woff2"]

INDEX_HTML = """<!DOCTYPE html>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=viewer.html">
<a href="viewer.html">MafiaScope viewer</a>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build static demo site")
    parser.add_argument("-d", "--logs-dir", default="../logs")
    parser.add_argument("-o", "--output", default="../site")
    parser.add_argument("--max-games", type=int, default=None,
                        help="Keep only the N newest root games (+ their branches)")
    args = parser.parse_args()

    games = scan_game_dirs(args.logs_dir)
    if not games:
        raise SystemExit("No games found — nothing to publish")

    if args.max_games:
        roots = sorted([g for g in games if not g.get("forked_from")],
                       key=lambda g: -(g.get("started_at") or 0))[: args.max_games]
        keep = {g["game_id"] for g in roots}
        games = [g for g in games
                 if g["game_id"] in keep or g.get("forked_from") in keep]

    out = os.path.abspath(os.path.join(HERE, args.output)) \
        if not os.path.isabs(args.output) else args.output
    os.makedirs(out, exist_ok=True)

    for name in STATIC_FILES:
        src = os.path.join(HERE, name)
        if not os.path.isfile(src):
            raise SystemExit(f"Missing {src} — run from src/ after downloading d3 locally")
        dst = os.path.join(out, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    with open(os.path.join(out, "all_games.json"), "w", encoding="utf-8") as f:
        json.dump(games, f, ensure_ascii=False)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX_HTML)

    size_mb = os.path.getsize(os.path.join(out, "all_games.json")) / 1e6
    n_branches = sum(1 for g in games if g.get("forked_from"))
    print(f"Site built at {out}")
    print(f"  {len(games)} games ({n_branches} branches), all_games.json = {size_mb:.1f} MB")
    if size_mb > 30:
        print("  ⚠ consider --max-games to keep the page load fast on a static host")


if __name__ == "__main__":
    main()
