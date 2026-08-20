#!/usr/bin/env bash
set -u
set -o pipefail

PROJECT_ROOT="/home/yjm/M4-main"
HF_BIN="/home/yjm/miniconda3/envs/package/bin/hf"
REPO_ID="ttgeng233/LongVALE"
REVISION="18889b01886e30c36b0d1c650ac4439ad460ee73"
ARCHIVE_RELATIVE="raw_videos_train/LongVALE_train_7240_part_19.zip"
LOCAL_DIR="$PROJECT_ROOT/intersuit/datasets/LongVALE_V2/official_archives"
TARGET="$LOCAL_DIR/$ARCHIVE_RELATIVE"
EXPECTED_SIZE="5264455669"
EXPECTED_SHA256="1702842ef7daf86b699708bdb8bb44a702486a8b11374778c2a0173a058ca3bf"
LONGVALE_MAX_ATTEMPTS="${LONGVALE_MAX_ATTEMPTS:-200}"
LONGVALE_RETRY_DELAY_SEC="${LONGVALE_RETRY_DELAY_SEC:-30}"

mkdir -p "$(dirname "$TARGET")"
cd "$PROJECT_ROOT"

longvale_attempt=1
while (( longvale_attempt <= LONGVALE_MAX_ATTEMPTS )); do
  echo "$(date -Is) LongVALE part19 下载尝试 ${longvale_attempt}/${LONGVALE_MAX_ATTEMPTS}"
  if HF_ENDPOINT=https://huggingface.co HF_HUB_DISABLE_XET=1 \
    "$HF_BIN" download "$REPO_ID" "$ARCHIVE_RELATIVE" \
      --repo-type dataset \
      --revision "$REVISION" \
      --local-dir "$LOCAL_DIR" \
      --max-workers 1; then
    break
  fi
  echo "$(date -Is) 下载连接中断，将在 ${LONGVALE_RETRY_DELAY_SEC}s 后从 incomplete 文件续传"
  longvale_attempt=$((longvale_attempt + 1))
  sleep "$LONGVALE_RETRY_DELAY_SEC"
done

if (( longvale_attempt > LONGVALE_MAX_ATTEMPTS )); then
  echo "$(date -Is) 达到最大重试次数，下载未完成" >&2
  exit 1
fi

if [[ ! -f "$TARGET" ]]; then
  echo "下载命令成功但目标文件不存在：$TARGET" >&2
  exit 1
fi

longvale_actual_size="$(stat -c %s "$TARGET")"
if [[ "$longvale_actual_size" != "$EXPECTED_SIZE" ]]; then
  echo "LongVALE part19 大小不一致：expected=$EXPECTED_SIZE actual=$longvale_actual_size" >&2
  exit 1
fi

read -r longvale_actual_sha256 _ < <(sha256sum "$TARGET")
if [[ "$longvale_actual_sha256" != "$EXPECTED_SHA256" ]]; then
  echo "LongVALE part19 SHA256 不一致：expected=$EXPECTED_SHA256 actual=$longvale_actual_sha256" >&2
  exit 1
fi

if ! unzip -tqq "$TARGET"; then
  echo "LongVALE part19 ZIP CRC 检查失败" >&2
  exit 1
fi

echo "$(date -Is) LongVALE part19 下载及大小/SHA256/ZIP CRC 检查通过"
