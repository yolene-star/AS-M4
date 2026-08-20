# M4-PTPA 音频语义、答案表示与融合位置重构计划

版本：`v2`（2026-08-20）

唯一机器可读数据责任入口：
`intersuit/configs/m4_ptpa_audio_dataset_responsibility_v2.json`。
新数据职责不改变原 S0–S6 的科学顺序和 fail-closed 门禁。

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

### 3.4 v2 数据责任实例化

| 阶段 | 数据集 | 唯一责任 | 明确不负责 |
| --- | --- | --- | --- |
| S1 | LLP | 冻结 BEATs 事件语义、1s/1s 窗口和音频/视觉事件上界 | 开放问答、长视频生成 |
| S2 | Clotho-AQA | 问题条件下的音频—答案语义表示 | 视觉融合、长视频 |
| S3a | AVCaps | audio/visual/audio-visual 模态贡献与融合位置冻结上界 | canonical event class、1s 时间边界 |
| S3b/S6 | LongVALE | 长视频端到端生成门禁 | S1 类别选择、S2 答案表示调参 |
| 历史开发 | AVE/EPIC-SOUNDS/dev760 | 只读反证、压力测试或非劣回归 | 新 untouched confirm |

任何数据集都不能跨职责替代另一数据集的失败结论，
不得对不同 ontology/任务的绝对分数求平均或选择性报告。

## 4. 全局禁止项

在对应阶段 Review 通过前，全部保持 `false`：

```text
gpu_execution_allowed
feature_extraction_allowed
llp_pilot_download_allowed
clotho_aqa_download_allowed
avcaps_download_allowed
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
- 在 metadata/physical-ID/license/media-availability Review 前下载 LLP 全量、
  Clotho-AQA 或 AVCaps；LongVALE `part_19` 只因用户已明确授权的
  S0.5 媒体 blocker 修复而例外允许，不自动解锁推理或 confirm。

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
8. 对 LLP/Clotho-AQA/AVCaps/LongVALE 分别锁定官方来源、revision/DOI、
   split 语义、许可证、媒体实体获取方式和预期校验值。
9. 将全部历史 M4/AS-M4/AVE/AVQA/MUSIC-AVQA/LongVALE/LLP 物理 ID
   归一到同一 overlap ledger，不只与 AVE 2500 清单比较。
10. 先审计小规模 media pilot 可下载率、音轨、时长、解码、SHA
    和标签粒度，再由独立 Review 决定是否允许 full media。

S0 通过只允许构建离线 manifest；不自动允许 GPU、特征提取或训练。

## 6. 阶段 S1：冻结音频语义上界

S1 唯一主数据为 LLP。本地官方 metadata 初审为
train/validation/test `10000/649/1200`，物理 ID 跨 split 交集为 0，
三个 split 都覆盖 25 类。与已知 AVE 历史 2500 视频排除后的
`9811/636/1183` 只是初审数，正式数量必须由全历史 overlap ledger
重算后锁定。

- official train-clean：只可用于 S1 development 与后续 S4 训练；train 为弱标注，
  不得伪造 1s 密集边界。
- official validation-clean：使用 dense audio/visual annotation 选择表示、pooling 和阈值。
- official test-clean：在候选和阈值冻结后只访问一次，作 S1 confirm；
  不因此解锁 S2/S3 的 test。

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

S2 唯一主数据为 Clotho-AQA，官方物理音频 train/validation/test
为 `1174/344/473`。它只证明音频问答语义，不证明视觉或融合。
必须保留数据集官方物理隔离 split：train 只作开发/后续 S4，
validation 选候选，test 在答案表示与阈值冻结后只访问一次。

只允许使用 S1 通过的冻结音频表示。依次比较：

1. `canonical_event_token`；
2. `normalized_short_text`；
3. `query_conditioned_semantic_embedding`。

旧 Qwen token 均值目标固定为历史反证，不作为候选。选择指标必须在 development split 预注册，internal confirm 只在候选和阈值冻结后访问一次。

S2 门禁：候选必须在 macro Recall@1、rank-first、margin CI 三项均超过旧目标，
并且 answer accuracy 和 audio-required 子集均满足 correct > mismatch。
yes/no 与单词答案必须分层报告，不得由 yes/no 大类掩盖开放答案失败。
任何一项不满足即不晋级。

## 8. 阶段 S3：融合位置冻结上界

候选位置：

- `visual_token_residual`：当前早期残差基线；
- `llm_input_audio_tokens`：在 LLM 输入嵌入处注入独立音频 token；
- `late_cross_attention`：独立晚融合模块。

首轮只使用确定性 gate 或无训练 adapter，上游表示与答案目标完全冻结。每次只改变一个融合位置；behavior-off 与 gate0 必须精确回退。

### 8.1 S3a：AVCaps 模态贡献上界

AVCaps 官方 train/validation/test 为 `1661/200/200`，每个 MP4 有分离的
audio、visual 和 audio-visual captions。必须冻结官方 revision/Zenodo MD5、
重算物理去重，并证明 test 未被历史访问。

- train 只作 development/后续 S4，validation 选融合位置，test 冻结后一次访问；
- 主单位是物理视频，比较 correct audio、mismatch、visual-only、audio-only 和 audio-visual；
- 主指标为数据集内的 audio-text/audio-visual-text rank-first、Recall@1 和配对 margin bootstrap；
- correct 必须显著高于 mismatch 和 visual-only 的音频语义得分，
  同时不降低 visual-caption 非劣门禁；
- AVCaps 无 canonical event class/1s boundary，不得回流替代 S1。

### 8.2 S3b：LongVALE 长视频端到端门禁

只有 S3a 通过后才可进入 LongVALE。端到端主门禁使用官方
LongVALE corpus 指标与物理媒体级 paired bootstrap。候选必须：

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

单模块数据不得混用：语义模块只用 LLP train-clean，答案表示只用
Clotho-AQA train，融合模块只用 AVCaps train。LongVALE/dev760/PT/PA
不得用于选 checkpoint 或调训练超参。

## 10. 阶段 S5：两模块组合

只有至少两个单模块分别通过独立 confirm 才允许组合。组合顺序：

1. 语义目标 + 答案表示；
2. 语义目标 + 晚融合；
3. 答案表示 + 晚融合。

禁止首轮三模块联合。任何组合收益不能归因时，退回单模块结果。

## 11. 阶段 S6：冻结回归

候选冻结后一次性运行 LongVALE internal confirm、dev760、OmniMMI PT/PA。
LLP/Clotho-AQA/AVCaps test 的候选和阈值在此前已各自冻结，不得因 S6 结果回头修改。
dev760/PT/PA 只做非劣回归，不用于选择候选。

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
- 数据下载顺序：LongVALE `part_19` 已授权补充 -> LLP train/validation 小规模 pilot
  -> S1 媒体 Review -> Clotho-AQA -> S2 Review -> AVCaps -> S3 Review；
- LLP official test、Clotho-AQA test、AVCaps test 媒体可以在完整性层封存，
  但模型效果访问必须分阶段解封；
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

1. 冻结 `dataset_responsibility_v2` 与正式计划 Review；
2. 生成全历史 split/访问状态台账和物理媒体交集矩阵；
3. 完成已授权 LongVALE `part_19` 的 SHA/CRC/member 审计；
4. 对 LLP 只先构建 metadata lock 和小规模 train/validation media pilot 计划，不解封 test；
5. 审计 hard-negative 合法性及旧 R0/R2/teacher 资产资格；
6. 输出修订后 S0 dataset qualification Review；
7. Review 通过后才允许 LLP pilot 媒体获取；LLP pilot 通过后才决定 full media，
   不因本计划修订自动下载 Clotho-AQA/AVCaps 或启动训练。
