# M4 音频语义 adapter-only 小试计划

## 目标

验证 UnAV S1-R temporal projector v2 的 768 维音频语义，是否能通过一个独立可训练的
768→3584 adapter 转换为 M4/Qwen2 可利用的答案信号。

## 严格边界

- 只训练 `scene_audio_encoder.m4_adapter` 的 weight/bias；
- BEATs、v2 semantic projector、旧 audio projector、fusion、Gate、视觉塔和 M4/Qwen2 全部冻结；
- 数据只使用 audio-required 的 `video_only/correct/mismatch/silence` 配对；这里的
  `video_only` 表示同一个 audio-required 问题不提供场景音频，不等于真正的 visual-only 问题；
- 不把普通 M4-IT 训练误当作 correct/mismatch 反事实监督；
- 不访问 formal test，不覆盖任何既有 checkpoint。
- adapter 输出固定从 `llm_input_audio_tokens` 注入，绕开已证实会压平 correct/mismatch
  表示差异的旧视觉残差 fusion projector。

## 训练目标

1. 对正确音频答案 token 使用 LM loss；
2. 约束 correct 的答案相关性高于 mismatch；
3. `gate=0` 必须精确回退；
4. visual-only 音频抑制暂不进入本 pilot loss。只有另建并审计独立 visual-only 问题控制集后，
   才允许启用该项，不能用当前 `video_only` 条件替代。

## 门禁

先 CPU 合同和参数白名单，再按：

```text
2-step -> checkpoint 恢复审计 -> 20-step 指标 -> 150-step（仅在 20-step 全通过后）
```

20-step 至少要求 correct > mismatch、correct 不劣于同题 `video_only` 基线、无输出退化、
冻结参数不变、loss/gradient finite。visual-only 音频影响门禁在独立控制集建成前记为
`NOT_EVALUATED`，不得误报为通过。

## 当前决策

30 组/120 条 manifest、来源审计、CPU loader/loss、teacher-forcing 和真实模型加载封装已完成，
训练仍关闭。下一步接通真实 M4 单条件 forward，完成 optimizer 白名单、冻结运行模块 SHA、
gate0 回退和 checkpoint 恢复审计；全部通过后再评审 2-step GPU 授权。
