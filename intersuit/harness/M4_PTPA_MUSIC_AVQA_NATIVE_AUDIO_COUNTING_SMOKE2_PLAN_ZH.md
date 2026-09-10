# MUSIC-AVQA 原生 Audio/Counting smoke2 计划

日期：2026-09-03（Asia/Shanghai）

## 结论

状态：`REPRESENTATION_COMPATIBLE_COUNTING_NOT_VALIDATED_GPU_CLOSED`。

本测试不再沿用 AVE 事件识别问题，而是严格采用 MUSIC-AVQA-v2.0 的原生 `Audio/Counting` 任务。测试内部只使用 MUSIC-AVQA dev，同一个原生问题、同一标注版本和一致的本地媒体资产。

## 两组设计

统一问题：`How many musical instruments were heard throughout the video?`

- 组 0：目标原生答案 `three`，mismatch 音频来源原生答案 `four`；
- 组 1：目标原生答案 `more than ten`，mismatch 音频来源原生答案 `four`；
- 每组包含 `correct/mismatch/video_only/silence`；
- correct 与 mismatch 保持同一目标视频、问题和目标答案，只替换场景音频；
- mismatch 来自 MUSIC-AVQA dev 的不同 physical media，并且该来源对同一问题的原生答案不同。

## 审计结果

- Manifest：2 组、8 条；
- Manifest SHA：`680de30ca2e3b7fedc051858364c7ccbe32d4e5d6f81c84d61bdd66e847eb973`；
- 原始 `train_balance.json` SHA：`34218d7aa2f60ff4aa1ece5f968c1e2a7c8e4c5e9db18ac6273ab7b5a3e9ef00`；
- 原始 MUSIC-AVQA `Audio/Counting` 有效标注：5,492 条；
- 所有目标与 mismatch 问题/答案均可回溯到原始标注；
- 视频和 16 kHz 音频 SHA 通过；
- 与 adapter pilot physical media 无重叠；
- 未访问 formal test。

## 指标与边界

主指标是 MUSIC-AVQA 原生计数答案的归一化命中率。只有 `correct accuracy > mismatch accuracy` 且 silence 与 video-only 精确一致，才允许扩大。

当前只完成 CPU 构建和审计，`gpu_generation_smoke=false`。下一步先实现与计数短答案匹配的 generation runner/输出审计测试，再单独申请 GPU2 2 组/8 条 smoke；不得与 AVE 事件识别结果合并。

## 编码器适配性复核

当前音频链是 `BEATs_iter3_plus_AS2M -> UnAV S1-R temporal projector v2 -> M4 adapter`。BEATs 预训练和 UnAV-100 都包含音乐/乐器声音；UnAV v2 的 100 类中明确覆盖 accordion、acoustic/base guitar、cello、drum kit、flute、piano、saxophone、snare drum、trumpet、violin 和 singing 等类别，validation Recall@1 约为 0.33--0.97（不同类别差异较大）。因此它适合用于“有没有某种乐器声”“是哪种乐器声”的表示诊断。

但 UnAV projector 的目标是 1 秒事件类别与 hard-negative margin，adapter 的 20-step 监督也是单答案相关性；二者都没有训练“跨整段视频去重并统计乐器种类”。所以 MUSIC-AVQA Counting 对当前链路属于组合能力/OOD 诊断，不能直接作为适配性已通过的主验证。正式 GPU 前应先增加同一数据集的原生 Audio/Counting teacher-forced NLL 基线，以及 `video_only` 视觉计数泄漏检查；若 correct 音频在 NLL 上不优于 mismatch，就不运行 generation。
