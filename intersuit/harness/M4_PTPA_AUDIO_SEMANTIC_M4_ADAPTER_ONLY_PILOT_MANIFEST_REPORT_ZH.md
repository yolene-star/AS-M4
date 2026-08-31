# M4 adapter-only 小试任务监督 Manifest 报告

日期：2026-08-31（Asia/Shanghai）

## 结论

状态为 `PASS_ADAPTER_PILOT_MANIFEST`。从已审计 AS-M4_STAGE2 dev manifest 确定性抽取 30 组
audio-required 问题，构造 `video_only/correct/mismatch/silence` 四条件，共 120 条记录。
该 manifest 只用于后续 adapter-only 任务监督小试，尚未训练。

## 审计

- 输入 manifest SHA：`6d8a29e53532593eb5b66f007e0cc774631f52afa070b82a3e3e1718fd4a2ea7`；
- 输出：`intersuit/harness/artifacts/audio_semantic_m4_adapter_only_pilot_v1/manifest.jsonl`；
- 输出 manifest SHA：`df903721df7c8be528a87a2f2e7605e260eb281bc2a0583e89af0efa4530ad4c`；
- 组数：30；记录数：120；每组四条件各 1 条；
- correct/mismatch 共用同一问题和目标视频，但 mismatch 音频来自不同物理视频且 event label 不同；
- video-only 不提供音频；silence 使用目标视频的音频来源但后续按零波形物化；
- 所有记录 `first_stage_training=true`，未访问 formal test。

## 后续边界

训练前仍需补充 waveform materialization、音频来源 SHA 重读和 adapter-only optimizer 审计。
`gpu_2step/gpu_20step/gpu_150step/formal_test` 继续关闭；当前 manifest 不得与旧 P3 Gate 特征
或失败 checkpoint 混用。
