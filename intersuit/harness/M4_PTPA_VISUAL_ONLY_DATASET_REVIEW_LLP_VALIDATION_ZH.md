# Visual-only 控制测试数据审查报告

日期：2026-09-01（Asia/Shanghai）

## 测试内唯一来源

- 数据集：LLP / AVVP；
- 媒体清单：`intersuit/datasets/LLP_PILOT_MEDIA_V2/final_media_v2/llp_pilot150_media_qualified_effect_locked.jsonl`；
- 标注：`AVVP_eval_visual.csv`；
- split：validation；
- 本地统一窗口清单：`intersuit/harness/artifacts/audio_visual_event_manifests/unified_audio_visual_manifest.jsonl`。

本测试不混入 AVE、AS_M4_STAGE2 或其他数据集。

## 审查结果

| 项目 | 结果 | 说明 |
|---|---|---|
| 来源与 split 统一 | PASS | 只使用 LLP validation |
| 媒体存在性 | PASS | 50/50 validation clip 存在 |
| visual-only 标注 | PARTIAL | 16 个 clip、165 个窗口来自 AVVP_eval_visual.csv |
| 窗口边界 | PASS | 窗口均在 0–10 秒范围内 |
| 视觉问题构造 | NOT_PROVEN | dense event label 尚未形成已审计 M4 generation 问题/答案 |
| adapter 音频输入 | FAIL | 当前媒体为 44.1 kHz AAC，未形成 16 kHz mono waveform |
| 使用权限 | FAIL | `feature_extraction_allowed=false`、`effect_access_allowed=false` |
| 规模 | FAIL FOR FORMAL CONTROL | 仅 16 个 clip，不足预注册的 20–30 组正式控制目标 |

## 结论

状态：`BLOCKED`。

LLP validation 可以保留为 visual-only 候选，但当前不能进入 generation smoke，也不能作为正式 visual suppression 结论。必须先解决使用权限、音频格式、问题构造和样本规模；不允许从其他数据集拼接补齐。
