# Dynamic Window Selector V2 计划与数据集整体 Review

日期：2026-07-25（Asia/Shanghai）

## 1. Review 结论

计划结论：`review_passed_with_conditions`。

数据集组合结论：`review_passed_with_conditions`。

当前执行范围：只允许 V0 数据冻结、contract 和小规模媒体可用率 pilot；不允许直接运行 V2 Oracle confirm、selector 训练或任何正式 test。

V2 计划满足“先上界、再确定性、再 selector-only、最后下游”的可证伪顺序，但存在明确外部有效性限制：主结论只覆盖稀疏事件与固定 K=8，不能推广到长事件或按比例预算。

## 2. 计划 Review

### 2.1 已解决的核心问题

`pass`：没有把 selector 直接接入联合训练。V1/V2 完全不训练；V3 只允许 `dynamic_window_selector.*`。

`pass`：Oracle 是唯一训练授权门禁。Oracle 不改善冻结 M4 时，明确停止 selector 并转向音频语义、答案表示和融合位置。

`pass`：策略矩阵覆盖 All、Uniform、Energy、Onset、cached Eventness、Oracle、Random 和条件 Temporal scorer。

`pass`：Temporal scorer fail-closed。历史失败资产不因计划需要而获得资格。

`pass`：主预算 K=8 在三个候选数据集均具有理论动态范围；K=16 明确降为成本曲线。

`pass`：定位能力与下游语义收益分开。UnAV 定位高分不能替代 LongVALE Oracle utility，QVHighlights/MUSIC-AVQA 也不能挽救主链。

`pass`：question-conditioned 训练包含 Audio-only、Question-only、shuffled-query 和 empty-query，能够排查文本捷径。

`pass`：ranking loss、optimizer、学习率、batch、epoch、early stopping、三训练 seed 和 checkpoint 选择已在首个 validation 前冻结。

`pass`：Semantic Contrast 延后到 selector 定位、causal replay 和 QA 下游全部通过之后，且要求 selector 冻结。

### 2.2 保留风险

`risk_scope`：`event_ratio <= 10%` 是根据已完成 N0 元数据分析确定的新研究域。它是合法的新假设，但属于 data-informed redesign；所有报告必须同时给出完整分布，不能称为通用 selector。

`risk_query`：UnAV event label 生成的问题是模板问题，不是开放域自然问题。V3 只能证明 event-label-conditioned ranking。

`risk_bridge`：LongVALE 的参考 caption 不能作为 selector query 后再用同一 caption 评价生成，否则答案泄漏。V2 中 selector 不读取 timestamp/caption；LongVALE 只检验 Oracle 音频效用。

`risk_final_data`：AVQA 媒体未完整，当前没有可立即执行的干净 QA 正式 test。V5 必须保持 blocked。

`risk_compute_claim`：cached Eventness 和 learned selector 依赖已编码音频，不能宣称节省 BEATs；计划已限制效率结论来源。

`risk_causality`：V1–V3 的离线排序不等于流式能力；只有 V4 strict-causal replay 通过后才能讨论在线接入。

### 2.3 计划最终判定

```text
contract_and_metadata_work_allowed: true
small_media_availability_pilot_allowed: true
oracle_effect_experiment_allowed: false  # 等待 V1 review
selector_training_allowed: false         # 等待 V2 confirm
formal_test_allowed: false
```

## 3. 数据集逐项 Review

### 3.1 LongVALE

官方来源：

- GitHub：`https://github.com/ttgeng233/LongVALE`
- 审计 commit：`2eb88ce3f72458faea6f3db8f841b41e6485a814`
- Hugging Face：`https://huggingface.co/datasets/ttgeng233/LongVALE`
- 许可：CC BY-NC-SA 4.0，学术/教育用途。

规模与结构：

- training：7,240 视频、91,863 事件；
- eval：1,171 视频、13,867 事件；
- 具有 duration、精确 timestamp 和 relation-aware caption；
- training/eval 物理视频隔离；
- K=8 理论 headroom：training 66.7700pp、eval 65.2887pp；
- 稀疏事件规模充足。

适合：

- 冻结 M4 的 Oracle-vs-All segment captioning；
- 长视频 source index/timestamp/cost 审计；
- 外部完整分布报告。

不适合：

- 直接用参考 caption 作为 query 后评估同一 caption；
- 单独证明开放域问题条件 selector；
- 商业用途。

判定：`qualified_for_V2_after_media_and_pollution_freeze`。

### 3.2 UnAV-100

官方来源：

- GitHub：`https://github.com/ttgeng233/UnAV`
- 审计 commit：`1609deba4fbaf25222c6ee6c7d43b0d0ddfab20d`
- Hugging Face：`https://huggingface.co/datasets/ttgeng233/UnAV-100`
- Dataset Card 许可：CC BY-NC-SA 4.0。

规模与结构：

- train：6,489 视频、18,123 事件；
- validation：2,134 视频、6,036 事件；
- test：2,167 视频、5,900 事件；
- 100 类、未裁剪、多事件、精确 temporal segment；
- 稀疏子集 train/val/test 分别为 7,408/2,448/2,294 事件；
- K=8 headroom 分别为 26.6184pp/26.5740pp/25.6231pp。

风险：

- 与本项目历史 AVE/Stage2 显式清单重叠 410 个物理视频；
- event label 模板只能构成受限问题；
- 完整分布包含大量长事件，比例预算会饱和。

处理：

- 410 个历史重叠视频从所有正式 split 排除；
- 排除后重新冻结规模与类别覆盖；
- 主结论限定稀疏事件，完整分布单列；
- test 只运行一次。

判定：`qualified_for_V1_V3_V4_after_exclusion_refreeze`。

### 3.3 QVHighlights

官方来源：

- GitHub：`https://github.com/jayleicn/moment_detr`
- 审计 commit：`b7e553ac3b0c898ee6b85e03ee507c064eab89ca`
- annotation：CC BY-NC-SA 4.0；
- code：MIT。

规模与结构：

- train：7,218 query、7,100 clip；
- val：1,550 query、1,519 clip；
- test：1,542 query、1,529 clip；
- 平均 clip 时长约 149.5 秒；
- train/val/test 的 clip 和源 YouTube ID 均无交叉；
- K=8 headroom：train 17.1506pp、val 17.9866pp、test 17.6636pp。

优点：

- 真实自然语言 query；
- moment window 与 saliency 标注；
- 物理视频 split 干净。

限制：

- 标注目标主要是视觉 moment retrieval，不保证问题依赖音频；
- 原始视频音轨存在率和可解码性尚未审计；
- test GT 已随官方仓库公开且本轮 review 已读取，不能称为未见 test。

判定：`qualified_auxiliary_after_audio_track_gate`。只能辅助检验自然 query 条件敏感性，不能放行主链或证明音频语义。

### 3.4 MUSIC-AVQA

官方来源：

- GitHub：`https://github.com/GeWu-Lab/MUSIC-AVQA_CVPR2022`
- 审计 commit：`cc8809514a2ee3d7ae81a3021c15e7c0679170d7`
- code：MIT；
- README 提供原始视频和 annotation 下载入口，但仓库未为数据媒体单独给出与代码同等明确的许可声明，正式使用前需记录数据使用条款。

updated annotation：

- train：31,927 QA；
- val：4,568 QA；
- test：9,129 QA；
- 合计 9,288 个 video ID；
- train-test 视频重叠 6,389；
- train-val 重叠 3,808；
- val-test 重叠 2,296。

优点：

- 含 Audio、Visual、Audio-Visual 问题；
- 有 counting、comparative、location、temporal 等类型；
- 适合 text-only、audio-mismatch、silence 和 gate=0 对照。

阻断：

- QA split 不是物理视频隔离；
- 本项目历史 Stage2 已触及 MUSIC-AVQA；
- 不具备新的未见 test 资格。

判定：`diagnostic_only_historical_and_split_overlap`。不得用于训练后正式泛化结论。

### 3.5 AVQA

官方入口：`https://mn.cs.tsinghua.edu.cn/avqa/`。

优点：

- 纯官方音视频问答；
- 能按 View、Sound、Both 分层；
- 适合最终问题条件下游收益。

当前阻断：

- 本地官方分片下载/解压/校验未完成；
- 物理 ID 与历史训练数据的污染交叉未完成；
- 当前不可运行 validation/test。

判定：`blocked_incomplete_media_and_pollution_audit`。它仍是 V5 首选，但不是当前可用数据。

### 3.6 AVE

官方 D1 已一次性访问且失败，Random Hit@4 饱和原因已经确认。

判定：`regression_only_frozen_test`。只用于 contract、All=Fixed 和历史复现，禁止产生新效果结论。

## 4. 数据集组合整体 Review

### 4.1 物理 ID 边界

本轮轻量元数据交叉结果：

- LongVALE 与 UnAV-100：0 重叠；
- LongVALE 与 QVHighlights：0 重叠；
- UnAV-100 与 QVHighlights：0 重叠；
- QVHighlights train/val/test：源 YouTube ID 互斥；
- MUSIC-AVQA 内部 split：严重重叠；
- AVQA：尚未完成。

上述“0 重叠”只针对当前可解析的官方物理 ID，不替代未来媒体 SHA 或 M4-IT/历史 harness 再审计。

### 4.2 职责覆盖

| 研究问题 | 主数据集 | 辅助数据集 | 是否当前就绪 |
| --- | --- | --- | --- |
| Oracle 定位上界 | UnAV-100 | LongVALE | 条件就绪 |
| 冻结 M4 Oracle 下游效用 | LongVALE | 无 | 等待媒体 pilot |
| event-label-conditioned 训练 | UnAV-100 | 无 | 等待排除重叠 |
| 自然语言 query 条件性 | QVHighlights | 无 | 等待音轨审计 |
| 正式音视频 QA 转化 | AVQA | MUSIC-AVQA 仅诊断 | blocked |

数据集组合能够覆盖从定位到下游的因果链，但当前缺少已经完全就绪的干净 QA 正式 test。因此整体 review 不能给出无条件通过。

### 4.3 最终数据集决策

```text
primary_oracle_utility: LongVALE
primary_localization_and_training: UnAV-100
natural_query_auxiliary: QVHighlights
qa_diagnostic_only: MUSIC-AVQA
qa_formal_gate: AVQA (blocked)
ave_status: regression_only
```

### 4.4 许可整体结论

该组合不能解释为“可无条件商用的开放数据”：

- LongVALE、UnAV-100 和 QVHighlights annotation 均带 CC BY-NC-SA 4.0 非商业限制；
- YouTube/第三方原始媒体仍可能受原始内容权利约束；
- MUSIC-AVQA 代码为 MIT，但数据媒体许可需在正式使用前另行确认；
- AVQA 的正式许可和下载条款必须随数据版本冻结。

因此 V2 当前只批准学术研究用途。任何商业训练、模型发布或重新分发媒体都必须另做法律/许可 review。

## 5. 下一步

严格按以下顺序执行：

1. 为 LongVALE/UnAV/QVHighlights 生成统一 `candidate_sources.json`、license SHA 和物理 ID registry；
2. 从 UnAV 所有 split 排除 410 个历史视频并重新冻结规模；
3. 只下载小规模 LongVALE training、UnAV train/validation 和 QVHighlights train 的媒体 pilot，审计音轨与缺失率；
4. 完成 V0 contract；
5. 运行 1 条、8 条单卡 smoke；
6. 完成 V1 validation 与 review；
7. 只有 V1 review 通过，才申请 V2 Oracle pilot。

当前不批准 selector 训练、正式 test 或 Semantic Contrast。
