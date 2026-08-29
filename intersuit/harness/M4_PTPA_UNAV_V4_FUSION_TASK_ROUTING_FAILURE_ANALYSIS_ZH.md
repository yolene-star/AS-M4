# UnAV v4 新 fusion 无任务收益的路由机制分析

## 结论

绕过旧 fusion projector 后，音频表示不再被旧投影层压平，但固定 `beats_simple_residual` 路径仍缺少问题条件和任务路由。它使用固定 gate，将音频残差均匀加到所有视频帧特征上；只有启用 learned gate v1 或 question-conditioned selector 时，代码才会计算 `question_features`。

## 证据

- bypass 与 lightweight 的 3 条音频任务在 behavior/correct/mismatch 下首 token、token 数和完整预测都完全一致，三者均未新增答对；
- 两种模式产生的预测变化都只发生在视觉任务；
- lightweight 的残差约为 bypass 的 34.5 倍，但音频任务仍完全不变；
- lightweight 下 mismatch 改变预测 2 条，correct 只改变 1 条，说明更强残差主要造成非选择性视觉扰动。

因此，旧 fusion projector 是早期的表示瓶颈，但绕过它之后，新的首要瓶颈变成“固定残差没有根据问题判断是否需要声音，也没有把音频证据路由到音频任务决策”。

## 下一步边界

不得直接放大 gate、扩大评测或训练全模型。下一步只允许建立 query-conditioned CPU 合同：冻结 BEATs、v4 和 M4，验证问题特征能否让音频问题获得更高相关性 gate，同时让视觉问题和错配音频接近零影响。
