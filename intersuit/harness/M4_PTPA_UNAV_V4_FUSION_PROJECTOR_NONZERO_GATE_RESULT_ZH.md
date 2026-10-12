# 新 fusion projector 非零 gate 诊断结果

2026 年 8 月 29 日在物理 GPU2、固定 gate=`0.005`、最多 10 条 dev 样本上比较 `bypass` 和 `lightweight`。

两种模式均无空输出、首 token EOS、nonfinite、媒体错配或冻结权重漂移。bypass 的 behavior/correct/mismatch 准确率均为 `3/10`；lightweight 也均为 `3/10`。两种模式的 correct-only 和 mismatch-only 新增答对数均为 `0`。bypass 残差约 `1.44e-5–1.68e-5`，lightweight 残差约 `5.03e-4–5.63e-4`，但 lightweight 中 mismatch 改变预测 2 条、correct 仅改变 1 条，未显示正确音频选择性。

结论：新 fusion 两种候选路径均技术稳定，但当前冻结 v4/BEATs/M4 基座下没有任务收益；停止 full dev、formal test、gate 放大和训练。后续若继续，只能先做只读机制分析或重新设计音频到语言模型的任务监督，不能直接扩大实验。
