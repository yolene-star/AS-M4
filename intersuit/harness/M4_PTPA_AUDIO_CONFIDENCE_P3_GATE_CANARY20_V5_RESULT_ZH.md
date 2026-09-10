# P3 Gate-only 20-step v5 结果

## 结论

Router 修复有效，但整体 20-step 指标尚未完全达标。`router_learning_rate=0.005` 后 Router balanced accuracy 为 `0.828333`，超过 `0.60`；correct/mismatch relevance 排序准确率为 `0.533333`，仍低于 `0.55` 门槛。

## 指标

- Router balanced accuracy：`0.828333`；正类 `0.85`，负类 `0.806667`；
- correct/mismatch relevance ranking：`0.533333`（150 对）；
- Gate 非恒定：通过；范围 `0..0.0958942`；
- checkpoint SHA：`543b3d19322747d53ed5d772e1575f5206f46386859d57d8233a94af894e05ed`；
- feature manifest SHA：`b04db904ac3f4864438ef87cf47df3904a85bbd240479f4149e333f025554b88`。

## 判定

Router 门禁通过，relevance 门禁失败；不得进入 150-step、P4 或 formal test。下一步只针对 relevance
监督权重/配对定义做离线检查和最小修复，保留 v5 checkpoint 作为诊断结果。
