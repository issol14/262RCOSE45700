#!/usr/bin/env bash
# Answer-generator comparison on the dev set (29) over the RNGD endpoint, cached passages.
# usage: scripts/llmcmp.sh   (key from .env; judge = LB_JUDGE_MODEL)
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
export LB_LLM_BASE_URL=${LB_LLM_BASE_URL:-https://endpoint.access.furiosa.dev/v1}
PY=.venv/bin/python
for m in EXAONE-4.5-33B-FP8 Qwen3-30B-A3B-FP8 gpt-oss-120b gemma-4-31B-it-FP8-block; do
  [ -f results/dev11-$m/dev_r0.jsonl ] || $PY -m bench.run --set dev --tag dev11-$m --model furiosa-ai/$m --concurrency 4
done
echo GENDONE
