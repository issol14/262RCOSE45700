#!/usr/bin/env bash
# Start the app on this machine: http://127.0.0.1:8000
# Embedding, reranking and generation run on the endpoints named in .env (see .env.example).
#   scripts/run.sh
#   PORT=8080 LB_LLM_MODEL=furiosa-ai/EXAONE-4.5-33B-FP8 scripts/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
: "${LB_LLM_API_KEY:?set LB_LLM_API_KEY (or put it in .env)}"
: "${LB_EMBED_URL:?set LB_EMBED_URL (or put it in .env)}"
: "${LB_RERANK_URL:?set LB_RERANK_URL (or put it in .env)}"
export LB_LLM_BASE_URL="${LB_LLM_BASE_URL:-https://endpoint.access.furiosa.dev/v1}"
export LB_LLM_MODEL="${LB_LLM_MODEL:-furiosa-ai/gemma-4-31B-it-FP8-block}"
if [ ! -x .venv/bin/python ]; then
  uv venv -q .venv --python 3.12
  uv pip install -q --python .venv/bin/python -r requirements.txt
fi
[ -f ui/dist/index.html ] || (cd ui && npm ci --silent && npm run build --silent)
exec .venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port "${PORT:-8000}"
