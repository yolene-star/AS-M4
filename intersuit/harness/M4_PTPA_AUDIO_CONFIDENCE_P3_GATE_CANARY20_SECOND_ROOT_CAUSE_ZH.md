# P3 Gate-only 20-step 第二根因分析

## 现象

修复多 group 混合 batch 后，真实 batch 已同时包含正负 Router 标签，但第二次 20-step 评估仍为：

- Router balanced accuracy：`0.5`（正类 0、负类 1）；
- correct/mismatch 排序准确率：`0.513333`；
- Gate 非恒定，范围 `0..0.002564`。

## 根因

`QuestionAudioRouter` 的输出为：

```text
sigmoid(network_output + router_logit_bias)
```

其中 `network` 最后一层权重和偏置均零初始化，`router_logit_bias=-4`，所以初始正负样本输出都约
`sigmoid(-4)=0.018`。训练学习率为 `1e-4`，20 步中最后层 bias 的理论移动量只有千分之几，
远不足以从 logit `-4` 移到分类阈值 `0`。因此即使 batch 标签已平衡，阈值评估仍会把所有样本判为
负类；这解释了正类准确率 0、负类准确率 1。

## 影响

第一根因（单类 batch）已修复；当前失败是初始化/优化步长与 20-step 门禁不匹配。relevance 排序
从 `0.493333` 提升到 `0.513333`，说明混合采样和时间 padding 已生效，但训练步数太少且 Gate 初始
关闭使排序信号仍弱。

## 最小修复建议

优先采用可审计的 Router 专用初始化/学习率方案：

1. 保留最终音频 Gate 的负 bias；将 Router 最后一层 bias 初始化为数据先验 logit（正类比例 2/3，
   约 `+0.693`），或对 Router 使用独立较高学习率；
2. 继续使用混合 pair-group batch，并在评估中同时报告概率分布和固定阈值结果；
3. 修复后先做 CPU 初始化/梯度方向测试，再重跑 2-step/20-step；不延长训练步数掩盖初始化问题。

当前 v2/v3 失败 checkpoint 不得用于 150-step 或 P4。
