# M4 音频语义 adapter-only 20-step 统一评估计划

## 目的

用同一 30 个 pair group、同一初始化 seed 和同一四条件输入，对未训练初始化与 20-step checkpoint
分别做完整 teacher-forced NLL 评估。禁止用训练 step 中不同样本的 loss 做纵向收益判断。

## 指标

- `correct < mismatch` 的组数、准确率和 `mismatch-correct` 平均/中位 margin；
- `correct < video_only` 的组数、准确率和 `video_only-correct` 平均收益；
- correct 平均 NLL 的训练前后下降；
- silence 与 video-only 的平均绝对 NLL gap；
- 全部 30 组、120 个条件 loss 的 finite 完整率；
- 每组 mismatch margin 相对训练前改善的比例。

这里的 `video_only` 是同一个 audio-required 问题不提供音频，不是真正 visual-only 问题。
visual-only 控制集继续记为 `NOT_EVALUATED`。

## 20-step 预注册停止门槛

20-step 后必须同时满足：

- correct<mismatch 排序准确率至少 `0.60`；
- 排序准确率相对初始化至少提升 `2/30`；
- post `mismatch-correct` 平均 margin 至少 `0.02`，且相对初始化至少提升 `0.02`；
- correct 相对 video-only 的胜率至少提升 `2/30`；
- `video_only-correct` 平均收益至少提升 `0.02`；
- correct 平均 NLL 至少下降 `0.02`；
- post silence/video-only 平均绝对 gap 不超过 `0.5`，且不得比初始化变差；
- 所有 loss、gradient、参数和 checkpoint 均 finite，冻结 SHA 不变。

任一门槛失败即停止，不进入 150-step、P4 或 formal test。

## 当前授权

只开放一次未训练初始化的 30 组 baseline eval；checkpoint eval、20-step、150-step 与 formal test
仍关闭。baseline 完成后锁定结果 SHA，并重新关闭 baseline 授权。
