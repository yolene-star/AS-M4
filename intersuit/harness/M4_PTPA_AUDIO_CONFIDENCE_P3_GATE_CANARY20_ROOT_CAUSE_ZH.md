# P3 Gate-only 20-step 失败根因分析

日期：2026-08-30（Asia/Shanghai）

## 根因

`PairGroupBatchSampler` 当前设计为“每个 batch 只包含一个 `pair_group_id`”，用于避免排序损失
跨问题配对。但 P3 数据中每个 `pair_group_id` 的 12 条记录全部属于同一种 modality：

- visual group：12 条记录的 `router_target=0`；
- audio group：12 条记录的 `router_target=1`；
- audio-visual group：12 条记录的 `router_target=1`。

因此 Router 在每个优化 batch 中只看到单一类别。20-step 训练按 group 随机取样，Router 梯度
会在全负 batch 和全正 batch 之间摆动；最终 checkpoint 出现正类准确率 `0`、负类准确率 `1`，
balanced accuracy 正好为 `0.5`。

## 证据

- 全量 manifest：5,400 条，visual/audio/audio-visual 各 1,800 条；
- Router 标签：visual 全为 0，audio 与 audio-visual 全为 1；
- 450 个 pair group；每个 group 12 条记录；
- 以 seed `20260830` 取到的首个 sampler batch：12 条均为 audio-visual，Router 标签全为 1；
- 20-step 独立评估：正类准确率 `0`、负类准确率 `1`、balanced accuracy `0.5`。

## 影响

这不是冻结特征或模型加载问题，也不是 Gate 数值不稳定。相关性排序只有 `0.493333`，还受到
训练步数过少和 batch 采样顺序影响，但首要硬失败是 Router batch 类别不平衡。

## 建议修复

保留 pair-group 约束用于 relevance/quality 排序，同时让 Router 每次更新看到混合正负样本。可选
实现顺序：

1. 新增按多个 pair group 组成 batch 的 sampler，并在排序损失中继续按 group mask；或
2. 将 Router BCE 拆成独立的 class-balanced mini-batch/loss，排序损失仍使用单 group batch；或
3. 使用梯度累积时强制正负 group 成对出现，并按类别归一化 Router loss。

修复后必须先运行 CPU sampler/label 分布测试，再重新执行 2-step 和 20-step；当前失败 checkpoint
不得继续用于 150-step 或 P4。
