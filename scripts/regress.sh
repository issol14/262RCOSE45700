#!/usr/bin/env bash
# Regression on the eval set (128 + 128 negatives) with the current default model, one run.
# Cached passages, gate-skipped items not generated,
# judge = Qwen3-30B-A3B. About 125 generation + 120 judge calls on the RNGD endpoint.
#   scripts/regress.sh [tag]        key from .env; needs data/allganize and results/_passages
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
export LB_LLM_BASE_URL=${LB_LLM_BASE_URL:-https://endpoint.access.furiosa.dev/v1}
T=${1:-reg-$(date +%m%d)}
M=${LB_LLM_MODEL:-furiosa-ai/gemma-4-31B-it-FP8-block}
PY=.venv/bin/python
$PY -m bench.run --set eval --tag "$T" --model "$M" --concurrency 4 --skip-gated
$PY -m bench.run --set eval_neg --tag "$T" --model "$M" --concurrency 4 --skip-gated
LB_JUDGE_MODEL=furiosa-ai/Qwen3-30B-A3B-FP8 LB_JUDGE_CONC=4 $PY -m bench.judge "results/$T/eval_r0.jsonl"
$PY -m bench.metrics "results/$T" eval 0.01 eval_neg
echo "last measured with this model (accuracy 0.805, page_hit 0.836, support 0.908, abstain 1.000, over_abstain 0.070)"
