# Dynamic Window Selector 主线对齐 Review

日期：2026-07-25（Asia/Shanghai）

最新校正：2026-08-03（Asia/Shanghai）。本次校正按用户再次确认的 selector-first 顺序，
覆盖下文旧的 `S2 audio-path precondition` 和 `S3 BLOCKED` 状态，但保留全部历史失败证据。

## 1. 基准主线

本 review 只使用以下研究顺序作为判定标准：

1. Dynamic Window Selector 先作为低成本、可证伪的诊断实验；
2. 先比较 All、Energy/Eventness Top-K、Temporal 高置信 Top-K、Oracle Top-K、Random Top-K；
3. 不直接接正式联合训练；M4/Qwen2、视觉塔、BEATs、projector 和 fusion 全部冻结；
4. 优先确定性 Top-K，只有 Oracle 有明显收益才训练 question-conditioned selector；
5. learned selector 只训练 selector，不联合解冻新增模块；
6. Oracle 无收益时停止 selector，转向音频语义目标、答案表示和融合位置；
7. Oracle 有收益时推进 question-conditioned selector，并重新设计 Semantic Contrast。

## 2. 对齐结论

结论：**发生了局部主线偏离，但不是代码实现方向错误，而是停止范围被错误扩大。**

符合主线的部分：

- Selector V2 已定义 All、Uniform、Energy、Onset、Eventness、Random、Oracle 和 qualified
  Temporal 对照；
- 已固定 K=8、1 秒窗口、绝对 timestamp、tie-break、Random seed 和 causal contract；
- question-conditioned scorer 已实现为独立最小模块；
- learned 阶段预定只训练 selector，M4/Qwen2、视觉塔、BEATs、projector、fusion 全冻结；
- 未启动联合训练、GPU smoke、formal test 或 selector training。

偏离主线的部分：

- `checkpoint-150` 在 dev760 correct/mismatch 门禁失败后，我们把“该 checkpoint 不具备
  Oracle 下游效用资格”扩大成“整个 selector 路线停止”；
- 随后把音频语义重构计划设为当前主入口，并要求其通过后才重新做 selector Oracle；
- 这颠倒了主线中“先做 selector 定位上界，再根据 Oracle 决策”的顺序。

## 3. 必须区分的两个 Oracle

### 3.1 Oracle 定位上界

问题：在 K=8 的容量约束下，若知道真实事件区间，正确窗口能否被保留？

依赖：timestamp、事件区间、窗口特征；不依赖 M4 fusion 或回答生成。

因此 dev760 音频路径失败不能阻止该实验。当前它仍应执行，只受媒体资格约束。

### 3.2 Oracle 下游效用

问题：把 Oracle 选中的音频窗口送入冻结 M4，是否比 All/Random 改善 caption/QA？

当前 checkpoint 的 correct audio 低于 mismatch/video-only，因此它不是合格音频路径。但按
最新主线，该失败作为 V2 的显式风险，而不是阻止低成本 Oracle 诊断的前置门禁。V2 必须
同时包含 Oracle-mismatch、All、Random、text-only 和输出退化检查；任何一项不通过都停止
selector，并把结论限定为“当前冻结栈中 Oracle 选窗没有可归因下游收益”。

## 4. 修正后的唯一工作流

```text
S0 冻结 contract、数据、指标和历史结果
  ↓
S1 Selector 定位上界（不接 M4、不训练）
  All / Uniform / Random / Energy / Onset / Eventness / Temporal-qualified / Oracle
  ├─ Oracle 无定位 headroom
  │    └─ 停止 selector；再决定是否独立研究音频语义
  └─ Oracle 有定位 headroom
       ↓
S2 Oracle 下游效用（冻结 M4 全部模块、不创建 optimizer）
  Oracle Top-K vs All vs Random vs deterministic Top-K
  ├─ Oracle 无明确下游收益或不高于 Oracle-mismatch
  │    └─ 停止 selector；转向语义目标、答案表示和 fusion
  └─ Oracle 有明确收益
       ↓
S3 确定性 selector 接入
  Energy/Eventness/Temporal Top-K，仍不训练
       ↓
S4 question-conditioned selector-only 训练
  只训练 selector；其他模块 SHA 必须不变
       ↓
S5 causal replay、一次性 test 与新的 Semantic Contrast 计划
```

## 5. 当前真实状态

```text
S0: PASS
S1 metadata/media/full validation: PASS
S1 Oracle−Random: +82.0843pp，95% CI 下界 > 0
S1 Energy/Onset/Eventness: PASS vs Random
S2 LongVALE Oracle pilot: AUTHORIZED_AFTER_PREFLIGHT
S2 confirm: NOT AUTHORIZED，等待 pilot
S3 deterministic integration: NOT AUTHORIZED，等待 S2 confirm
S4 learned selector: NOT AUTHORIZED
S5 causal replay / Semantic Contrast: NOT AUTHORIZED
```

因此不能说“Oracle 已经没有下游收益”，也不能说“selector 已被效果实验否决”。目前已知：

- Oracle 的完整 validation 定位 headroom 已通过并完成双次复现；
- Energy、Onset 和冻结 cached Eventness 均显著高于 Random；
- 当前冻结 M4 音频路径历史 selectivity FAIL，V2 必须保留 mismatch 和 video-only 风险解释；
- LongVALE Oracle 下游 pilot 尚未运行，是当前主线下一动作。

## 6. 数据与执行 blocker

- AVE_HF 没有可靠本地事件边界，不能承担正式 window-level Oracle 定位监督；
- UnAV-100 完整稀疏 validation 已完成 443 samples / 335 physical videos；
- LongVALE 格式和 caption 指标已验证，V2 运行前仍需完成目标 pilot 媒体 preflight；
- Temporal scorer 历史资产资格失败且窗口 schema 不兼容；只有新 scorer 独立资格通过才加入，
  否则在正式矩阵中明确记录 `not_qualified`，不能静默替代。

当前只允许全冻结 V2 Oracle pilot；learned selector、M4 联合训练、教师扩展、Semantic
Contrast 和 test 仍禁止。

## 7. Review 决策

```text
main_plan: docs/DYNAMIC_WINDOW_SELECTOR_DIAGNOSTIC_TRAINING_PLAN_V2_ZH.md
current_stage: S2_LONGVALE_ORACLE_PILOT_PREFLIGHT
immediate_action: RUN_FROZEN_ORACLE_PILOT_AFTER_MEDIA_PREFLIGHT
audio_semantic_plan: PAUSED_UNTIL_ORACLE_FAILS
joint_training: FORBIDDEN
formal_test: FORBIDDEN
```

本次校正不追认任何未运行实验，也不改变 dev760 的冻结失败结论。它只把该失败降为 V2
必须显式控制的风险，并恢复“Oracle 先判、失败后才重构语义”的 selector-first 主线。
