# Dynamic Window Selector 低成本诊断与最小训练计划 V2

日期：2026-07-25（Asia/Shanghai）

## 1. 计划定位

本计划根据以下研究假设重新设计：

> 先把 Dynamic Window Selector 当作低成本、可证伪的诊断变量，而不是直接接入正式联合训练。先验证正确选窗是否存在定位上界和下游收益；只有 Oracle 有明确收益，才训练最小 question-conditioned selector；Oracle 无收益时立即停止 selector，转向音频语义目标、答案表示和融合位置。

本计划是独立 V2 路线，不修改以下冻结结论：

- AVE D1 official test 仍为 `test_failed`，禁止重跑或改判；
- 旧 N0 的“全事件分布 + 25% 主预算”路线仍为 `n0_failed_data_or_metric_incompatible`；
- 历史 AVE、Stage2、`dev760` 和 temporal scorer 暴露记录继续有效。

V2 的研究对象明确缩小为：

- 长视频中的稀疏音视频事件；
- 固定容量窗口记忆，而不是按视频长度保留 25%；
- 先证明选中正确音频窗口有用，再讨论学习式和问题条件选择。

旧 N0 已经显示固定 K 与比例预算回答的是不同问题。V2 使用新的假设、数据边界、目录和门禁，不用 V2 结果追认旧路线成功。

### 1.1 2026-07-25 主线状态校正

后续 review 发现，`checkpoint-150` 的音频选择性失败只否决“使用该 checkpoint 运行
LongVALE Oracle 下游效用”，不能否决不依赖 M4 fusion 的 V1 定位上界。因此当前状态按
阶段拆分为：

```text
V0 contract/data review: PASS（媒体资格仍按数据集单独判断）
V1 localization upper bound: ACTIVE / media-gated
V2 M4 Oracle downstream utility: BLOCKED_BY_AUDIO_PATH
V3 learned selector: NOT_AUTHORIZED
audio semantic reconstruction: CONDITIONAL_BLOCKER_REMEDIATION
```

主执行入口仍是本计划。音频语义重构计划不是替代路线；只有 V1 证明 Oracle 定位 headroom
存在、而 V2 又因冻结音频路径不合格无法解释时，才允许用它产出一个新的、独立 review 的
冻结音频 checkpoint。若 V1 Oracle 本身没有定位 headroom，应直接停止 selector，不应先训练
音频语义模块来“挽救”定位假设。

## 2. 核心决策

### 2.1 主预算

主预算固定为 `K=8` 个 1 秒 micro-window，补充报告 `K=4`；`K=16` 只作为成本曲线，不参与晋级。

选择依据来自已完成的只读元数据审计：

| 数据集 | K=8 Oracle 理论 headroom | K=16 Oracle 理论 headroom |
| --- | ---: | ---: |
| LongVALE training | 66.7700pp | 50.9476pp |
| UnAV-100 train | 26.6184pp | 12.2654pp |

`K=8` 在两个已验证候选集合上均有可辨识性。固定 K 也对应未知总时长时的固定容量 memory，避免比例预算依赖完整视频时长。

### 2.2 目标域

主结论只针对 `event_duration / video_duration <= 10%` 的稀疏事件。该定义是在启动任何 V2 模型实验之前冻结，且规模足够：

| 数据集/split | 稀疏事件数 | 涉及物理视频 |
| --- | ---: | ---: |
| LongVALE training | 75,900 | 6,468 |
| LongVALE eval | 11,217 | 1,041 |
| UnAV-100 train（去除 410 个历史暴露视频，按 `(video_id,event_label)` 合并 occurrence） | 1,380 | 1,039 |
| UnAV-100 validation（同上） | 443 | 335 |
| UnAV-100 test（同上） | 455 | 336 |

所有运行必须同时报告完整分布结果，避免把稀疏域结论外推到长事件。完整分布只作外部有效性报告，不阻断稀疏域主门禁。

### 2.3 数据职责

- LongVALE：Oracle 音频选窗能否改善冻结 M4 的 segment captioning；
- UnAV-100：确定性定位和受限 event-label query 的 question-conditioned selector 主要训练/验证；
- AVE：只保留 contract/回归，不再产生效果结论。

不同数据集不混合训练样本、不加权总分，每个阶段只使用指定数据集回答一个问题。

QVHighlights、MUSIC-AVQA 和 AVQA 已从本计划删除：前者不能证明音频 selector，MUSIC-AVQA 的物理视频 split 严重重叠且已有历史污染，AVQA 当前媒体与官方指标链未完成可执行验证。它们不得作为辅助结果、正式门禁或失败后的替代数据。

## 3. 总体决策树

```text
V0 Contract 与数据资格
  ├─ fail：修 harness/数据，不运行效果实验
  └─ pass
       ↓
V1 确定性与 Oracle 定位上界
  ├─ Oracle 无 headroom：停止 selector
  └─ Oracle 有 headroom
       ↓
     冻结音频路径资格检查
       ├─ fail：只进入音频语义 blocker 修复；不得运行 V2
       └─ pass
            ↓
V2 冻结 M4 的 LongVALE Oracle 下游效用
  ├─ Oracle 无下游收益：停止 selector，转向语义/表示/融合
  └─ Oracle 有明确收益
       ↓
V3 最小 question-conditioned selector-only 训练
  ├─ query 条件性或音频增量失败：停止 learned selector
  └─ pass
       ↓
V4 strict-causal replay 与一次性定位确认
  ├─ fail：不接入 M4
  └─ pass：仅确认受限的 event-label query-conditioned selector 路线
```

V4 通过不等于已经证明开放域 QA 收益。问题条件下游和 Semantic Contrast 必须另立新计划，并重新审查一套完整、干净、可运行的 QA 数据；不属于当前 V2。

这里的“音频路径资格检查”只用于保证 V2 可解释，不改变用户给定的主假设顺序：先证明
selector 定位上界，再证明 Oracle 下游效用，最后才训练 question-conditioned selector。

## 4. V0：Contract、数据与资产资格

### 4.1 统一窗口 contract

- 原子单位固定为原始 1 秒 scene-audio micro-window；
- 保存显式 `selected_source_indices` 和绝对 timestamp；
- 选中集合进入后续模块前按时间排序；
- `K=0` 拒绝，`K>N` 固定为 `min(K, N_valid)`；
- tie-break 固定选择更早窗口；
- silence、全 padding、尾窗和非整数 duration 行为固定；
- V2 专用切窗固定 `window_sec=1.0`、`hop_sec=1.0`、`pad_short=true`；partial tail tensor 补齐到 1 秒，但 `end_sec` 截断到真实媒体时长，并保存 `valid_sample_count`；
- All 与原 Fixed 路径逐元素一致；
- `enable_scene_audio=0` 和强制 `gate=0` 行为回退不变；
- 全部输出有限，推理前后非训练参数 SHA 不变；
- Random 使用五个固定 seed。

### 4.2 数据冻结

LongVALE：

- 只从官方 training 中按物理视频划分互斥 `oracle_dev`、`oracle_pilot`、`oracle_confirm`；
- `oracle_dev` 不少于 50 视频/100 事件；
- `oracle_pilot` 不少于 100 视频/500 事件；
- `oracle_confirm` 不少于 200 视频/1,000 事件；
- official eval 在 V3/V4 结构、checkpoint 和全部阈值冻结前不运行预测。
- 官方 training JSON schema 固定映射为 `video_id -> duration + segments{"start_end": caption}`；
- 官方 eval JSON schema 固定映射为 `video_id -> duration + timestamps[[start,end]] + sentences[]`；
- 逐条审计结果为 training 7,240 视频/91,863 事件、eval 1,171 视频/13,867 事件，非法记录均为 0；
- 官方 `seg_captioning` 预测记录必须包含 `video_id`、`task="seg_captioning"`、`answer` 和 `info.sentence_id`；
- 官方 golden fixture 已实际跑通 BLEU-4、METEOR、ROUGE-L、CIDEr；源码输出键 `Rouge` 在报告层规范化为 `ROUGE-L`，只允许改字段名，不得改实现或分数；
- M4 环境已固定 `pycocoevalcap==1.2` 与 `pycocotools==2.0.11`，版本清单位于 `intersuit/configs/dynamic_selector_v2_metric_requirements.txt`；因 Conda site-packages 在 Codex 沙箱只读，adapter 仅把 PTBTokenizer 临时输入移到 `/tmp`，jar、Java 参数、token filtering 和四项评分公式均不变。无额外 `PYTHONPATH` 的官方 golden 已通过。

UnAV-100：

- 使用官方 train/validation/test；
- 排除已在历史 AVE/Stage2 清单中出现的 410 个物理视频；
- sample unit 固定为 `(video_id,event_label)`；同视频同标签全部 occurrence 合并成 `target_intervals` union，positive 与任意 occurrence 重叠，negative 与全部 occurrence 均不重叠；
- 去污染后冻结规模：train 9,948 samples/6,229 videos/17,477 annotations，validation 3,337/2,052/5,830，test 3,352/2,099/5,730；类别数为 100；
- 去污染后的稀疏 union-ratio `<=10%` 样本为 train/validation/test = 1,380/443/455；旧的 7,408/2,448/2,294 是 annotation-level 口径，已作废；
- 排除 ID、label mapping 与三份 manifest 必须逐个校验 SHA；当前冻结值记录于 `docs/DYNAMIC_WINDOW_SELECTOR_V2_PLAN_DATASET_REVIEW_ZH.md`；
- test 在 V3 validation 与 review 通过前不运行预测。
- 官方仓库 JSON schema 固定映射为 `database -> video_id -> subset/duration/annotations[]`，annotation 包含 `segment[start,end]`、`label`、`label_id`；
- 官方 Hugging Face parquet 字段固定为 `video_id`、`duration`、`timestamp[start,end]`、`event_label`；
- parquet 逐条审计结果为 train 18,123、validation 6,036、test 5,900 条，非法记录均为 0；
- parquet 不含 `label_id`，manifest 冻结时必须按排序后的唯一 `event_label` 建立稳定映射，并对 GT/pred 共用同一映射；
- selector 主指标 Event Coverage@K、Positive-window Recall@K、mean best tIoU 直接从 timestamp 计算，不依赖类别 ID；
- 官方 class-aware temporal detection mAP 只作辅助指标，tIoU 阈值固定为 0.1–0.9；
- 官方 evaluator 在当前 NumPy 原样运行会因已删除的 `np.float` 失败；经等价兼容替换 `np.float -> float` 后，完美预测 golden fixture 在全部九个 tIoU 阈值均得到 mAP=1.0；
- 正式适配必须把这一兼容补丁纳入测试并锁定 SHA，禁止改变 AP/tIoU 公式。兼容测试未通过时不得运行 V1/V3/V4。

### 4.3 条件资产门禁

Temporal scorer 当前仍为：

```text
diagnostic_failed
schema_incompatible
missing_inputs
formal_eligible=false
```

因此 V1–V4 默认省略 `temporal_high_conf_topk`，记录为 `omitted_ineligible_asset`。不得复用历史 20 个 hop=0.5 秒窗口分数，也不得插值为 10 个或其他窗口。

如确需 Temporal scorer，必须单立 T0-V2 支线：

- 仅使用当前阶段合格 train 数据；
- 窗口 schema 与 1 秒 micro-window 完全一致；
- 输入模型、维度、预处理和归一化冻结；
- 独立同步/偏移任务先通过验收；
- 训练数据与所有正式 confirm/test 物理隔离；
- T0-V2 成功只能加入尚未运行的阶段，不重开已完成阶段。

## 5. V1：确定性与 Oracle 定位上界

### 5.1 数据

主数据使用经污染排除后的 UnAV-100 train/validation 稀疏事件。LongVALE `oracle_dev` 只用于确认同一 harness 可处理长视频 caption 边界。

### 5.2 策略矩阵

核心必跑：

- `all`；
- `uniform_topk`；
- `energy_topk`；
- `onset_topk`；
- `eventness_cached_topk`；
- `oracle_topk`；
- `random_topk`，五个固定 seed。

条件策略：

- `temporal_high_conf_topk`：仅在新资产通过资格门禁时运行，否则省略；
- `legacy_multiscale_dynamic`：只做代码回归，不进入排行榜。

所有 Top-K 使用同一 `K=8`；`K=4` 补充，禁止阈值后少于 K 的策略混入固定 K 排行。

### 5.3 指标

- Event Coverage@K；
- Positive-window Recall@K；
- mean best tIoU；
- 首个命中排名；
- Oracle headroom recovery；
- 窗口/token 保留率；
- waveform/BEATs/projector/fusion 的实际计算量。

定位主指标按事件计算，统计使用物理视频 cluster paired bootstrap，固定 10,000 次、seed `20260724`。

### 5.4 通过条件

必须同时满足：

1. Oracle 相对 Random 均值的 Event Coverage 提升至少 20pp；
2. Oracle−Random 的 95% CI 下界大于 0；
3. 至少一个非 Oracle 确定性策略相对 Random 提升 5pp，且胜过至少 4/5 个 seed；
4. 该确定性策略的 cluster bootstrap 95% CI 下界大于 0；
5. All=Fixed、source index、timestamp、参数 SHA 和双次复现全部通过。

Oracle 未通过时停止 selector。确定性策略失败但 Oracle 通过时允许进入 V2，但明确记录“存在定位上界，当前规则策略无效”。

## 6. V2：冻结 M4 的 Oracle 下游效用

本阶段是 selector 是否值得训练的唯一下游 Oracle 门禁。

### 6.1 数据与输入公平性

- 只使用 LongVALE official training 内互斥的 `oracle_pilot` 和 `oracle_confirm`；
- 目标事件满足稀疏域定义；
- prompt 对所有方法完全相同：`Describe the audio-visual event between <start> and <end> seconds.`；
- timestamp 对所有方法的语言模型输入可见；
- 非 Oracle selector 不得读取 timestamp 数值；
- 视觉窗口、问题 token、解码和非音频输入逐样本一致；
- 只有 scene-audio 窗口集合变化；
- 全部属于 `noncausal_upper_bound`，记录完整 clip lookahead。

### 6.2 冻结模块

候选基座锁定为：

```text
intersuit/checkpoints/AS-M4-BEATs-semantic-contrast-canary150-v1/checkpoint-150
lock: intersuit/configs/dynamic_selector_v2_checkpoint_lock.json
checkpoint_locked: true
oracle_eligible: false
audio_path_selectivity_gate_status: failed
v2_oracle_utility_allowed: false
```

lock 已固定 config、model index、4 个 safetensors shard、BEATs SHA，以及 dev760 五条件预测 SHA。该 checkpoint 在 gate=0.005 下得到 video-only/correct/mismatched = 254/251/259，正确音频低于错配和 video-only，selectivity 门禁正式失败。Fixed Stage2 checkpoint 同样失败（254/255/259）；12k/32k M4 baseline 不含 scene-audio/projector/fusion 权重，不能作为等价替代。当前仓库内没有合格冻结基座，因此本轮 V2 Oracle utility、LongVALE 媒体 pilot、selector 训练和 GPU smoke 全部停止。

全部冻结且 `eval()`：

- M4/Qwen2 与 `lm_head`；
- 视觉塔；
- BEATs；
- projector/resampler；
- fusion 与 gate；
- Event Aligner、Offset 和 learned Gate。

不创建 optimizer，不训练任何参数。

### 6.3 对照

- text-only；
- All audio windows；
- Uniform K=8；
- Energy K=8；
- Onset K=8；
- cached Eventness K=8；
- Random K=8 五 seed；
- Oracle K=8；
- Oracle-mismatch 五个固定一一映射；
- qualified 时的 Temporal K=8。

### 6.4 Pilot 早停

必须同时满足：

1. Oracle 相对 All 的逐事件 METEOR 至少提高 0.01；
2. Oracle 相对 Random 均值至少提高 0.01；
3. 正确音频 Oracle 高于五个 mismatch 均值；
4. Oracle 高于 text-only；
5. 不新增空输出、首 token EOS、重复文本或非有限输出。

任一失败立即停止 selector，不解封 confirm。

### 6.5 Confirm 门禁

必须同时满足：

1. Oracle 相对 All 的 METEOR 至少提高 0.02，cluster bootstrap 95% CI 下界大于 0；
2. Oracle 相对 Random 均值至少提高 0.02，且胜过至少 4/5 seed；
3. 正确 Oracle 相对 mismatch 均值至少提高 0.02，CI 下界大于 0；
4. Oracle 相对 text-only 至少提高 0.03，CI 下界大于 0；
5. BLEU-4、ROUGE-L、CIDEr 至少两个与 METEOR 同方向；
6. 无新增输出异常；
7. token-budget preflight 与参数 SHA 审计通过。

Confirm 失败时，结论固定为“正确选窗不能改善当前 M4 下游语义”，转向音频语义目标、答案表示和融合位置。

## 7. V3：最小 question-conditioned selector-only 训练

只有 V2 confirm 通过才启动。

### 7.1 训练任务

主训练数据为经污染排除后的 UnAV-100 official train 稀疏事件。

将官方 `event_label` 冻结成三个预注册问题模板，例如：

```text
When does <event_label> occur?
Which audio window contains <event_label>?
Locate the moment containing <event_label>.
```

模板只用于形成 query，不生成答案、不修改正窗口。每个物理视频的模板选择由固定 hash 决定。实现模块是通用 `QuestionConditionedWindowSelector`，但本阶段 query 只来自 event label，因此结论只能称为 `event-label query-conditioned ranking`，不能伪装成开放域 QA。

### 7.2 参数白名单

只允许：

```text
model.streaming_av_module.question_conditioned_window_selector.*
```

冻结：

- M4/Qwen2、`lm_head`；
- 视觉塔、BEATs；
- projector/resampler；
- fusion/gate；
- Event Aligner/Offset；
- 其他 `streaming_av_module` 参数。

第一轮读取冻结的逐窗口音频缓存，只优化 ranking loss，不使用 LM loss。启动时打印全部可训练参数；白名单按完整参数名后缀匹配 `streaming_av_module.question_conditioned_window_selector.*`，出现白名单外参数或可训练参数量为 0 立即失败。旧 `dynamic_window_selector` 是无参数 cached-eventness baseline，严禁作为 learned selector 训练。

最小 scorer 结构固定为：

- 音频窗口表示与 query embedding 分别通过 selector 内部线性投影；
- L2 normalization 后计算逐窗口 dot-product；
- 可拼接冻结的 energy/onset 标量，但不得读取视觉特征、答案或目标 timestamp；
- 不增加新的 projector、fusion、gate 或语言模型 adapter；
- Top-K 只在有效窗口 mask 内执行。

第一轮 loss 固定为同视频 pairwise margin ranking：

\[
L_{\text{rank}} = \max(0, m - s_{\text{positive}} + s_{\text{negative}})
\]

- positive 为与目标事件有正重叠的窗口；
- negative 为同一视频内无重叠窗口；
- margin 固定为 `0.2`；
- 每个 positive 固定采样 4 个 negative，其中 2 个随机、2 个当前 batch hard negative；
- 不加入 LM loss、Semantic Contrast、类别分类、边界回归或跨数据集 loss。

训练配置在首个 validation 结果前冻结：

```text
optimizer: AdamW
learning_rate: 1e-4
weight_decay: 0.01
global_effective_batch_size: 128 events
micro_batch_size: 32 events
gradient_accumulation_steps: 4
world_size: 1
max_epochs: 20
early_stopping_metric: validation Event Coverage@8
early_stopping_patience: 3
max_grad_norm: 1.0
precision: bf16（硬件不支持时 fp32，必须在首轮前冻结）
training_seeds: 20260720, 20260721, 20260722
```

全局有效 batch 固定按 `micro_batch_size × gradient_accumulation_steps × world_size = 128` 计算。单卡物理 GPU 1 为默认训练设备；只有实测显存或吞吐不足并完成 review 后才允许使用物理 GPU 2、3、4 的普通 DDP，切换 world size 时必须同步调整 micro batch 或 accumulation，不能把全局 batch 静默放大。不得为了改变结果切换 seed、loss 或 checkpoint 选择。

每个 seed 独立训练并保留其 validation-best checkpoint。正式门禁报告三 seed 的逐 seed 与均值；至少 2/3 seed 必须通过方向性条件，不能只挑最高 seed。official test 一次性运行三个预注册 checkpoint，不在 test 后选主 seed。

### 7.3 输入和对照

训练 selector 输入只包含：

- 冻结的 query embedding；
- 冻结的逐窗口音频表示；
- window mask、source index 和 timestamp；
- 不包含官方目标边界。

对照：

- Random 五 seed；
- V1 锁定确定性策略；
- Audio-only selector；
- Question-only selector；
- Audio+Question selector；
- shuffled-query；
- empty-query；
- Oracle。

### 7.4 Validation 门禁

在 UnAV-100 official validation 稀疏事件、K=8 下必须同时满足：

1. Audio+Question Event Coverage 高于 Random 均值 10pp；
2. 高于 Audio-only 5pp；
3. 高于 Question-only 5pp；
4. 正确 query 高于 shuffled query 5pp，cluster bootstrap 95% CI 下界大于 0；
5. 正确 query 高于 empty query 5pp；
6. Question-only 不得达到 Audio+Question 的 90%；
7. Oracle headroom recovery 至少 0.30；
8. 平均保留窗口不超过 8；
9. 全部非 selector 参数训练前后 SHA 不变。

第 1–5 条同时要求三 seed 均值满足阈值、至少 2/3 seed 单独方向一致；第 1–4 条的物理视频 cluster bootstrap 95% CI 下界必须大于 0。

任一失败停止 learned selector，不通过解冻 projector、fusion 或语言模型补救。

## 8. V4：strict-causal replay 与一次性定位确认

V3 validation 通过后冻结 checkpoint、K、阈值和全部对照。

先在 UnAV validation 执行 strict-causal replay：

- 当前时刻只能读取 `timestamp.end <= t` 的窗口；
- 固定容量 K=8 memory；
- causal state 必须保存 retained features、scores、timestamps、source indices、mask 和独立 `next_source_index`；不得从已裁剪的 Top-K 最大索引反推下一窗口编号；
- 禁止完整 clip 排序后伪装成 causal；
- 在目标事件结束时结算主指标，视频结束时结果仅补充；
- 未来窗口扰动不得改变当前输出。

strict-causal validation 必须再次满足 V3 第 1–6 条，才允许一次性运行 UnAV official test。test 必须满足同样门禁，且不得根据 test 重选 checkpoint 或模板。

## 9. 后续问题条件下游与 Semantic Contrast

当前 V2 不包含开放域 QA 正式门禁，也不包含 Semantic Contrast 实现。只有 V3 和 V4 全部通过，且未来另有一套官方仓库、媒体、schema、指标、许可、物理 split 与历史污染均完成可执行 review 的 QA 数据，才允许启动独立计划：

- selector checkpoint 完全冻结；
- 正样本为选中窗口与 question-answer 联合表示；
- 负样本优先使用同视频非答案窗口，再使用跨视频语义相似事件；
- silence 只作鲁棒性对照，不作主要语义负样本；
- 第一轮只训练 Semantic Contrast 新参数；
- 不同时解冻 selector、fusion、projector 或语言模型；
- 使用新的 train/validation/test 边界，禁止复用任何已访问 test 调参。

如果没有满足上述要求的数据集，则停在 V4 的受限结论，不得用 QVHighlights、MUSIC-AVQA、AVQA 或 AVE 补位。

## 10. 运行与算力顺序

所有 Python 命令使用 `conda run -n M4`。GPU 命令显式映射：

1. CPU contract 与元数据审计；
2. 单卡 GPU 1：1 条样本；
3. 单卡 GPU 1：8 条 smoke；
4. 单卡 GPU 1：V1/V2 pilot；
5. 只有 selector 训练需要多卡时，优先 `CUDA_VISIBLE_DEVICES=2,3,4`；
6. 正式训练前重新执行 preflight；
7. 每次 GPU 运行记录开始时间、配置、stdout/stderr、功率和内核异常。

第一轮训练使用冻结缓存，禁止为了“省计算”宣称减少 BEATs；只有 Energy/Onset 等前置策略可以报告 BEATs 编码窗口减少。

## 11. 产物、提交与停止规则

独立目录：

```text
intersuit/harness/artifacts/dynamic_selector_v2_v0/
intersuit/harness/artifacts/dynamic_selector_v2_v1/
intersuit/harness/artifacts/dynamic_selector_v2_v2/
intersuit/harness/artifacts/dynamic_selector_v2_v3/
intersuit/harness/artifacts/dynamic_selector_v2_v4/
```

每阶段保存来源、commit、license、manifest SHA、污染报告、锁定配置、逐样本结果、summary、中文报告和 review。

提交边界：

1. V0/V1 harness 与数据适配；
2. V2 LongVALE Oracle utility；
3. V3 selector-only 训练入口；
4. V4 causal replay；
5. Temporal scorer 如未来重新获得资格，单独提交。

checkpoint、媒体、数据集、特征、缓存、预测和日志不得加入 Git。

任何阶段出现以下情况立即停止：

- Oracle 无定位 headroom；
- Oracle 不改善冻结 M4；
- correct query 不高于 shuffled/empty；
- Audio+Question 不高于 Question-only/Audio-only；
- 非 selector 参数发生变化；
- test、媒体、split 或门槛在结果查看后被修改；
- 通过解冻其他模块来挽救 selector。

## 12. 当前执行授权

本计划的独立 review 见 `docs/DYNAMIC_WINDOW_SELECTOR_V2_PLAN_DATASET_REVIEW_ZH.md`，当前结论为 `review_completed_route_stopped`。

当前只允许：

1. V0 CPU contract、数据/许可/污染冻结和回归测试；
2. 保留 selector 模块作为未来合格音频基座的诊断基础；
3. 另立音频语义目标、答案表示和融合位置重构计划。

本轮不再等待 V1：checkpoint selectivity 前置门禁已经失败，停止规则已触发。只有未来产生新的、物理隔离且通过正确音频高于错配音频门禁的冻结基座，才允许重新申请媒体 pilot 与 V1/V2；不得修改本次负结果。

```text
selector_training_allowed: false
longvale_media_pilot_allowed: false
gpu_smoke_allowed: false
formal_test_allowed: false
semantic_contrast_allowed: false
current_route: stopped_checkpoint_ineligible
```
