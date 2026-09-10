# P3 Gate-only 20-step 指标审计结果

日期：2026-08-30（Asia/Shanghai）

## 结论

状态为 `FAIL_P3_GATE_ONLY_CANARY20_METRICS`。训练执行本身完成，但独立评估未达到合同门槛，
因此禁止进入 150-step、P4 或 formal test。

## 指标

- correct/mismatch relevance 排序准确率：`0.493333`，门槛 `>=0.55`，失败；
- Router balanced accuracy：`0.500000`，门槛 `>=0.60`，失败；
  - 正类准确率：`0.0`；负类准确率：`1.0`；
- Gate 非恒定：通过；
- Gate 范围：`0.0 .. 0.00193513`，整体仍接近关闭。

## 资产一致性

- 评估记录数：5,400；
- feature manifest SHA：`b04db904ac3f4864438ef87cf47df3904a85bbd240479f4149e333f025554b88`；
- checkpoint SHA：`dfc3fed5fa902b2ed176fa1b07733aae35c2db5e516522e66b317f79d50e3b44`；
- feature audit：`PASS_P3_FROZEN_FEATURE_AUDIT`。

## 下一步

先分析 Router 正类全错和排序接近随机的原因，重点检查 batch sampler 的 pair-group 组成、监督
标签与阈值定义，以及 20-step 训练是否只看到了单一类别。修复并通过 CPU 回归后，重新申请
独立 2-step/20-step canary；不修改或覆盖当前失败 checkpoint。
