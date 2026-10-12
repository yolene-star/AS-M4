# P3 Gate-only 2-step Canary 结果

日期：2026-08-30（Asia/Shanghai）

## 结论

状态为 `PASS_P3_GATE_ONLY_CANARY2`。基于已独立审计的 5,400 条冻结特征，在物理 GPU2 上完成
2 个 Gate-only 优化步，并保存 checkpoint。该 canary 只验证训练合同的最小有限性和保存链路，
不代表 20/150-step 指标通过，也不代表最终 M4 答案收益。

## 结果

- step 1 loss：`4.251686096191406`；step 2 loss：`4.2064995765686035`；
- router BCE：`4.0181498527526855 -> 4.015706539154053`；
- quality rank：`0.1335901916027069 -> 0.09085387736558914`；
- relevance rank：`0.0999458059668541 -> 0.09993910789489746`；
- sync loss：两步均为 `0.0`；
- feature manifest SHA：`b04db904ac3f4864438ef87cf47df3904a85bbd240479f4149e333f025554b88`；
- checkpoint SHA：`0f0e3b1e982406fc226733641a73e33c0541a38a0a13d77b1bafee1e266d9c5e`。

## 运行证据

- 运行配置：`checkpoints/AS-M4-AudioConfidenceGate-P3-canary2-v1/run_config.json`；
- 训练报告：`checkpoints/AS-M4-AudioConfidenceGate-P3-canary2-v1/report.json`；
- checkpoint：`checkpoints/AS-M4-AudioConfidenceGate-P3-canary2-v1/gate-only-checkpoint.pt`；
- GPU2 训练后：11 MiB / 49140 MiB，450W，0% 利用率；
- 退出后未发现预计算或 Gate 训练进程。

## 边界与下一步

本 canary 使用独立 Gate 模块和冻结特征数据集，尚未执行 20/150-step 任务指标，也未接入完整
7B 端到端 forward。下一步先完成 checkpoint 恢复、参数白名单和 Gate 输出非塌缩的离线审计，再
单独评审 20-step 授权；P4 与 formal test 继续关闭。
