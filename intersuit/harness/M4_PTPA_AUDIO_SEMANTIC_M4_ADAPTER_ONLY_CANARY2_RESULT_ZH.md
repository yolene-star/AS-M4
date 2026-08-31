# M4 音频语义 adapter-only 2-step canary 结果

日期：2026-08-31（Asia/Shanghai）

## 结论

最终状态为 `PASS_ADAPTER_ONLY_CANARY2`。物理 GPU2 上完成两个真实 7B adapter-only optimizer
step，只更新 768→3584 `m4_adapter.weight/bias`；loss、gradient 和更新后参数均 finite，冻结运行模块
SHA 前后完全一致，checkpoint 保存与恢复 SHA 一致。`gpu_20step/gpu_150step/formal_test` 未开放。

## 最终通过配置

- 逻辑设备：`cuda:0`，物理映射：`CUDA_VISIBLE_DEVICES=2`；
- attention：PyTorch SDPA；
- non-reentrant activation checkpointing；
- adapter 参数和 AdamW state：FP32，adapter 输出显式转回 Qwen BF16；
- chunked LM loss：启用，chunk size `256`，loss chunk checkpoint `0`；
- allocator：`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`；
- 视频帧、上下文和音频窗口均未削减。

## 结果锁

- 运行时合同 SHA：`c70616a858a2620728aad8035b2304db81c81a721461d59dd52e441d77509e63`；
- Manifest SHA：`df903721df7c8be528a87a2f2e7605e260eb281bc2a0583e89af0efa4530ad4c`；
- checkpoint：`canary2_v4/checkpoint-step-2.pt`；
- checkpoint SHA：`9b5ff19eef9f9212b284ed8f9a2f4535b8c12b0c0c08729b15126898fa18a0e9`；
- checkpoint 大小：约 32 MiB；
- adapter 训练后/恢复后 SHA：`8136351fd05cd24bddb8125b6f2fa85dadf8de0d19d889ef84349f42e2507697`；
- 运行时间：16.10 秒；
- CUDA peak allocated：`23,691,858,944` bytes（约 22.1 GiB）。

## 两步指标

### Step 1：`adapter_pilot_00`

- video-only / correct / mismatch / silence NLL：`5.600193 / 6.306780 / 6.258410 / 6.239803`；
- ranking hinge：`0.248370`，处于 active；
- weight/bias gradient norm：`19.263985 / 19.549454`；
- clip 前总 gradient norm：`27.445988`；
- 更新后 adapter weight/bias norm：`33.163963 / 0.005987`。

### Step 2：`adapter_pilot_01`

- video-only / correct / mismatch / silence NLL：`1.812595 / 1.823125 / 1.932750 / 2.910086`；
- ranking hinge：`0.090375`，处于 active；
- weight/bias gradient norm：`6.958864 / 5.792556`；
- clip 前总 gradient norm：`9.054254`；
- 更新后 adapter weight/bias norm：`33.164501 / 0.008815`。

不同 step 使用不同 pair group，不能用 step 1 与 step 2 的 NLL 变化证明学习收益。Step 2 的
correct<mismatch 只说明该组在更新前满足排序，不代表整体 30 组通过。

## 失败重试与根因

1. `canary2_v1`：eager attention 在首个有梯度 forward OOM；没有 optimizer step。
2. `canary2_v2`：SDPA + checkpointing 解决 OOM，但 BF16 adapter 经 AdamW 第一步更新后，第二步
   音频 loss 非有限；未保存 checkpoint。
3. `canary2_v3`：FP32 adapter 解决数值问题，step 1 finite，但第二步 backward 距离显存需求约
   80 MiB，仍边界 OOM。
4. `canary2_v4`：启用 256-token chunked LM loss 后完整通过，峰值约 22.1 GiB。

所有失败输出目录和日志均保留；它们没有产生可恢复 checkpoint。GPU 后状态均恢复为 11 MiB、
0%、450W，无残留进程；内核检查无本次 NVRM/Xid/AER/PCIe/hung-task 错误。

## 下一步门禁

在开放 20-step 前必须先完成：

1. 对 2-step checkpoint 做独立离线 schema、optimizer 白名单和 SHA 重读；
2. 注册 20-step 汇总指标，至少覆盖 30 组 correct-vs-mismatch 排序、correct-vs-video-only、
   silence 非退化、finite 比例和 adapter 变化量；
3. 明确 visual-only 控制集仍为 `NOT_EVALUATED`，不得用 `video_only` 条件替代；
4. 20-step 使用新输出目录，不从任何失败 v1--v3 状态恢复。

独立 checkpoint 审计已完成，状态 `PASS_ADAPTER_CANARY_CHECKPOINT_AUDIT`：adapter tensor 为
FP32 且 shape/finite 通过，optimizer parameter/state 数均为 2，运行时合同、manifest、checkpoint
SHA 全部一致，冻结 SHA 与恢复 SHA 均通过。最终完整 CPU 回归为 `695 passed, 2 warnings`，
耗时 122.45 秒。
