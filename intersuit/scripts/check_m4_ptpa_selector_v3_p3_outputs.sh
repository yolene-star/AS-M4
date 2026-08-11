#!/usr/bin/env bash
set -euo pipefail

project_root="/home/yjm/M4-main"
run_name="${P3_RUN_NAME:-p3_longvale_canary20}"
if [[ "$run_name" != "p3_longvale_canary20" && "$run_name" != "p3_longvale_frozen100" ]]; then
  echo "P3_RUN_NAME 非法：$run_name" >&2
  exit 2
fi
run_dir="$project_root/intersuit/harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/$run_name"
if [[ "$run_name" == "p3_longvale_canary20" ]]; then
  lock="$run_dir/p3_canary_lock.json"
else
  lock="$run_dir/p3_full_lock.json"
fi

if [[ ! -f "$lock" ]]; then
  echo "P3 canary lock 不存在：$lock" >&2
  exit 2
fi

condition_count=0
expected_count="$(
  conda run -n M4 python -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["sample_count"])' \
    "$lock"
)"
expected_condition_count="$(
  conda run -n M4 python -c \
    'import json,sys; print(len(json.load(open(sys.argv[1]))["conditions"]))' \
    "$lock"
)"
while IFS= read -r condition; do
  [[ -z "$condition" ]] && continue
  condition_count=$((condition_count + 1))
  prediction="$run_dir/predictions/$condition.jsonl"
  if [[ ! -f "$prediction" ]]; then
    echo "$condition prediction 不存在" >&2
    exit 3
  fi
  line_count="$(wc -l < "$prediction")"
  if [[ "$line_count" != "$expected_count" ]]; then
    echo "$condition 输出行数异常：$line_count，expected=$expected_count" >&2
    exit 3
  fi
done < <(
  conda run -n M4 python -c \
    'import json,sys; lock=json.load(open(sys.argv[1])); print("\n".join(lock["conditions"]))' \
    "$lock"
)

if [[ "$condition_count" != "$expected_condition_count" ]]; then
  echo "P3 condition 数量异常：$condition_count，expected=$expected_condition_count" >&2
  exit 4
fi
printf '%s\n' "P3 prediction postcheck pass: $condition_count conditions x $expected_count rows"
