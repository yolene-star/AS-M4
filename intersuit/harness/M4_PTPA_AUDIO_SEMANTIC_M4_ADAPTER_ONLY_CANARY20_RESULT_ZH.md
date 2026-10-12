# M4 音频语义 adapter-only 20-step 结果

日期：2026-08-31（Asia/Shanghai）

## 结论

最终状态为 `PASS_ADAPTER_CANARY20_METRICS`。objective-v2 20-step 在物理 GPU2 完成，30 组
训练前后统一 teacher-forced NLL 评估的十项预注册门禁全部通过。训练、评估与 checkpoint
独立审计均通过，但 150-step、P4 和 formal test 继续关闭。

本结果证明小规模任务桥接在当前 pilot 上产生了可测的 correct-audio 与 video-only 收益；它仍不等于
最终生成答案准确率或跨数据集泛化结论。

## 训练合同

- 训练范围：仅 FP32 `m4_adapter.weight/bias`；
- 冻结：M4/Qwen2、视觉塔、BEATs、UnAV v2 projector、streaming AV module；
- attention/内存：SDPA、non-reentrant checkpointing、256-token chunked LM loss；
- objective：correct LM、correct-vs-mismatch margin、correct-vs-video benefit hinge；
- 物理全零 silence：在 audio-token 注入前确定性旁路，精确退化为 video-only；
- 训练组：前 20 个 pair group；完整评估：30 组。

## 资产锁

- Manifest SHA：`df903721df7c8be528a87a2f2e7605e260eb281bc2a0583e89af0efa4530ad4c`；
- 20-step checkpoint SHA：`9169a61c48308e13f08f81435b903596f68104b797eba43896a1f2210eb8cca5`；
- 20-step 运行时合同 SHA：`8bcc46b33737ed6249efe625f12032c20a91358cd954cb0b056b33281f27de27`；
- silence-bypass baseline summary SHA：`a4eb583874b33aa9e2b630e1715e3484566691546821105c6024d24d79370625`；
- post summary SHA：`02f40b61926fd4fe85ace9f6d0b612faa432a2d968c362bdf1c0ba6c0f791e99`；
- 最终 gate SHA：`5504173a198728fb8fd92dc08c8afd2ef633ef10774a19318f1b07ed7c2d5da8`。

## 30 组统一指标

| 指标 | 初始化 | 20-step | 变化 |
|---|---:|---:|---:|
| correct<mismatch | 13/30（0.4333） | 18/30（0.6000） | +0.1667 |
| mismatch−correct 平均 margin | -0.00980 | +0.09049 | +0.10029 |
| correct<video-only | 9/30（0.3000） | 22/30（0.7333） | +0.4333 |
| video-only−correct 平均收益 | -0.26514 | +0.14219 | +0.40733 |
| correct 平均 NLL | 3.24113 | 2.83380 | -0.40733 |
| silence/video-only 平均绝对 gap | 0.0 | 0.0 | 0.0 |

所有 120 个条件 loss 均 finite；每组 mismatch margin 改善比例为 `0.6667`。

## silence 安全修复

第一版 post 评估虽然 8 项任务指标通过，但 silence gap 从 `0.5332` 恶化为 `0.6052`，因此状态为
`FAIL_ADAPTER_CANARY20_METRICS`。离线拆分显示恶化集中在训练过的前 20 组，说明 NLL anchor
不足以提供结构性安全保证。

随后加入可配置的物理全零 silence bypass：当原始 scene-audio window 最大绝对幅值不超过 `1e-6`
时不插入 audio token。重新运行 baseline/post 后，30/30 组 silence NLL 与 video-only 逐组精确一致；
任务相关三条件指标完全复现，最终十项门禁全部通过。该修复不改变非零 correct/mismatch 音频路径。

## 泛化边界与下一步

- 训练过的前 20 组排序率从 `0.45` 提升到 `0.70`；
- holdout10 排序率保持 `0.40`，平均 margin 仅小幅改善约 `0.0092`；
- holdout10 correct 平均 NLL 改善约 `0.0807`，但不足以证明稳定排序泛化；
- 真正 visual-only 问题控制仍为 `NOT_EVALUATED`。

因此下一步不是直接运行 150-step，而是先构建独立 visual-only 控制集，并补 generation-level
小样本 correct/mismatch/video-only 对照。两项均通过后，才评审 150-step 或扩大数据。

最终完整 CPU 回归为 `701 passed, 2 warnings`，耗时 122.48 秒；`git diff --check` 通过。
