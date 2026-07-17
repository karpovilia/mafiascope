# MafiaScope — interactive game viewer + curated dataset + counterfactual fork API.
#
# Build:  docker build -t mafiascope .          (or podman build)
# Run:    docker run --rm -p 8080:8080 -e DEEPSEEK_API_KEY=sk-... mafiascope
#         then open http://localhost:8080/viewer.html
#
# The DEEPSEEK_API_KEY is only needed for the "⑂ Branch" button (fork API);
# without it the viewer works read-only and fork requests fail gracefully.
#
# Optional: to bake the bifurcation-panel data into the image, stage it as
# bifdata/bifurcation/{points.json, fork_results.jsonl, <point_id>/selected.json}
# in the build context before building. A missing dir degrades softly
# (the viewer simply shows no bifurcation panel).

FROM docker.io/library/python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Pure-Python subset of the project deps — no torch/transformers; the only
# backends usable inside the container are the API ones (DeepSeek/OpenAI).
RUN pip install --no-cache-dir requests httpx pyyaml python-dotenv numpy

WORKDIR /app
COPY configs/ /app/configs/
# Curated games (the committed dataset) + bifurcation fork games.
COPY logs/ /app/logs/
# Optional bifurcation data: "bifdata*" matches nothing in a plain checkout
# and then copies only README.md; with bifdata/ staged it lands as /app/bifurcation.
COPY README.md bifdata* /app/
COPY src/ /app/src/

# Pre-build the viewer data at image-build time so the container starts instantly.
WORKDIR /app/src
RUN python - <<'PY'
import json
from prepare_viewer import scan_game_dirs, load_bifurcation

bif = load_bifurcation("/app/bifurcation")
games = scan_game_dirs("/app/logs", bif_data=bif)
assert games, "no games found under /app/logs"
with open("all_games.json", "w", encoding="utf-8") as f:
    f.write(json.dumps(games, ensure_ascii=False))
with open("viewer_data.json", "w", encoding="utf-8") as f:
    f.write(json.dumps(games[-1], ensure_ascii=False))
n_pts = sum(len(v) for v in bif.values())
print(f"viewer data ready: {len(games)} games, {n_pts} bifurcation points")
PY

EXPOSE 8080
ENTRYPOINT ["python", "serve_viewer.py", "--port", "8080", "--no-open", "--no-rebuild", \
            "-d", "/app/logs", "-c", "/app/configs/config.yaml", \
            "--bifurcation-dir", "/app/bifurcation"]
