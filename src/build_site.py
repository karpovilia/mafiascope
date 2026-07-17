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

from prepare_viewer import scan_game_dirs, load_bifurcation

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_FILES = ["viewer.html", "dashboard.html", "probe_editor.html", "d3.v7.min.js",
                "fonts/onest-latin.woff2", "fonts/onest-cyrillic.woff2"]

# Showcase bifurcation points whose 20 forks ship as full replayable games.
# All other bifurcation forks are pruned from the static build (their outcomes
# still render in the fan panel — they live in the parent's bifurcation_points
# metadata); this keeps all_games.json within static-host budget.
DEFAULT_SHOWCASE_POINTS = [
    "poli_3845221c_r1_Alex",   # policy gap, 4/20 flips (paper screenshot)
    "poli_0da78714_r2_Dana",   # policy gap, 3/20 flips
    "perc_36594b66_r1_Casey",  # perception gap, lock-in 498/500
]

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
    parser.add_argument("--bifurcation-dir", default="../../mafia2/data/bifurcation",
                        help="Bifurcation experiment data (points.json etc.); "
                             "missing dir degrades softly to no panel")
    parser.add_argument("--bifurcation-full-forks",
                        default=",".join(DEFAULT_SHOWCASE_POINTS),
                        help="Comma-separated point_ids whose forks keep full replay "
                             "data in the build; 'all' keeps every fork, 'none' drops "
                             "them all (fan-panel outcomes stay either way)")
    args = parser.parse_args()

    bif_data = load_bifurcation(args.bifurcation_dir)
    if bif_data:
        print(f"Bifurcation data: "
              f"{sum(len(v) for v in bif_data.values())} points across {len(bif_data)} games")

    games = scan_game_dirs(args.logs_dir, bif_data=bif_data)
    if not games:
        raise SystemExit("No games found — nothing to publish")

    # Prune non-showcase bifurcation forks: a static host should not ship 320
    # full replays; the fan panel reads all outcomes from the parent metadata.
    sel = (args.bifurcation_full_forks or "").strip()
    if bif_data and sel != "all":
        keep_pts = set() if sel in ("", "none") else {s.strip() for s in sel.split(",")}
        all_pts = {p["point_id"] for pts in bif_data.values() for p in pts}
        unknown = keep_pts - all_pts
        if unknown:
            print(f"  ⚠ unknown point_id(s) in --bifurcation-full-forks: {sorted(unknown)}")
        fork_to_pt = {v["fork_game_id"]: p["point_id"]
                      for pts in bif_data.values() for p in pts
                      for v in p["variants"] if v.get("fork_game_id")}
        before = len(games)
        games = [g for g in games
                 if g["game_id"] not in fork_to_pt
                 or fork_to_pt[g["game_id"]] in keep_pts]
        print(f"  bifurcation forks: kept full replays for {sorted(keep_pts & all_pts)}, "
              f"pruned {before - len(games)} fork games")

    if args.max_games:
        roots = sorted([g for g in games if not g.get("forked_from")],
                       key=lambda g: -(g.get("started_at") or 0))[: args.max_games]
        keep = {g["game_id"] for g in roots}
        # bifurcation-point parents always survive the cut — they carry the
        # fan-panel metadata the demo is built around
        keep |= {g["game_id"] for g in games if g.get("bifurcation_points")}
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
