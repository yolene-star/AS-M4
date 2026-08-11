#!/usr/bin/env bash
set -euo pipefail

project_root="/home/yjm/M4-main"
run_name="${P2_RUN_NAME:-p2_smoke}"
if [[ ! "$run_name" =~ ^p2_smoke(_rerun[0-9]+)?$ ]]; then
  echo "P2_RUN_NAME 必须是 p2_smoke 或 p2_smoke_rerun<数字>" >&2
  exit 2
fi
run_dir="$project_root/intersuit/harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/$run_name"
plan="$run_dir/matrix_plan.jsonl"
p2_gpu="${P2_GPU:?必须显式设置 P2_GPU，例如 P2_GPU=2}"

if [[ ! "$p2_gpu" =~ ^[0-4]$ ]]; then
  echo "P2_GPU 必须是 0..4 的单个物理 GPU 编号" >&2
  exit 2
fi
if [[ "$p2_gpu" == "1" ]]; then
  echo "P2 默认不使用多卡可疑的物理 GPU 1" >&2
  exit 2
fi
if [[ ! -f "$plan" ]]; then
  echo "P2 matrix plan 不存在：$plan" >&2
  exit 2
fi

mkdir -p "$run_dir/logs" "$run_dir/debug" "$run_dir/predictions"
exec > >(tee "$run_dir/logs/launcher.log") 2>&1
start_time="$(date -Is)"
printf '%s\n' "$start_time" > "$run_dir/logs/start_time.txt"
printf '%s\n' "$p2_gpu" > "$run_dir/logs/physical_gpu.txt"
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
export CUDA_VISIBLE_DEVICES="$p2_gpu"
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

for condition in behavior_off gate0 current_micro_top8 fixed_all external_random_top8 external_oracle_top8 oracle_mismatch; do
  line_count="$(wc -l < "$run_dir/predictions/$condition.jsonl")"
  if [[ "$line_count" != "2" ]]; then
    echo "$condition 输出行数异常：$line_count，expected=2" >&2
    exit 3
  fi
done
