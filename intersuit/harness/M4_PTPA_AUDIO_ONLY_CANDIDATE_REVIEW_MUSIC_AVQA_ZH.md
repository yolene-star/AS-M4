# MUSIC-AVQA audio-only 候选审查报告

日期：2026-09-03（Asia/Shanghai）

## 审查范围

- 唯一来源：`MUSIC-AVQA-v2.0`；
- 范围：`intersuit/datasets/AS_M4_STAGE2/reserve_manifest.json` 中对应的 25 条 `task_type=audio` 记录；
- 本地视频与 16 kHz 音频路径均存在。

## 结果

状态：`BLOCKED_FOR_CURRENT_GENERATION_CONTRACT`。

### 通过项

- 测试内来源可以保持单一为 MUSIC-AVQA-v2.0；
- 音频路径为 16 kHz，SHA 字段存在；
- 问题文本明确涉及 sound/instrument；
- 媒体文件均可定位。

### 阻塞项

- 25 条记录只有 11 个 physical media，无法直接形成 20 个独立 pair group；
- 答案只有 `no`（17 条）和 `four`（8 条），不适合当前事件类别答案的 generation 合同；
- 同一视频重复多个问题，且有 `_flip` 派生视频，不能视为独立物理媒体；
- 所有记录 `task_subtype=Counting`，任务本质是音频存在性/计数，不是单一声音事件识别；
- 当前不具备与 `correct/mismatch/video_only/silence` 现有事件标签合同直接兼容的答案结构。

## 决策

MUSIC-AVQA 不作为当前 audio generation smoke 的替代来源。它可以在未来作为独立的“音频存在性/计数”测试，但必须另建问题模板、指标和 pair 构造，不能与 AVE 事件识别结果合并。
