# Dynamic Window Selector 主线对齐 Review

日期：2026-07-25（Asia/Shanghai）

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

依赖：一个已证明 correct audio 优于 mismatch 的冻结音频路径。当前 checkpoint 不满足，
所以该实验被阻断；强行运行得到“无收益”也无法归因到 selector。

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
S2 冻结音频路径资格
  correct > mismatch，且 correct 不低于 video-only
  ├─ fail
  │    └─ 进入音频语义重构子计划，只修 blocker；selector 主假设保持 pending
  └─ pass
       ↓
S3 Oracle 下游效用（冻结 M4 全部模块）
  Oracle Top-K vs All vs Random vs deterministic Top-K
  ├─ Oracle 无明确下游收益
  │    └─ 停止 selector；转向语义目标、答案表示和 fusion
  └─ Oracle 有明确收益
       ↓
S4 确定性 selector 接入
  Energy/Eventness/Temporal Top-K，仍不训练
       ↓
S5 question-conditioned selector-only 训练
  只训练 selector；其他模块 SHA 必须不变
       ↓
S6 causal replay、一次性 test 与新的 Semantic Contrast 计划
```

## 5. 当前真实状态

```text
S0: PASS（UnAV 小规模媒体资格已通过；LongVALE 媒体资格仍为 false）
S1 metadata: PASS_ORACLE_HEADROOM
S1 32-video media pilot: PASS_MEDIA_QUALIFICATION / PASS_PILOT_SIGNAL
S1 full validation: NOT RUN；当前主线下一步
S2: FAIL for checkpoint-150
S3: BLOCKED
S4: NOT AUTHORIZED
S5: NOT AUTHORIZED
S6: NOT AUTHORIZED
```

因此不能说“Oracle 已经没有收益”，也不能说“selector 已被效果实验否决”。目前只知道：

- Oracle 的 validation metadata 定位 headroom 已通过并完成双次复现；
- 32-video validation 媒体 pilot 上 Energy/Onset 已显著高于 Random，但该分层 pilot 不能替代完整 validation；
- Eventness 与完整 validation 定位矩阵尚未运行；
- 当前冻结 M4 音频路径不具备解释 Oracle 下游效用的资格。

## 6. 数据与执行 blocker

- AVE_HF 没有可靠本地事件边界，不能承担正式 window-level Oracle 定位监督；
- UnAV-100 有可靠区间、格式和指标，32-video pilot 的 `media_verified=true`，全量 validation 仍未准备；
- LongVALE 格式和 caption 指标已验证，但同样 `media_verified=false`；
- 因此 S1 的实际下一动作是扩展完整 validation 媒体并复现 Energy/Onset/Oracle，而不是训练 selector 或音频 projector。

完整 V1 通过前只允许继续无训练的 validation 定位实验和 Eventness 资产资格审查；
learned selector、M4 联合训练和 test 仍禁止。

## 7. Review 决策

```text
main_plan: docs/DYNAMIC_WINDOW_SELECTOR_DIAGNOSTIC_TRAINING_PLAN_V2_ZH.md
current_stage: S1_LOCALIZATION_UPPER_BOUND
immediate_action: EXPAND_UNAV_VALIDATION_AND_COMPLETE_V1
audio_semantic_plan: CONDITIONAL_BLOCKER_REMEDIATION
joint_training: FORBIDDEN
formal_test: FORBIDDEN
```

本次校正不追认任何未运行实验，也不改变 dev760 的冻结失败结论；它只把失败作用域恢复到
正确层级，并恢复用户给定的 selector-first 主线。
