# P3 全量冻结特征预计算结果

日期：2026-08-30（Asia/Shanghai）

## 结论

状态为 `PASS_P3_FROZEN_FEATURE_AUDIT`。在物理 GPU2、Conda `M4` 和锁定的 P3 全量合同下，
已生成全部 5,400 条首阶段冻结特征包，并通过独立 `--audit-only` 重读审计。该结果不包含
Gate 反向训练，也不证明下游答案收益。

## 结果

- 输出目录：`intersuit/datasets/VALOR32K_AVQA_V2_AUDIT/p3/frozen_features_v1`；
- 特征包数量：5,400；
- 磁盘占用：约 1.4G；
- quality 监督窗口：43,857；
- relevance 监督窗口：31,896；
- manifest SHA-256：`b04db904ac3f4864438ef87cf47df3904a85bbd240479f4149e333f025554b88`；
- 输出目录使用 `overwrite_existing=false`，未覆盖既有资产。

## 运行证据

- 开始时间：`2026-08-30T11:37:52+08:00`；
- GPU 前后：GPU2 均为 11 MiB / 49140 MiB，450W，0% 利用率；
- 独立审计日志：`harness/artifacts/audio_confidence_p3_full_precompute_v1/logs/independent_audit.log`；
- GPU 证据：`gpu_before_retry.csv`、`gpu_after.csv`；
- 进程退出后未发现预计算或 Gate 训练进程；
- 首次失败仅为相对模型路径错误，未进入编码循环，随后使用绝对路径成功完成。

## 下一步边界

正式特征资产现在具备进入 Gate-only `2-step` canary 的数据前提，但仍需先完成训练入口的资产
路径绑定、manifest SHA/来源 SHA 锁定和参数冻结审计；`20-step`、`150-step`、P4 7B 与
formal test 继续关闭，不能用本结果宣称模型能力提升。
