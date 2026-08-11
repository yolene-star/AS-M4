#!/usr/bin/env bash
set -euo pipefail

project_root="/home/yjm/M4-main"
run_dir="$project_root/intersuit/harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/p3_longvale_canary20"
lock="$run_dir/p3_canary_lock.json"

if [[ ! -f "$lock" ]]; then
  echo "P3 canary lock 不存在：$lock" >&2
  exit 2
fi

condition_count=0
while IFS= read -r condition; do
  [[ -z "$condition" ]] && continue
  condition_count=$((condition_count + 1))
  prediction="$run_dir/predictions/$condition.jsonl"
  if [[ ! -f "$prediction" ]]; then
    echo "$condition prediction 不存在" >&2
    exit 3
  fi
  line_count="$(wc -l < "$prediction")"
  if [[ "$line_count" != "20" ]]; then
    echo "$condition 输出行数异常：$line_count，expected=20" >&2
    exit 3
  fi
done < <(
  conda run -n M4 python -c \
    'import json,sys; lock=json.load(open(sys.argv[1])); print("\n".join(lock["conditions"]))' \
    "$lock"
)

if [[ "$condition_count" != "14" ]]; then
  echo "P3 condition 数量异常：$condition_count，expected=14" >&2
  exit 4
fi
printf '%s\n' "P3 prediction postcheck pass: 14 conditions x 20 rows"
