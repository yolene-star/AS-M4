# M4 音频 adapter 实验候选数据集审查报告

日期：2026-09-01（Asia/Shanghai）

## 审查对象

本次只审查一套来源，不拼接其他数据集：

- 数据集：LLP / AVVP；
- 媒体清单：`intersuit/datasets/LLP_PILOT_MEDIA_V2/final_media_v2/llp_pilot150_media_qualified_effect_locked.jsonl`；
- 音频标注：`intersuit/datasets/LLP/data/AVVP_eval_audio.csv`；
- 视觉标注：`intersuit/datasets/LLP/data/AVVP_eval_visual.csv`；
- split：`intersuit/datasets/LLP/data/AVVP_val_pd.csv`；
- 统一窗口清单：`intersuit/harness/artifacts/audio_visual_event_manifests/unified_audio_visual_manifest.jsonl`。

不使用 AVE、AS-M4_STAGE2、UNAV 或其他来源补齐样本。

## 逐项结果

| 审查项 | 结果 | 证据与结论 |
|---|---|---|
| 来源统一 | PASS | 只使用 LLP_PILOT_MEDIA_V2 validation 与 AVVP LLP 标注 |
| split 隔离 | PASS | 只考虑 validation；不访问 test |
| 物理媒体唯一性 | PASS | validation 50 个 clip、50 个 physical media |
| 媒体存在性 | PASS | 50/50 本地媒体存在 |
| 容器与编解码 | PASS | 全部 H.264/AAC，44.1 kHz；时长约 9.0–10.0 秒 |
| visual-only 语义 | PARTIAL | dense 标注存在 16 个 visual-only clip，但需要逐窗口人工/规则复核，不能仅凭事件名推断 |
| audio-only 语义 | PARTIAL | 36 个 audio-only clip；Speech 占 363/582 个窗口，类别分布高度偏斜 |
| adapter 音频输入契约 | FAIL | 当前清单只有嵌入式 44.1 kHz AAC；未提供已审计 16 kHz mono waveform 和对应 SHA |
| 特征/效果使用权限 | FAIL | locked media report 明确 `feature_extraction_allowed=false`、`effect_access_allowed=false` |
| 规模要求 | FAIL | visual-only 仅 16 个 clip，不满足计划中的 20–30 组独立控制目标 |
| generation-level 可回答性 | NOT_PROVEN | 当前 dense 窗口标签不是 M4 问答答案合同，尚未证明生成答案格式和问题模板有效 |

## 审查结论

状态：`BLOCKED_DATASET_REVIEW`。

LLP_PILOT_MEDIA_V2 在“单一来源、split、媒体完整性”方面合格，但不满足当前 adapter 实验的完整输入与授权要求，因此不能直接生成 review manifest、不能运行 adapter generation smoke，也不能据此开放 `gpu_150step`。

## 下一步条件

只有满足以下条件，才能重新审查：

1. 由数据责任人明确允许该 LLP validation 媒体的特征提取/效果使用；
2. 在同一 LLP validation 来源内生成并锁定 16 kHz mono waveform，记录原始媒体 SHA、转换命令、输出 SHA 和窗口边界；
3. 证明 visual-only 窗口的画面标签确实可见，并冻结不少于 20 个互斥 clip；
4. 将 audio-only 与 visual-only 的问题、答案、窗口和条件构造写入同一 schema；
5. 完成 correct/mismatch/video-only/silence 的组内审计，且 mismatch 不跨 split、不复用同标签源；
6. 重新运行独立 review，全部硬门禁通过后，才可考虑小样本 generation-level 推理。

在此之前，继续保持：`gpu_150step=false`、`formal_test=false`，不下载或拼接其他数据集。
