# M4-PTPA 音频语义、答案表示与融合位置重构计划

## 1. 决策背景

基础模型固定为 `M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1`。

Dynamic Window Selector V3 已完成 100 个物理视频、14 个条件和 10,000 次官方 METEOR corpus paired cluster bootstrap。Oracle Top-8 相对 All、Random、Current Micro Top-8、mismatch 和 behavior-off 的五个差值全部为负，因此 selector 路线正式停止：不运行 deterministic selector、不训练 learned selector、不访问 selector confirm。

本计划建立独立路线 `audio_semantic_fusion_reconstruction_v1`。它不继承 selector 权限，也不把旧 Semantic Contrast、旧 R2 文本目标或已访问的 teacher-confirm 包装为新 confirm。

## 2. 核心假设

按优先级检验：

1. 冻结音频表示是否能区分正确事件语义与同视频、跨视频和 mismatch hard negative；
2. canonical event token、标准化短文本和 query-conditioned semantic embedding 哪一种更适合承载答案语义；
3. 音频信息是否在当前视觉 token 残差融合中过早被稀释，晚融合是否有更高上界；
4. 只有多个单模块分别通过，才讨论联合训练。

## 3. 历史资产分类

### 3.1 可作为只读基础证据

- AVE R0 的物理媒体划分、官方标签映射和 `test_locked` 边界；
- 冻结 BEATs checkpoint 与已审计的本地媒体；
- behavior-off、gate0、current、correct、mismatch 的历史回退证据；
- 本次 P3 官方 selector 失败结果。

### 3.2 只能作为明确反证

- 旧 Semantic Contrast dev760：correct 低于 video-only，mismatch 反而更高；
- 旧 R2 的 `old_answer_token_mean`、`canonical_event_label`、`question_plus_canonical_answer`、`label_description_prototype` 均未通过；
- 将 clip label 复制到全部 1 秒窗口不能视为窗口级事件监督。

### 3.3 仅可作为开发候选

- `audio_semantic_teacher_t0_v2/pad__ensemble3` 可用于设计 teacher target；
- 其 `teacher_confirm` 已访问，不能作为本路线 confirm 或最终门禁；
- 新路线使用它前必须重新审计许可证、模型来源、输入归一化、类别映射和物理媒体交集。

## 4. 全局禁止项

在对应阶段 Review 通过前，全部保持 `false`：

```text
gpu_execution_allowed
feature_extraction_allowed
semantic_target_training_allowed
answer_representation_training_allowed
fusion_training_allowed
joint_training_allowed
confirm_accessed
formal_test_accessed
selector_training_allowed
```

禁止：

- 重启 selector 路线或修改其失败门槛；
- 使用旧 `MM_TUNABLE_PARTS=streaming_av_module` 解冻整条音频链；
- 首轮联合训练 Qwen2、视觉塔、BEATs、projector 和 fusion；
- 用 dev760/PT/PA 选超参数或 checkpoint；
- 把已访问的 teacher-confirm、PA 或旧 LongVALE pilot 称为 untouched confirm/test；
- 把 clip label 静默提升为精确时间边界；
- 覆盖原始 M4、12k/32k 或 PTPA checkpoint。

## 5. 阶段 S0：合同、数据和泄漏审计

仅 CPU、只读，不提取特征、不训练。

必须完成：

1. 冻结 selector 失败 Review SHA；
2. 建立 train/development/internal-confirm/test-locked 物理媒体交集矩阵；
3. 标记所有历史已访问 split，不能重新命名；
4. 审计每条样本的 `physical_media_id`、问题、canonical label、caption、答案类型和监督粒度；
5. 明确负样本来源和是否存在标签冲突；
6. 冻结 1s/1s 特征合同和 BEATs checkpoint；
7. 生成独立 S0 lock、Review JSON 和中文报告。

S0 通过只允许构建离线 manifest；不自动允许 GPU、特征提取或训练。

## 6. 阶段 S1：冻结音频语义上界

### 6.1 表示

优先比较：

- 冻结 BEATs clip pooling；
- 冻结 BEATs 1s/1s window pooling；
- 已资格化的开发 teacher embedding。

不得训练 projector、fusion 或语言模型。

### 6.2 正负样本

- positive：正确物理媒体、正确 canonical event；
- same-video hard negative：高能量非目标或邻近非目标窗口，仅在有真实时间监督时启用；
- cross-video same-class：同类、不同物理媒体，用于一致性；
- cross-video acoustic-hard-negative：声学相似但 canonical label 不同；
- mismatch negative：来自错配媒体，且 canonical label 必须不同；
- 无可靠时间边界的 clip-level 数据不得生成 same-video window label。

### 6.3 指标与门禁

主要指标：macro Recall@1、balanced accuracy、rank-first、positive-minus-hard-negative margin 和物理媒体级 bootstrap CI。

S1 必须同时满足：

- macro Recall@1 高于随机基线至少 `+0.20`；
- balanced accuracy 高于随机基线至少 `+0.20`；
- mean positive-minus-hard-negative margin `>0`；
- margin 95% CI 下界 `>0`；
- correct 高于 mismatch；
- 至少 3 个固定 seed 方向一致；
- internal confirm 尚未访问。

否则停止该表示，不允许通过训练下游模块掩盖失败。

## 7. 阶段 S2：答案表示上界

只允许使用 S1 通过的冻结音频表示。依次比较：

1. `canonical_event_token`；
2. `normalized_short_text`；
3. `query_conditioned_semantic_embedding`。

旧 Qwen token 均值目标固定为历史反证，不作为候选。选择指标必须在 development split 预注册，internal confirm 只在候选和阈值冻结后访问一次。

S2 门禁：候选必须在 macro Recall@1、rank-first、margin CI 三项均超过旧目标，并且 correct > mismatch；任何一项不满足即不晋级。

## 8. 阶段 S3：融合位置冻结上界

候选位置：

- `visual_token_residual`：当前早期残差基线；
- `llm_input_audio_tokens`：在 LLM 输入嵌入处注入独立音频 token；
- `late_cross_attention`：独立晚融合模块。

首轮只使用确定性 gate 或无训练 adapter，上游表示与答案目标完全冻结。每次只改变一个融合位置；behavior-off 与 gate0 必须精确回退。

端到端主门禁使用官方 LongVALE corpus 指标与物理媒体级 paired bootstrap。候选必须：

- correct 相对 current 至少 `+0.01 METEOR`；
- 95% CI 下界 `>0`；
- correct > mismatch；
- empty/EOS/repetition/nonfinite 不增加；
- internal confirm 方向一致。

## 9. 阶段 S4：单模块训练

只有 S1/S2/S3 中对应冻结上界通过后才允许。

默认冻结：

- M4/Qwen2、embedding、lm_head；
- vision tower、resampler、现有 mm projector；
- BEATs；
- 未被本阶段明确命名的 projector/fusion/streaming 模块。

每个实验使用精确参数 allowlist、独立 optimizer 和新 checkpoint。每步审计非 allowlist gradient 必须为 0。执行顺序为 `CPU contract -> 2-step -> 20-step -> 150-step -> full`，至少 3 个 seed。

## 10. 阶段 S5：两模块组合

只有至少两个单模块分别通过独立 confirm 才允许组合。组合顺序：

1. 语义目标 + 答案表示；
2. 语义目标 + 晚融合；
3. 答案表示 + 晚融合。

禁止首轮三模块联合。任何组合收益不能归因时，退回单模块结果。

## 11. 阶段 S6：冻结回归

候选冻结后一次性运行 LongVALE internal confirm、dev760、OmniMMI PT/PA。dev760/PT/PA 只做非劣回归，不用于选择候选。

沿用门禁：

- dev760 Accuracy 不低于 current `-0.5pp`；
- PT Accuracy 不低于 `-1.0pp`；
- PA Accuracy 不低于 `-1.0pp`；
- PA Precision 不低于 `-0.5pp`；
- PA IoU 不低于 `-0.5pp`；
- correct > mismatch；
- behavior-off/gate0 精确回退。

## 12. 资源顺序

- S0、manifest、合同测试、离线统计：CPU；
- S1 先复用合格冻结特征，缺失时才申请单卡 GPU 2 提取；
- S2 离线表示实验优先 CPU；
- S3 单卡 smoke 显式使用 GPU 2/3/4 中一张；
- 训练先单卡或 DDP-only，小模块通过后才评估 ZeRO-3；
- 不默认使用全部 GPU。

## 13. 完成与停止定义

本路线只有在语义目标、答案表示或融合位置中至少一个单模块通过独立 confirm，并通过官方端到端指标与回归门禁后，才可称为候选。

任一阶段 `next_stage_allowed=false`，下游 launcher 必须 fail-closed。没有合格候选时，保留原 PTPA 模型并停止新增训练。

## 14. 批准后的第一批工作

只执行 S0：

1. 固化新路线 basis lock；
2. 生成历史 split/访问状态台账；
3. 生成物理媒体交集矩阵；
4. 审计 hard-negative 合法性；
5. 审计旧 R0/R2/teacher 资产资格；
6. 输出 S0 Review；
7. Review 通过后才允许构建 S1 离线 manifest。
