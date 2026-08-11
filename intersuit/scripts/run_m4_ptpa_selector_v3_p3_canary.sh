#!/usr/bin/env bash
set -euo pipefail

project_root="/home/yjm/M4-main"
run_dir="$project_root/intersuit/harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/p3_longvale_canary20"
plan="$run_dir/matrix_plan.jsonl"
p3_gpu="${P3_GPU:?必须显式设置 P3_GPU，例如 P3_GPU=2}"

if [[ ! "$p3_gpu" =~ ^[0-4]$ ]]; then
  echo "P3_GPU 必须是 0..4 的单个物理 GPU 编号" >&2
  exit 2
fi
if [[ "$p3_gpu" == "1" ]]; then
  echo "P3 默认不使用多卡可疑的物理 GPU 1" >&2
  exit 2
fi
if [[ ! -f "$plan" || ! -f "$run_dir/p3_canary_lock.json" ]]; then
  echo "P3 canary plan/lock 不完整" >&2
  exit 2
fi

mkdir -p "$run_dir/logs" "$run_dir/debug" "$run_dir/predictions"
exec > >(tee "$run_dir/logs/launcher.log") 2>&1
printf '%s\n' "$(date -Is)" > "$run_dir/logs/start_time.txt"
printf '%s\n' "$p3_gpu" > "$run_dir/logs/physical_gpu.txt"
printf '%s\n' "running" > "$run_dir/logs/status.txt"
git rev-parse HEAD > "$run_dir/logs/code_commit.txt"

finish() {
  exit_code=$?
  printf '%s\n' "$(date -Is)" > "$run_dir/logs/end_time.txt"
  printf '%s\n' "$exit_code" > "$run_dir/logs/exit_code.txt"
  if [[ "$exit_code" == "0" ]]; then
    printf '%s\n' "completed" > "$run_dir/logs/status.txt"
  else
    printf '%s\n' "failed" > "$run_dir/logs/status.txt"
  fi
}
trap finish EXIT

unset AS_M4_FORCE_AUDIO_GATE
export CUDA_VISIBLE_DEVICES="$p3_gpu"
export PYTHONPATH="$project_root/intersuit"
export TOKENIZERS_PARALLELISM=false

cd "$project_root"
conda run --no-capture-output -n M4 python -m intersuit.harness.runners.run_predictions_from_plan \
  --plan "$plan" \
  --backend model \
  --device cuda:0 \
  --max_new_tokens 128 \
  --dump_diagnostics \
  --debug_dir "$run_dir/debug" \
  --resume

while IFS= read -r condition; do
  line_count="$(wc -l < "$run_dir/predictions/$condition.jsonl")"
  if [[ "$line_count" != "20" ]]; then
    echo "$condition 输出行数异常：$line_count，expected=20" >&2
    exit 3
  fi
done < <(
  conda run -n M4 python -c \
    'import json,sys; lock=json.load(open(sys.argv[1])); print("\n".join(lock["conditions"]))' \
    "$run_dir/p3_canary_lock.json"
)
