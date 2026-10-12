# M4 音频语义 adapter-only 真实 7B forward smoke 结果

日期：2026-08-31（Asia/Shanghai）

## 结论

状态为 `PASS_ADAPTER_ONLY_REAL_FORWARD_SMOKE`。在物理 GPU2（逻辑 `cuda:0`）上，真实加载
`M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1`、冻结 BEATs、UnAV S1-R temporal projector v2、
M4/Qwen2 和视觉塔，仅暴露新建 768→3584 `m4_adapter` 的 weight/bias。完成同一组
`video_only/correct/mismatch/silence` teacher-forced forward 和 correct-audio gate0 回退。

本结果只证明真实链路可执行，不证明未训练 adapter 有任务收益，也不授权 2-step 训练。

## 运行与资产锁

- Manifest SHA：`df903721df7c8be528a87a2f2e7605e260eb281bc2a0583e89af0efa4530ad4c`；
- 运行时合同 SHA：`cdae8ac65c0975be3b278c700bfb003aecb1fc41e77a953818b89e170d720982`；
- UnAV v2 SHA：`0bf779dbc1678328d935089f8d41c16e216266b694dd86e51415bfb96f92a610`；
- 初始化 seed：`20260831`；
- 注入位置：`llm_input_audio_tokens`；
- 可训练参数严格为 `model.scene_audio_encoder.m4_adapter.weight/bias`；
- 运行前标准 preflight 为 `pass`，GPU2 为 11 MiB、0%、450W；运行后恢复为 11 MiB、0%，无残留进程；
- 内核检查未发现本次产生的 NVRM、Xid、AER、PCIe Bus Error 或 hung task。

## 指标

| 条件 | teacher-forced NLL |
|---|---:|
| video_only | 5.551567 |
| correct | 6.285980 |
| mismatch | 6.232847 |
| silence | 6.098197 |
| correct audio + gate0 | 5.551567 |

gate0 与 video-only 的绝对差为 `0.0`。冻结 audio encoder（排除 m4_adapter）SHA 和
streaming AV module SHA 前后完全一致；adapter SHA 也因本次无反向而保持一致。

当前随机初始化 adapter 的 correct NLL 高于 mismatch 和 video-only，属于训练前负基线，不能解释为
correct 音频收益。真正的 visual-only 问题控制集尚未构建，状态为 `NOT_EVALUATED`。

## 执行中发现并修复的问题

1. 第一次启动从仓库根目录解析 checkpoint 内部相对视觉塔路径失败；改为固定从 `intersuit/`
   工作目录启动，失败日志保留。
2. 首次进入音频 forward 时发现整体 BF16 转换后 semantic projector 为 BF16，而旧代码强制输入
   FP32，触发 dtype mismatch。现改为按 projector 实际 dtype 计算 residual，再转 FP32 归一化；
   新增 BF16 CPU 回归。

## 验证与下一步

- adapter 相关定向回归：`10 passed, 2 warnings`；
- 完整 CPU 回归：`693 passed, 2 warnings`，耗时 121.20 秒；
- `git diff --check`：通过。

下一步先独立评审 2-step 的真实反向显存和目标系数合同，确认输出目录、训练前后冻结 SHA、
optimizer 白名单、gradient finite/clip、checkpoint 保存恢复均已覆盖后，才允许把 `gpu_2step`
改为 true。`gpu_20step/gpu_150step/formal_test` 继续关闭。
