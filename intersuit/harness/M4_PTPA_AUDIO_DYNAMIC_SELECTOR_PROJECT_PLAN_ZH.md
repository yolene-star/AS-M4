# M4-PTPA-Best-AudioBranch-v1 Dynamic Window Selector 诊断实验计划书

版本：V3.0

日期：2026-08-11（Asia/Shanghai）

状态：**计划已审查，尚未授权实现或运行**

文档权威路径：

```text
intersuit/harness/M4_PTPA_AUDIO_DYNAMIC_SELECTOR_PROJECT_PLAN_ZH.md
```

本文件是 V3 唯一执行计划。任何影响模型锁、数据划分、主指标、K、seed、门禁阈值或阶段顺序的修改，都必须提升版本号、记录变更原因并重新 Review。首条正式推理开始后，禁止回改预注册门禁；只能创建新版本并把旧结果保留为失败或历史证据。

## 0. 执行摘要

本计划基于下面这个唯一模型版本开展：

```text
intersuit/checkpoints/M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1
```

目标不是立刻把 Dynamic Window Selector 接进正式训练，而是先回答一个可证伪的问题：

> 在 M4/Qwen2、视觉塔、BEATs、现有 projector、fusion 和 gate 全部冻结时，如果人为提供理想音频窗口，最终任务是否明显变好？

决策只允许有两条主分支：

1. **Oracle 明显有效**：先测试确定性 Top-K，再训练 question-conditioned selector；训练范围只限 selector。
2. **Oracle 无明显收益**：立即停止 selector 路线，优先重构音频语义目标、答案表示和融合位置。

本轮不允许通过反复调 K、gate、数据子集或门禁阈值把失败实验“调成成功”。

## 1. 模型、代码与基线锁

### 1.1 目标模型文件锁

| 资产 | SHA256 |
| --- | --- |
| `config.json` | `359aed2660f9e8a3606d6d97d1fa89d5265d83415d414c56c3f21a5e17c2eeb8` |
| `model.safetensors.index.json` | `202dbfc77a361e69e69a9fe10cd230922a26c597a19dcf86df071f6ca84d1470` |
| `audio-branch.safetensors` | `de23fd7281b3835183533df384ab3398849b13c50386875a75b680cf4e12bf17` |
| `model_version_manifest.json` | `8e5c68069c62c22e736f7022074d5d8832943a49d2a8bb6c7dd7c2866e47a4cf` |
| PT/PA 评测锁 | `1ccddb36060a5dacad6bd3cb156395339225a57841517991441c8cce0b4aec4d` |
| 旧 selector V2 锁 | `0619688a6cce142e85115a0624b6bc7f37a76758afa0402350752ad41c17f5f9` |

代码基线：

```text
e362856d5e6b397c3fcd70b6703919307d022d93
```

BEATs 权重 SHA256：

```text
d43cbfad4d7b56381c061d7a24774f908d4d94c72961f6eb1d9090ff18cd8d34
```

任何一个锁发生变化，都必须创建新实验版本，不能在 V3 结果目录内继续追加。

### 1.2 当前配置的真实语义

目标模型已经配置为：

```text
scene_audio_window_mode=micro_window
dynamic_window_top_k=8
window_sec=1.0
hop_sec=1.0
fusion=beats_simple_residual
gate=0.005
```

因此 2026-08-11 已完成的 correct 基线实际是 **Current Micro-window Top-8**，不是 All windows。

任何新计划、配置、表格和报告都必须使用这个准确名称，禁止把它重命名为 fixed/all。

### 1.3 已冻结的端到端基线

| Benchmark | Behavior-off | Current Micro Top-8 | 配对变化 |
| --- | ---: | ---: | ---: |
| dev760 Accuracy | `250/760 = 32.89%` | `255/760 = 33.55%` | `+0.66pp`；9 gain / 4 harm |
| OmniMMI PT Accuracy | `126/200 = 63.00%` | `126/200 = 63.00%` | 0 个二值翻转 |
| OmniMMI PA Accuracy | `21.50%` | `22.50%` | `+1.00pp` |
| OmniMMI PA Precision | `31.8190%` | `31.8685%` | `+0.0494pp` |
| OmniMMI PA IoU | `14.0221%` | `13.7894%` | `-0.2327pp` |

三套 gate0 均已验证与 behavior-off 精确一致。

这些结果是 selector 新实验必须超越或保持的下游基线，但 dev760/PT/PA 已被多次分析，不能再被称为未见 test。

### 1.4 旧锁处理

现有 `dynamic_selector_v2_checkpoint_lock.json` 必须保持原样，其中：

```text
learned_selector_training_allowed=false
```

继续有效。

新路线必须创建独立的 V3 run lock。只有本计划定义的 Oracle 端到端主门禁全部通过，V3 lock 才能单独把 selector-only training 改为 allowed。禁止修改旧锁来绕过旧失败结论。

## 2. 审查结论与已知反证

### 2.1 已有正向定位证据

- UnAV 稀疏 validation：K=4/8 的 Oracle、Energy、Eventness 均高于 Random；
- K=8 时 Oracle Event Coverage 接近 1.0；Eventness 与 Random 均值约为 `0.320/0.179`；
- AVE validation：Oracle Hit@4 为 1.0，Oracle−Random 为 `10.63pp`。

这些结果只证明窗口定位可能有效，不证明 M4 最终回答会变好。

### 2.2 已有失败证据

- AVE official test 的 Oracle−Random 为 `8.86pp`，未达到预注册 `10pp`；不得调 K、改阈值或重跑 test；
- 旧 `checkpoint-150` LongVALE Oracle pilot：
  - Oracle−All METEOR：`+0.00074`；
  - Oracle−Random mean METEOR：`+0.00044`；
  - Oracle 增加重复输出；
  - 正式结论为 `stop_selector_route`；
- 旧 Semantic Contrast dev760：correct 低于 video-only 和 mismatched，正确音频选择性失败；
- 旧 Temporal scorer：`diagnostic_failed + schema_incompatible + missing_inputs`，不具备正式资格。

### 2.3 为什么允许再做一次

旧 LongVALE pilot 加载的是完整旧 AS-M4 checkpoint；当前目标版本由官方 M4 基座叠加轻量音频 overlay 构成，语言模型和视觉主干不同。当前版本也在新 dev760 与 PA 上出现小幅正信号。

因此允许做一次更小、更严格的新版本诊断，但旧失败是强先验：

- 不降低 Oracle 门禁；
- 不重复旧 official test；
- 不把定位指标代替端到端指标；
- Oracle 再失败就终止路线。

## 3. 可证伪假设

### H0：窗口选择无端到端价值

Oracle Top-K 不显著优于 true All、Random、当前 Micro Top-8 或 Oracle-mismatch。

若 H0 不能被拒绝，停止 selector。

### H1：理想窗口存在端到端价值

Oracle Top-K 在冻结最新模型上显著优于 true All、Random 和当前 Micro Top-8，且不增加输出异常。

H1 通过后才允许测试 deterministic/learned selector。

### H2：确定性策略可恢复 Oracle headroom

Energy/Eventness/合格 Temporal 至少恢复 30% 的 `Oracle−Random` 端到端差值，并在独立 confirm split 上成立。

H2 通过时优先采用确定性策略，question-conditioned selector 只作增量实验。

### H3：问题条件信息是必要的

H1 通过、H2 失败时，selector-only 的 question-conditioned scorer 能在 confirm split 上显著高于 deterministic 和 Random，同时保持冻结边界。

H3 失败后不扩大训练范围。

## 4. 非目标与禁止事项

本轮不做：

- M4/Qwen2、视觉塔、BEATs、projector、fusion 联合训练；
- selector 与最终答案 loss 的首次联合反传；
- 新 gate sweep；
- dev760/PA 上反复挑 K 或策略；
- 在已经看过的 AVE official test 上重新决策；
- 复用旧 Temporal scorer 进入正式矩阵；
- 把 Oracle 结果作为可部署模型指标；
- 覆盖任何基线 checkpoint、现有预测或结果目录。

## 5. 代码设计原则：先重构合同，不做条件补丁

实现阶段禁止继续在 runner、model forward 和 OmniMMI adapter 中堆叠以实验名为条件的 `if/elif`。

### 5.1 统一选择合同

新增单一、类型明确的选择合同：

```text
SelectionRequest
  sample_id
  source_indices
  timestamps
  valid_mask
  raw_windows 或 cached_features
  energy/onset/eventness/temporal scores（可选）
  question_features（仅 learned）
  target_intervals（仅 diagnostic oracle）
  seed（仅 random/mismatch）

SelectionResult
  policy_name
  selected_source_indices
  selection_scores
  selection_mask
  selected_timestamps
  selection_stage
  full_window_count
  selected_window_count
  policy_metadata
```

所有 policy 必须返回相同结构；runner、模型诊断和 evaluator 只消费 `SelectionResult`，不根据策略名重写业务逻辑。

### 5.2 Policy registry

策略通过 registry 构造：

```text
fixed_all
current_micro_topk
uniform_topk
energy_topk
eventness_cached_topk
temporal_highconf_topk
oracle_topk
random_topk
oracle_mismatch_topk
question_conditioned_topk
```

每个 policy 单独实现、单独测试；禁止在一个大函数中继续添加策略分支。

### 5.3 外部选择与模型内部选择分层

分成两类：

1. **External deterministic/oracle policy**：在进入 BEATs 或 scene-audio encoder 前确定原始微窗口；模型接收已经选好的窗口和 trace，内部 selector 必须 bypass。
2. **Question-conditioned policy**：在冻结 BEATs feature 和冻结 question embedding 上打分；训练时独立运行，端到端确认时才接入模型。

`fixed_all` 和 external Top-K 不得通过永久修改 `model.config.scene_audio_window_mode` 实现。需要显式的 batch/forward selection mode，防止一个条件污染后续条件。

### 5.4 Oracle 隔离

Oracle policy 必须位于 diagnostic-only namespace，并满足：

- 没有 target interval 时直接失败；
- train/production mode 调用直接失败；
- 输出 metadata 固定写入 `uses_ground_truth=true`；
- 正式发布模型配置不能引用 Oracle；
- Oracle manifest 与普通 manifest 使用不同 artifact kind。

### 5.5 计算口径

每个 policy 明确 `selection_stage`：

- `pre_encoder`：可以减少 BEATs 窗口 forward；
- `post_encoder`：只能减少后续 fusion/token，不能声称节省 BEATs；
- `fixed_all`：容量上界，计算预算不与 Top-K 匹配。

Energy 可以做 pre-encoder；当前 Eventness/Temporal 依赖 BEATs feature 时属于 post-encoder。

## 6. 窗口与公平性合同

- 原子窗口：`1.0s`；
- hop：`1.0s`；
- tail：与目标 checkpoint 现有 pad/clamp 规则一致；
- 主预算：K=8；
- K=4：仅离线定位，或 K=8 Oracle 通过后的效率确认；
- Top-K 结果必须唯一、合法，并在进入模型前恢复时间顺序；
- 所有 Top-K 条件使用相同 K、相同音频、相同 gate、相同 fusion 和相同生成参数；
- `N < K` 时选择全部 N 个窗口，并记录 effective K；
- Random 使用 sample_id + 固定 seed 派生随机流，不能让不同样本意外共享同一索引序列；
- mismatch 必须是物理媒体级无自映射 derangement；
- selected indices、timestamps 和 source weights 必须一一对应。

Smoke 必须证明：

- true All 的 selected count 等于 full count；
- external Top-K 没有二次选择；
- current Micro Top-8 仍走原内部路径；
- gate0 与 behavior-off 精确一致；
- PA 分批流式路径的 source-weight 投影与主 M4 路径一致。

## 7. 实验条件矩阵

| 条件 | 窗口 | 角色 | 是否可部署 |
| --- | --- | --- | --- |
| `behavior_off` | 无音频 | 原 M4 基线 | 是 |
| `gate0` | 执行选择但残差为 0 | 精确回退 | 是 |
| `fixed_all` | 全部有效窗口 | 容量上界 | 是，但高成本 |
| `current_micro_top8` | 当前内部 selector | 当前版本基线 | 是 |
| `uniform_top8` | 均匀 8 个 | matched-K 对照 | 是 |
| `energy_top8` | Energy 排名 | 低成本确定性候选 | 是 |
| `eventness_top8` | 合格 Eventness 排名 | 确定性候选 | 是 |
| `temporal_highconf_top8` | 新合格 Temporal scorer | 条件候选 | 是 |
| `oracle_top8` | 标注重叠优先 | 可用性上界 | 否 |
| `random_top8_seed_*` | 5 个固定 seed | 机会/方差基线 | 否 |
| `oracle_mismatch_seed_*` | 错配音频中的映射 Oracle | 语义选择性反证 | 否 |
| `question_conditioned_top8` | 问题-窗口相关性 | learned 候选 | 条件通过后 |

固定控制 seed：

```text
20260720
20260721
20260722
20260723
20260724
```

## 8. 数据协议与防泄漏

### 8.1 LongVALE

现有 100-video Oracle pilot 已被旧实验使用，只能作为 V3 diagnostic pilot，不能称为未见 confirm。

V3 pilot：

- 100 个物理去重视频；
- 每视频按冻结规则选 1 个稀疏事件；
- 共 100 个端到端样本；
- 抽样规则、ID、caption、interval 和 SHA 在任何 M4 推理前锁定。

若 Oracle 通过，必须从未进入旧/新 pilot 的物理视频构建独立 confirm split。没有新物理视频时，不能把 pilot 重命名为 confirm。

### 8.2 OmniMMI PA

PA 200 条已用于当前模型评测，不是未见 test。V3 内部预先冻结：

- `PA-reserve40`：只用于 selector smoke/pilot；
- `PA-confirm160`：策略与阈值冻结后一次性运行；
- 两部分不混合报告推断统计；
- 完整 200 可作为描述性结果，但不能宣称 untouched test。

Oracle 使用 answer interval，属于标签泄漏上界，只用于诊断。

### 8.3 dev760 与 PT

- dev760 没有 event start/end，禁止伪造 Oracle；
- dev760 只用于 deterministic/learned 候选的下游回归；
- PT 没有窗口级 Oracle 标注，只作行为回归；
- dev760/PT 已被反复查看，不能用于解锁训练或调参。

### 8.4 UnAV 与 AVE

- UnAV validation 只复用已有离线定位资产；
- AVE validation 可作离线旁证；
- AVE official test 已执行并失败预注册门禁，禁止重跑、调 K 或重新解释为 V3 test。

### 8.5 物理媒体去重

所有 split 必须按物理媒体 SHA/physical_media_id 去重，不只按 sample_id、文件名或 YouTube ID。

必须输出交集矩阵：

```text
train ↔ pilot
train ↔ confirm
pilot ↔ confirm
historical selector assets ↔ confirm
dev760 ↔ LongVALE/PA（按物理媒体）
```

任一非预期交集都 fail-closed。

## 9. 分阶段执行计划

### P0：合同与 harness 重构

不运行 GPU。

交付：

- typed selection contract；
- policy registry；
- true `fixed_all`；
- external selection bypass；
- Oracle diagnostic 隔离；
- V3 run lock schema；
- selector-only trainable alias；
- 参数/梯度/缓存/窗口合同测试。

P0 门禁：

- CPU 测试全部通过；
- current Micro Top-8 行为未改变；
- All/Top-K 不发生二次选择；
- 无 experiment-name 特判进入模型核心；
- `git diff --check` 通过；
- Review 清单无 blocker。

### P1：离线定位矩阵

不运行 M4，只读取冻结缓存。

策略：All、Uniform、Energy、Eventness、Oracle、5-seed Random；Temporal 不合格时明确 omitted。

K：4、8。

指标：

- Event Coverage；
- Positive Recall；
- Hit@K；
- mean best tIoU；
- first-hit rank；
- Oracle−Random cluster bootstrap 95% CI。

P1 只用于验证 score/窗口合同，不解锁训练。

### P2：GPU 接线门禁

单卡，每条件 2 条。

顺序：

1. behavior-off；
2. gate0；
3. current Micro Top-8；
4. true All；
5. external Random Top-8；
6. external Oracle Top-8；
7. Oracle-mismatch。

必须保存完整 SelectionTrace、音频残差、冻结 SHA、显存峰值和运行时间。

### P3：LongVALE 核心 Oracle pilot

先 20 样本 canary，再运行冻结 100 样本。

核心条件：

- behavior-off；
- fixed_all；
- current_micro_top8；
- oracle_top8；
- 5-seed Random；
- 5-seed Oracle-mismatch。

Oracle 未通过时立即停止，不补跑 deterministic 或 learned 条件。

### P4：确定性策略补跑

只在 P3 通过后执行：Uniform、Energy、Eventness、合格 Temporal。

在 pilot 上选择候选后，只允许在独立 confirm split 运行一次正式确认。

### P5：PA 辅助 Oracle

先 PA-reserve40，再在策略冻结后运行 PA-confirm160。

主指标是 IoU；Accuracy 与 Precision 为非劣约束。

PA 不能覆盖 LongVALE 主门禁。LongVALE 失败时，PA 偶然提升不能解锁 selector。

### P6：selector-only 训练

只在 P3 通过后执行，见第 12 节。

### P7：下游回归

候选冻结后运行：

- dev760 behavior/gate0/current/candidate/mismatched；
- OmniMMI PT behavior/gate0/current/candidate；
- OmniMMI PA behavior/gate0/current/candidate/mismatched。

不得在这些结果上继续更新 selector。

## 10. 统计计划

### 10.1 预注册主比较

LongVALE 主比较：

1. Oracle − fixed_all；
2. Oracle − 5-seed Random mean；
3. Oracle − current Micro Top-8；
4. Oracle − 5-seed Oracle-mismatch mean；
5. Oracle − behavior_off。

主指标：METEOR；ROUGE-L/CIDEr/BLEU4 为辅助指标。

### 10.2 聚类与配对

- 以物理视频为 cluster；
- 10,000 次 cluster bootstrap；
- bootstrap seed 在矩阵生成前冻结；
- 同一样本条件间做 paired difference；
- 分类正确/错误翻转补充 exact McNemar；
- PA 对 Accuracy/Precision/IoU 分别做 paired cluster bootstrap。

### 10.3 多重比较

五个预注册主比较按固定顺序 gate，不做事后挑选。

deterministic 候选的多个次要比较使用 Holm 校正；未经校正的 p-value 只能作描述性结果。

### 10.4 实用效果门槛

LongVALE Oracle 必须全部满足：

- Oracle−All METEOR `>= 0.01`；
- Oracle−Random mean METEOR `>= 0.01`；
- Oracle−Current Micro Top-8 METEOR `>= 0.01`；
- 上述三个差值的 95% CI 下界均 `> 0`；
- Oracle > mismatch mean；
- Oracle > behavior-off；
- empty/EOS/repetition/nonfinite 不高于 All。

PA 辅助门禁：

- Oracle−All IoU `>= +1.0pp`，95% CI 下界 `> 0`；
- Accuracy 不低于 All `-1.0pp`；
- Precision 不低于 All `-0.5pp`；
- Oracle > mismatch。

确定性候选：

- 高于 5-seed Random mean，CI 下界 `> 0`；
- 恢复至少 30% 的 Oracle−Random headroom；
- confirm split 方向一致；
- 不增加输出异常。

冻结候选的下游非劣门禁（相对 Current Micro Top-8）：

- dev760 Accuracy 不低于 `-0.5pp`；
- PT Accuracy 不低于 `-1.0pp`；
- PA Accuracy 不低于 `-1.0pp`；
- PA Precision 不低于 `-0.5pp`；
- PA IoU 不低于 `-0.5pp`；
- correct 条件必须高于同配置 mismatched；
- 空输出、EOS 异常、重复输出和 nonfinite 不得增加。

这些阈值只用于防止候选破坏现有能力，不能替代 LongVALE confirm 的正向收益门禁。

## 11. Oracle 决策表

| 结果 | 决策 |
| --- | --- |
| Oracle 未超过 All/Random | 停止 selector；转语义/表示/融合重构 |
| Oracle 定位好但端到端无提升 | 停止 selector；说明瓶颈不在选窗 |
| Oracle 提升但 mismatch 同样提升 | 停止；先修正确音频选择性 |
| Oracle 通过，deterministic 通过 | 优先确定性方案，再做 question-conditioned 增量 |
| Oracle 通过，deterministic 失败 | 允许 question-conditioned selector-only 诊断 |
| learned pilot 通过但 confirm 失败 | 不晋级，不扩大训练范围 |
| learned confirm 通过 | 再做 dev760/PT/PA 冻结回归，仍不直接联合训练 |

## 12. Question-conditioned selector-only 训练

### 12.1 参数范围

必须冻结：

- M4/Qwen2、embedding、lm_head；
- vision tower、mm projector、resampler；
- BEATs；
- scene audio projector；
- fusion/audio projector；
- event detector、temporal aligner、confidence gate；
- query-speech encoder/projector；
- 其余 streaming module 参数。

只训练：

```text
question_conditioned_window_selector.audio_projection.weight
question_conditioned_window_selector.question_projection.weight
```

当前约 `1,114,112` 参数。若新增 scalar/listwise scorer，必须重新记录精确参数名、数量和初始化 SHA。

现有 `MM_TUNABLE_PARTS=streaming_av_module` 会解冻过多参数，禁止使用。应新增独立、语义明确的 selector-only alias，并由单一函数生成 trainable allowlist。

### 12.2 低成本训练方式

第一选择不是加载 7B 模型联合训练，而是离线缓存：

- 冻结 BEATs window feature；
- 冻结 M4 question embedding；
- timestamp、mask、target interval；
- physical_media_id、question SHA、checkpoint SHA；
- hard-negative 映射。

在缓存特征上单独训练约 111 万参数的 selector。只有离线 retrieval/Hit@K 门禁通过，才加载完整 M4 做端到端确认。

### 12.3 初始化

组合 checkpoint 中两个 question-conditioned 投影是固定 seed 的补权重，不是预训练 selector。

新 run 必须：

- 使用新固定 seed；
- 保存初始化 state SHA；
- 不把 overlay 的随机补权重描述为预训练；
- 至少运行 3 个训练 seed；
- seed 间报告均值、标准差和最差 seed。

### 12.4 损失

先训练排序，不接最终答案 loss：

- question-window listwise / InfoNCE；
- 正样本：时间重叠且语义匹配窗口；
- 同视频 hard negative：高能量但非目标、邻近但不重叠窗口；
- 跨视频 hard negative：同类或声学相似但答案不匹配；
- mismatch negative：时间相似但来自错配音频；
- coverage loss；
- diversity/相邻重复惩罚。

BEATs feature 和 question embedding 必须 detach；梯度只能出现在 selector allowlist。

### 12.5 优化与停止

- 独立 optimizer，只接收 allowlist 参数；
- 每步审计非 allowlist grad 数量必须为 0；
- 保存 best validation ranking checkpoint，不按 dev760/PT/PA 选 checkpoint；
- early stop 指标预先固定；
- 训练失败不允许解冻 fusion、projector 或语言模型补救。

## 13. Semantic Contrast 重设计

只有 Oracle 通过才进入本节。

旧目标的问题是整段 correct/mismatch 对比没有建立可泛化的正确音频选择性。新目标改为 question-conditioned window ranking：

- 问题与正确事件窗口拉近；
- 同视频非目标窗口推远；
- 错配音频中时间相似窗口作为强负样本；
- 事件类别和 caption 语义共同监督；
- 正负样本按事件类别、时长、能量和来源平衡；
- 先证明 retrieval/Hit@K，再接最终回答。

Semantic Contrast 与 selector 训练分开编号、分开 optimizer、分开 checkpoint，不在首轮一起训练。

## 14. Oracle 失败后的替代路线

优先级固定为：

1. **语义目标**：重新构造 correct/mismatch、同视频 hard negative、事件描述监督；
2. **答案表示**：canonical event token、短文本或 query-conditioned semantic embedding；
3. **融合位置**：比较当前视觉帧残差与 LLM input/cross-attention 的更晚融合；
4. **时间目标**：重新训练符合 1s/1s schema 的 Temporal scorer；
5. **单模块上界**：每个方案先冻结其他模块做上界；
6. **最后才联合训练**：只有多个单模块独立通过，才讨论联合训练。

## 15. Temporal scorer 资格要求

旧 Temporal 资产不能使用。新 scorer 必须满足：

- 窗口 schema 精确为 1s/1s；
- 输入字段完整且与运行时一致；
- train/pilot/confirm 物理媒体零重叠；
- 至少 3 个 seed；
- 每个 seed 都通过预注册 margin/offset 指标；
- checkpoint、feature manifest、input SHA 完整；
- test 未访问；
- 明确是 pre-encoder 还是 post-encoder；
- qualification 结果为 formal eligible。

未满足时矩阵保留：

```text
temporal_highconf_top8 = omitted_ineligible_asset
```

不允许因缺少 Temporal 而推迟 Oracle 主判断。

## 16. 缓存与产物规范

### 16.1 缓存键

缓存键至少包含：

```text
physical_media_sha256
audio_decode_contract_sha256
window_contract_sha256
BEATs_checkpoint_sha256
M4_checkpoint_sha256（question embedding）
question_text_sha256
feature_dtype
feature_shape
```

任一字段变化都 cache miss，禁止静默复用。

### 16.2 结果目录

建议根目录：

```text
intersuit/harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/
```

子目录：

```text
locks/
manifests/
selection_traces/
caches/
smokes/
predictions/
metrics/
reviews/
logs/
```

每阶段必须保存：

- 开始/结束时间；
- Conda 环境与 Python executable；
- Git commit/worktree diff；
- GPU 映射、功率、显存；
- 完整 config 和 SHA；
- stdout/stderr；
- PID/exit code；
- manifest 与 selection trace；
- frozen state audit；
- evaluator 输入与输出；
- postflight/kernel error 检查。

### 16.3 结果不可覆盖

- 每次实验使用新 run name；
- resume 只能在严格前缀和相同 lock SHA 下执行；
- partial 输出先归档再重跑；
- smoke/canary 不得写入 full 正式文件；
- 不覆盖现有 dev760/PT/PA 结果。

## 17. 资源与运行预算

### 17.1 GPU 策略

- CPU 合同、矩阵、离线统计：不占 GPU；
- 单卡 smoke：显式选择一张 GPU；
- 条件级并行最多使用物理 GPU 2/3/4；
- GPU 1 只用于隔离单卡，不进入多卡；
- 不默认使用 GPU 0/2/3/4 四卡；
- 不使用五卡。

### 17.2 粗略预算

- P0 合同实现与 CPU 测试：0.5–1 个工作日；
- P1 离线定位：1–3 小时；
- P2 smoke：0.5–1 GPU 小时；
- P3 LongVALE 核心 100 样本：约 10–16 条件，预计 8–16 GPU 小时；
- P5 PA-reserve40：约 3–6 GPU 小时；
- selector 离线训练：单卡预计 1–3 小时/seed；
- confirm 与下游全量只在前置门禁通过后估算和启动。

实际速度以 20-sample canary 重新估算；估算变化必须先汇报。

### 17.3 后台持久化

超过 30 分钟的任务必须使用与 Codex 临时会话脱钩的可审计 launcher：

- `nohup + setsid` 或项目正式 staged launcher；
- PID、状态文件、阶段门禁；
- stdout/stderr 不依赖 `conda run` 缓冲；
- 每个阶段完成后验证行数再进入下一阶段。

## 18. 测试计划

### 18.1 单元测试

- 每个 policy 的稳定排序与 tie-break；
- K=0、K>N、空 mask、短音频、尾窗口；
- Random per-sample 可复现；
- mismatch 无自映射；
- Oracle 无标签/生产模式 fail-closed；
- SelectionResult shape、时间顺序和 source index；
- cache key/SHA 漂移拒绝；
- selector-only allowlist 与参数量；
- optimizer 不含冻结参数。

### 18.2 集成测试

- true All 不经过内部 Top-K；
- external Top-K 不发生二次选择；
- current Micro Top-8 行为保持；
- gate0 精确回退；
- frozen SHA 前后不变；
- batch 内不同 effective K；
- PA streaming chunk 的 source_weights 投影正确；
- resume 严格验证 manifest 前缀；
- evaluator 可读取带标准 JSON boolean 的诊断输出。

### 18.3 GPU smoke

- 每条件 2 条；
- 检查显存、NaN/Inf、空输出、EOS、残差比例；
- 检查 `selected_window_count`、`full_window_count`；
- 检查物理/逻辑 GPU 映射；
- 结束后检查 NVRM/Xid/AER/PCIe/hung task。

## 19. 风险登记表

| ID | 风险 | 触发信号 | 预防/处置 | 阶段影响 |
| --- | --- | --- | --- | --- |
| R1 | 找不到物理未见 LongVALE confirm | 与历史资产 SHA 有交集，或样本数不足 | P0/P1 先完成媒体交集审计；无法建立时停止，不能把 pilot 冒充 confirm | 阻断 P4/P6 晋级 |
| R2 | Oracle interval 与 1s 窗口口径错位 | 手算样本与 trace 不一致 | 建立边界、tail、半开区间单测和人工 gold cases | 阻断 P1/P2 |
| R3 | external Top-K 被内部 selector 二次截断 | selected/full count 或 source index 异常 | 显式 bypass 合同、集成测试、逐样本 trace | 阻断所有 GPU full |
| R4 | true All 因长度或显存被静默截断 | 输入数少于预处理窗口数 | fail-closed；若超预算，单独报告不可运行，不以截断结果代替 All | 阻断对应比较 |
| R5 | 条件间配置或缓存污染 | 同输入重复运行不一致 | 禁止永久修改全局 config；cache key 包含全部锁；条件隔离 smoke | 阻断 P2 |
| R6 | 100-video pilot 统计功效不足 | CI 很宽、方向不稳定 | 如实判为 inconclusive/失败；不得通过事后扩样调门禁，扩样只能进入新版本 | 停止 V3 解锁 |
| R7 | selector-only 实际解冻其他参数 | allowlist 外出现 grad 或 SHA 变化 | optimizer 构造和前后 SHA 双审计，任一异常立即停止 | 阻断 P6 |
| R8 | evaluator 解析或行对齐错误 | 标准 JSON boolean 失败、样本数不一致 | 禁用不安全 `eval()` 路径；schema 校验、ID join、分母审计 | 阻断指标发布 |
| R9 | 后台任务被会话回收或产生半成品 | PID 消失、状态仍 running、行数不足 | detached launcher、原子状态、完成行数和 exit code 双校验 | 当前阶段失败并归档 |
| R10 | PA/dev760/PT 被用于事后选型 | 多次查看后继续改策略 | 只作冻结回归；任何基于其结果的修改进入下一版本 | V3 候选失效 |

风险状态必须写入各阶段 Review；不能只在总结中口头说明。

## 20. Bug Review 清单

Review 按 blocker/major/minor 分类，以下任一 blocker 未关闭不得运行 full pilot。

### 20.1 配置与状态污染

- 是否在条件间永久修改 `model.config`？
- 是否在一个条件结束后恢复 selection mode？
- behavior/gate0 是否意外保留上一条件的 audio cache/state？
- question-conditioned causal state 是否跨样本泄漏？

### 20.2 双重选择与索引

- external Top-K 后内部 selector 是否又选一次？
- selected source index 是否仍指向原始完整窗口？
- index、timestamp、feature、signal statistics 是否同序？
- batch pad 后 source_weights 是否映射到正确槽位？
- PA 首批窗口少于 K 时是否重复出现历史 mask/shape bug？

### 20.3 All 真实性

- `fixed_all` 是否真的输入全部有效窗口？
- 是否因 checkpoint 默认 micro_window 被静默截成 Top-8？
- 是否因 max length/缓存而静默丢尾窗口？
- All 的诊断窗口数是否等于预处理窗口数？

### 20.4 数据泄漏

- Oracle target 是否只在 diagnostic policy 可见？
- pilot/confirm 是否物理媒体隔离？
- Random/mismatch mapping 是否在看指标前冻结？
- 是否错误把 dev760/PT/PA 称为 unseen test？

### 20.5 冻结与梯度

- trainable allowlist 是否精确？
- optimizer param group 是否只含 selector？
- frozen 参数是否出现 grad 或 SHA 变化？
- question/BEATs cache 是否 detach？
- 三个 seed 是否使用独立初始化？

### 20.6 指标与 evaluator

- PA evaluator 是否继续使用不兼容标准 JSON 的 `eval()`？
- METEOR/PA 指标输入是否逐条对齐？
- cluster bootstrap 是否按物理视频而非 QA 行？
- 多策略比较是否执行 Holm 校正？
- 空预测是否按官方口径计入分母？

### 20.7 运行可靠性

- 长任务是否依附临时工具会话？
- 输出是否因未 flush 看似 0 行？
- PID 已退出但状态文件是否仍显示 running？
- full 前是否验证 smoke 输出目录隔离？
- D 状态时是否停止反复调用 `nvidia-smi`？

## 21. Review 流程与签字门禁

每阶段 Review 必须输出独立 JSON 和中文报告：

```text
status: pass | pass_with_omissions | fail
blockers: []
majors: []
minors: []
evidence: {...}
next_stage_allowed: true | false
```

顺序：

1. P0 code/contract review；
2. P1 data/cache/statistics review；
3. P2 GPU smoke review；
4. P3 Oracle gate review；
5. P4 deterministic review；
6. P6 selector-only training review；
7. confirm/downstream final review。

任何阶段 `next_stage_allowed=false` 时 launcher 必须 fail-closed，不能只靠人工记忆停止。

## 22. 清理、回退与提交

- 所有新模块有配置开关；
- behavior-off 必须回到原 M4；
- gate0 必须保持音频诊断但零残差；
- 权重回退必须重新加载本计划锁定 checkpoint，不能靠 freeze 恢复；
- selector 训练使用新 checkpoint/run name；
- 失败的短步 checkpoint、重复 cache 和 smoke 权重及时清理；
- 重大合同/模块实现完成后单独 Git commit；
- 提交前不得包含模型、媒体、cache、预测或训练日志。

## 23. 最终完成定义

Selector 路线只有同时满足以下条件才可称为“候选方案”，不能直接称为最终方案：

- Oracle 主门禁通过；
- deterministic 或 learned selector 在独立 confirm 通过；
- correct > mismatched；
- dev760 不退化；
- PT 不超过容忍度退化；
- PA Accuracy/Precision/IoU 满足冻结门禁；
- behavior/gate0 精确回退；
- 冻结 SHA、数据隔离、统计 Review 全通过；
- 至少 3 个训练 seed 方向一致；
- 没有 blocker/major 未关闭。

若任一条件失败，selector 不进入正式训练主线。

## 24. 本计划批准后的第一批工作

只执行 P0，不启动训练：

1. 定义 typed selection contract 与 policy registry；
2. 重构 runner/model 的 external selection bypass；
3. 实现 true fixed_all；
4. 隔离 Oracle diagnostic policy；
5. 新增 selector-only allowlist/optimizer audit；
6. 修复 evaluator 标准 JSON 兼容；
7. 补齐 CPU 单元/集成测试；
8. 完成独立 code review；
9. Review 通过后才生成 P1 离线矩阵。

P0 的实现要求是结构化重构，不允许用实验名特判、环境变量堆叠或复制现有分支形成补丁式方案。
