# AVUT audio-only 候选审查报告

日期：2026-09-03（Asia/Shanghai）

## 审查对象

- 来源数据集：`AVUT`；
- 当前候选范围：`intersuit/datasets/AS_M4_STAGE2/reserve_manifest.json` 中 `source_dataset == AVUT` 且 `task_type == audio` 的 37 条记录；
- 媒体：本地视频与 WAV 均存在。

## 结果

状态：`BLOCKED_AUDIO_ONLY_SEMANTICS`。

### 通过项

- 单一来源字段为 AVUT；
- 当前 reserve split 记录完整；
- 35 个唯一 physical media；
- 视频和音频路径均存在；
- 37 条均为人工来源 `qa_origin=human`。

### 阻塞项

- 答案主要为 `A/B/C/D`，缺少可直接用于事件生成的答案文本；
- 37 条问题中只有 7 条包含明显听觉词（sound/hear/saying/speak/voice/music 等）；
- 其余问题大量询问视频提及内容、人物、时间、食物、地点或字幕知识，不能证明答案依赖音频波形；
- 当前 manifest 未提供统一 choices 到答案文本的冻结字段；
- 因此不能把这 37 条整体作为 audio-only generation 测试，也不能从中随意挑选少量样本冒充统一任务。

## 决策

AVUT 不进入当前 audio generation smoke。若未来继续使用 AVUT，必须先在 AVUT 原始 choices 标注上重新构造一个明确的音频问题子集，并完成视觉泄漏与答案文本审计；本轮不拼接、不改写答案、不运行 GPU。
