# M4-PTPA-Best-AudioBranch-v1 Dynamic Selector 计划审查报告

日期：2026-08-11（Asia/Shanghai）

审查对象：

```text
intersuit/harness/M4_PTPA_AUDIO_DYNAMIC_SELECTOR_PROJECT_PLAN_ZH.md
```

审查状态：**计划级通过；仅允许下一步进入 P0 结构化实现审查，不允许直接训练或运行正式 GPU 实验。**

```text
status=pass_for_plan_only
implementation_allowed=P0_only_after_user_approval
gpu_full_allowed=false
selector_training_allowed=false
formal_experiment_allowed=false
```

## 1. 审查范围

本次按排查 bug 的方式检查了：

- 目标 checkpoint、代码、BEATs 和旧实验锁是否明确；
- 当前基线是否被误称为 All windows；
- external Top-K 是否可能被模型内部 selector 二次选择；
- Oracle、Random、mismatch 与 learned policy 是否隔离；
- M4/Qwen2、视觉塔、BEATs、projector、fusion、gate 的冻结范围是否可验证；
- 数据划分是否把已查看集合误称为未见 test；
- 统计门禁是否覆盖 All、Random、Current Micro Top-8、mismatch 和 behavior-off；
- selector-only 训练是否可能误用 `streaming_av_module` 粗粒度解冻；
- evaluator、缓存、长任务、失败清理和回退是否有执行约束；
- Oracle 成功与失败两条路线是否都有明确终止条件。

本次没有修改模型代码、配置、checkpoint 或实验产物，没有启动 Python、GPU、推理或训练。

## 2. 已确认的基线事实

1. 目标模型配置为 `micro_window + Top-8`，因此当前 correct 结果是 **Current Micro-window Top-8**，不是 All windows。
2. dev760 当前为 `255/760 = 33.55%`，相对 behavior-off 增加 `0.66pp`，但该集合已被查看，不能用来解锁 selector 训练。
3. PT 当前仍为 `126/200 = 63.00%`，没有二值翻转。
4. PA 当前 Accuracy 增加 `1.00pp`，但 IoU 下降 `0.2327pp`，不能只报 Accuracy 正信号。
5. 旧 LongVALE Oracle pilot 和 AVE official test 都留下了反证；V3 必须保持更严格而不是更宽松的停止门禁。
6. 旧 selector lock 中 `learned_selector_training_allowed=false` 必须保持不变；V3 只能使用独立锁。

## 3. 审查中发现并已在计划书关闭的问题

### R-PLAN-001：Oracle 门禁没有显式比较当前 Micro Top-8

- 严重级别：Blocker
- 原因：假设 H0 包含 Current Micro Top-8，但原统计主比较只列 All、Random、mismatch 和 behavior-off，执行时可能出现“Oracle 没超过当前方案仍被判通过”。
- 修正：主比较新增 `Oracle − Current Micro Top-8`，并要求 METEOR 至少 `+0.01`、95% CI 下界大于 0。
- 状态：已关闭。

### R-PLAN-002：下游回归只有方向描述，没有冻结容忍度

- 严重级别：Major
- 原因：“dev760 不退化”“PT 不超过容忍度”不能直接转成 fail-closed launcher 条件。
- 修正：补充 dev760、PT、PA Accuracy/Precision/IoU 的相对 Current Micro Top-8 非劣阈值，并明确这些阈值不能替代 confirm 正收益。
- 状态：已关闭。

### R-PLAN-003：缺少显式风险登记与阶段影响

- 严重级别：Major
- 原因：未见 confirm 不足、All 静默截断、统计功效不足、评测器解析错误等风险虽散落在正文，但没有统一 owner/gate 语义。
- 修正：新增 R1–R10 风险表，规定触发信号、处置和阻断阶段。
- 状态：已关闭。

### R-PLAN-004：预注册计划缺少变更控制

- 严重级别：Major
- 原因：正式推理后修改 K、seed、split 或阈值会破坏可证伪性。
- 修正：声明单一权威文档路径；所有关键变化必须升版本并重新 Review，正式推理后不得回改 V3 门禁。
- 状态：已关闭。

## 4. 实现前仍未关闭的工程 Blocker

这些不是计划书缺陷，而是 P0 必须解决的真实代码缺口。未关闭前不得进入 P1/P2。

### B-001：缺少一等公民 selection contract 与 policy registry

当前 runner/model 已有分散选择能力，但尚未形成统一 `SelectionRequest/SelectionResult` 合同。若直接继续加条件，容易出现策略名特判、字段漂移和不同 adapter 行为不一致。

验收：所有 policy 返回统一 trace；模型核心不按实验名分支；CPU 合同测试通过。

### B-002：external Top-K 可能被默认 micro selector 二次选择

目标 checkpoint 默认是 `micro_window Top-8`。只在 runner 预选窗口而不显式 bypass 内部 selector，不能证明 external Oracle/Random/All 的真实语义。

验收：true All selected count 等于 full count；external Top-K 的 source index 与输入 trace 一致；内部 selector 调用计数为 0。

### B-003：缺少真正的 true All 路径

当前 correct 基线不是 All。必须单独实现 fixed_all，并对长度、尾窗口和显存超限 fail-closed，禁止静默截断后仍标记 All。

验收：预处理窗口数、模型接收窗口数、trace full/selected count 全部一致。

### B-004：现有可训练别名过粗

`MM_TUNABLE_PARTS=streaming_av_module` 会解冻 selector 之外的新增音频模块，不满足本计划冻结要求。

验收：新增 selector-only allowlist；optimizer 只含精确参数；非 allowlist grad 为 0；训练前后冻结参数 SHA 不变。

### B-005：Oracle 尚未做到 diagnostic-only 强隔离

Oracle 使用标签区间，只能作为上界。若它能出现在普通训练或发布配置中，会形成严重标签泄漏。

验收：无 target、train mode、production mode 均 fail-closed；artifact 明确写 `uses_ground_truth=true`。

### B-006：标准 JSON evaluator 兼容性需修复

既有 PA 评测路径存在使用 Python `eval()` 解析诊断结构的风险，标准 JSON 的 `true/false/null` 可能失败，同时也有安全问题。

验收：改用严格 JSON/schema 解析；样本 ID join、行数和分母一致性测试通过。

## 5. 数据与实验启动前 Blocker

### D-001：LongVALE 独立 confirm 尚未证明可构建

现有 100-video pilot 已被旧实验使用。V3 可以把它当 diagnostic pilot，但不能称为未见 confirm。

验收：提供物理媒体 SHA 交集矩阵；confirm 与训练、旧/新 pilot、历史 selector 资产零非预期交集。无法构建则 V3 不能晋级 learned selector。

### D-002：PA 只能作内部 reserve/confirm，不能包装为 untouched test

PA 200 条已经参与当前版本分析。计划中的 40/160 划分只能防止 V3 内部继续反复调策略，不能消除历史暴露。

验收：划分 manifest 在 selector 推理前冻结；报告用词保持“内部 confirm”。

### D-003：Temporal scorer 当前不合格

旧资产存在诊断失败、schema 不兼容和输入缺失，不能为了凑齐矩阵而使用。

验收：未重新满足 1s/1s schema、零媒体交集、三 seed 和正式 qualification 前，固定报告 `omitted_ineligible_asset`。

## 6. 统计与判定 Review

计划现已形成 fail-closed 决策链：

```text
Oracle 同时超过 All、Random、Current Micro Top-8、mismatch、behavior-off
  -> 才运行 deterministic
  -> deterministic 通过则优先确定性方案
  -> deterministic 失败才允许 selector-only learned 诊断
  -> 独立 confirm 通过后才做 dev760/PT/PA 冻结回归
  -> 任一主门禁失败，停止 selector 路线
```

主统计使用物理视频级 paired cluster bootstrap、固定 seed 和 10,000 次重采样；分类翻转补充 exact McNemar；次要多策略比较使用 Holm 校正。该设计可以回答“是否有足够大的端到端 headroom”，不会把窗口定位指标误当最终收益。

剩余注意点：100-video pilot 的置信区间可能较宽。计划已规定这种情况按 inconclusive/失败处理，不能在 V3 内事后扩样或放宽阈值。

## 7. 代码风格 Review 结论

计划明确禁止以下补丁式实现：

- 根据实验名在模型核心堆 `if/elif`；
- 通过永久修改全局 `model.config` 在条件间切模式；
- 用多组环境变量分别控制相同语义；
- 为 OmniMMI、LongVALE、dev760 复制三套选择逻辑；
- 在 runner 预选后让模型内部再选一次；
- 用粗粒度 `streaming_av_module` 伪装 selector-only；
- 把 Oracle target 字段混入生产 schema。

P0 应完成一轮合同级重构，而不是向旧路径逐点塞条件。该结构在 CPU/集成 Review 通过前，不应启动任何正式 GPU 条件。

## 8. 最终审查结论

计划书在目标、反证、阶段、冻结、统计、风险、回退和停止条件上已达到可执行计划级完整度，允许作为 V3 唯一权威计划保存。

当前结论不是“selector 可以训练”，而是：

1. 计划级 Review 通过；
2. 代码实现仍有 B-001 至 B-006 六个 blocker；
3. 数据启动仍有 D-001 至 D-003 三个 blocker/资格项；
4. 下一步若获用户批准，只允许执行 P0；
5. P0 Review 通过前，GPU full、Oracle pilot 和 selector training 均保持禁止。

人话解释：现在已经把“先证明理想选窗真的有用，再决定要不要训练 selector”写成了带刹车的实验流程。最危险的二次选窗、假 All、误解冻、标签泄漏和拿旧数据冒充未见测试都被列成硬阻断项；当前只是计划准备好了，还没有动模型。
