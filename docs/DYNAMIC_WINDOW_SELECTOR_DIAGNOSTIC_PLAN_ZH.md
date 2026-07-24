# Dynamic Window Selector 单链路排错与可证伪计划

日期：2026-07-24（Asia/Shanghai）

## 1. 结论

本轮确实是“先测试”，但不是随意跑几个数据集，而是像排查一个系统性 bug 一样，沿一条固定工作链逐层定位：

```text
接口与索引正确性
→ 短视频 dense 标注正确性
→ 未裁剪多事件视频的 Oracle 上界
→ question-conditioned selector 单模块训练
→ 纯官方音视频问答的下游收益
→ 专项同步鲁棒性
```

每一层只回答一个问题；上一层未通过，下一层不得启动。

本计划禁止：

- 把 AVE、AVUT、MUSIC-AVQA 等样本拼成一个训练、验证或测试集合；
- 把不同数据集的指标加权成一个总分；
- 用已经反复查看过结果的 `AS_M4_STAGE2`、`dev760` 或本地冻结集合做正式测试；
- 在一个阶段同时修改 selector、projector、fusion、Gate、Event Aligner 和语言模型；
- Oracle 失败后继续调 K、阈值或 scorer，直到“调出提升”。

## 2. 现有数据审计

### 2.1 明确排除的集合

| 集合 | 审计结果 | 本计划处理 |
| --- | --- | --- |
| `AS_M4_STAGE2` | train/dev/reserve 均混合 AVE、AVUT、MUSIC-AVQA | 只保留为历史记录，不进入新门禁 |
| `dev760` | 已用于 Fixed BEATs、Semantic Contrast 等多轮正式评测 | 不再作为未见测试集 |
| `AVE_HF_*` 本地整理集 | 共触及 2500 个物理视频，且已用于 scorer、Stage2 或划分筛选 | 只可做代码回归，不报告新效果结论 |
| AVUT 本地集合 | 691 个官方视频中，Stage2 已触及 424 个；本地 frozen/final/smoke 又触及 243 个 | 不作为正式主数据集 |
| AVUT 真正未见部分 | 只剩 24 个视频、59 个 QA，其中 Audio Event Location 仅 4 题 | 样本过小，不足以支撑主结论 |
| AVQA 当前下载 | 官方视频分片尚未完成下载和校验 | 完成完整性审计前禁止评测 |

### 2.2 可保留的纯官方 AVE 测试资产

AVE 官方标注共有 4143 条，官方划分为：

| 官方划分 | 标注条数 | 具有局部时间边界 | 排除本地已触及物理视频后 | 其中局部时间边界 |
| --- | ---: | ---: | ---: | ---: |
| Train | 3339 | 1108 | 812 | 255 |
| Validation | 402 | 144 | 392 | 143 |
| Test | 402 | 141 | 394 | 140 |

因此，在尚未完成 M4 训练污染交叉审计前，新链路有以下候选池：

- 最多 392 个未触及的官方 validation 物理视频做开发；
- 最多 394 个未触及的官方 test 物理视频做一次性确认；
- localization 主指标只在其中 143/140 个局部事件样本上计算；
- 全片事件样本只用于接口稳定性、silence、长度和输出有效性检查，不进入 Oracle 上界主指标。

上述数字还要减去与 M4-IT 和历史 AS-M4 训练资产重叠的物理视频，最终数量以污染审计后冻结的 manifest 为准。AVE 官方 annotation 中存在重复 YouTube ID，train 与 validation/test 有少量物理视频交叉。所有新 manifest 必须按 YouTube ID 物理去重，而不能只按 annotation 行号去重。

## 3. 纯数据集工作链

每个阶段使用一个官方数据集，不混样本、不混 split、不混指标。

| 阶段 | 唯一数据源 | 唯一职责 | 官方入口 |
| --- | --- | --- | --- |
| D0 | 合成张量与固定本地样本 | 查接口、mask、索引、缓存和回退 bug | 不涉及数据集 |
| D1 | AVE 官方原始划分 | 查 1 秒窗口映射和确定性策略实现 bug | [AVE 官方仓库](https://github.com/YapengTian/AVE-ECCV18) |
| D2 | UnAV-100 官方原始划分 | 判定未裁剪多事件视频是否存在可利用的选窗上界 | [UnAV-100 官方项目](https://unav100.github.io/) |
| D3 | LongVALE 官方原始划分 | 训练并检验 question-conditioned selector | [LongVALE 官方仓库](https://github.com/ttgeng233/LongVALE) · [官方数据](https://huggingface.co/datasets/ttgeng233/LongVALE) |
| D4 | AVQA 官方原始划分 | 检验选窗能否转化为纯官方下游问答收益 | [AVQA 官方项目与下载](https://mn.cs.tsinghua.edu.cn/avqa/) |
| D5 | VGGSound-Sparse 官方集合 | 独立检验稀疏同步和人工 offset 鲁棒性 | [SparseSync 官方仓库](https://github.com/v-iashin/SparseSync) |

UnAV-100 官方项目提供 raw video 清单、train/validation/test split、annotations、features 和 evaluation scripts。若官方代码没有独立 GitHub 仓库，只记录项目页下载版本、文件 SHA 和获取日期，不用非官方镜像替代。

AVQA 官方入口是项目页而非 GitHub 仓库。正式数据只能来自该入口；Hugging Face 二次打包版不能作为主结果来源。

## 4. D0：先修链路，不测效果

### 4.1 当前已知实现风险

1. 当前 `eventness` 包含 encoded feature change，必须先运行 scene-audio encoder；它不是 BEATs 前置低成本 selector。
2. 当前 `SceneAudioEncoderOutput` 与 `DynamicWindowSelectorOutput` 保存 timestamps，但没有原始窗口 position ID。
3. 语言模型输入的 position ID 后续会连续重建；现阶段不能声称“保留了不连续窗口的原始 position ID”。
4. 当前训练入口只能解冻整个 `streaming_av_module` 或 fusion，不能保证只训练 selector。
5. 现有 Stage2 scorer 和 `dev760` 已被多次观察，存在测试集反馈污染。
6. 单一 mismatch 映射不足以排除偶然性。
7. 不同长度视频若只报告固定 K，会把视频长度偏差误当成 selector 能力。
8. Oracle 时间边界若参与普通策略打分，会造成目标泄漏。
9. 当前 dynamic 实现会把 1/2/4 秒多尺度区间池化成候选，再做 NMS；它与“直接选择 K 个原始 1 秒窗口”不是同一个实验单位，不能混在同一排行榜。
10. LongVALE、UnAV-100、AVQA 和历史 M4 训练清单都可能共享 YouTube 物理视频；只看数据集名称不能证明测试独立。

### 4.2 必须先通过的单元测试

- 有效窗口、padding、全 padding 和 silence；
- `K=0`、`K=1`、`K>N`；
- tie-break 固定选择更早窗口；
- 选中集合按原始时间重新排序；
- source index 与绝对 timestamps 全程不变；
- Oracle overlap 与边界邻近补齐；
- Random 五个固定 seed 可复现；
- mismatch 五个固定一一映射可复现，禁止同视频和同音频；
- cache miss、重复 key、损坏 cache 和输入 SHA 变化；
- `all` 与现有 Fixed 路径逐元素一致；
- `enable_scene_audio=0` 与 `gate=0` 行为回退；
- 所有输出无 NaN/Inf；
- 全部模型参数 SHA 在推理前后不变。

现阶段只承诺保留 `source_index + absolute_timestamp`。如果后续要研究 gap-aware position encoding，必须作为独立研究变量另开阶段，不能偷偷并入 selector。

正式 harness 的原子选择单位固定为 scene-audio 的原始 micro-window，默认 1 秒。Energy、Onset、cached Eventness、Temporal scorer、Random 和 Oracle 都只能对同一组 micro-window 排名。现有多尺度 pooled interval 实现改名为 `legacy_multiscale_dynamic`，只做历史回归，不进入 D1–D4 主门禁。

### 4.3 D0 退出条件

所有单元测试与 8 条单卡 smoke 全部通过。任何失败只修 harness，不下载新数据、不运行效果实验。

## 5. D1：AVE 官方纯数据实现门禁

### 5.1 数据冻结

1. 从 AVE 官方 annotation 和 order 文件生成 train/validation/test 物理视频清单。
2. 以 YouTube ID 去除跨 split 的物理重复。
3. 排除所有 `AVE_HF*`、Stage2 和历史 scorer 已触及视频。
4. 392 个 validation 和 394 个 test 只作为候选上限；完成 M4 训练污染审计后再冻结最终数量。
5. 下载完成后冻结媒体缺失清单、manifest SHA、annotation SHA 和官方仓库 commit SHA。
6. test manifest 在 validation 调试结束前保持封存。
7. 对 M4-IT、历史 AS-M4 清单和 AVE 官方 test 做 YouTube ID 交叉审计；重叠样本不得进入未见主结果。

### 5.2 策略矩阵

| 策略 | 定义 | 能否节省 BEATs 计算 |
| --- | --- | --- |
| `all` | 全部有效窗口 | 否 |
| `uniform_topk` | 时间均匀选择 | 是 |
| `energy_topk` | waveform RMS 排序 | 是 |
| `onset_topk` | waveform onset 排序 | 是 |
| `eventness_cached_topk` | encoded feature change + energy + onset | 否 |
| `temporal_conf_cached_topk` | 冻结 Temporal scorer margin 排序 | 否 |
| `oracle_topk` | 与官方事件边界重叠时长排序 | 仅上界 |
| `random_topk` | 五个固定 seed | 对照 |

不得再把 `eventness_cached_topk` 写成“前置压缩”或“端到端效率方案”。

### 5.3 评测协议

- validation 可用于修 harness 和冻结确定性阈值；
- test 只运行一次；
- 主预算 `K=4`，补充 `K=2/8`；
- 所有固定 K 策略统一选择 `min(K, N_valid)`；
- localization 只在局部边界样本计算；
- 报告 Window Hit@K、正窗口 Recall@K、首个命中排名、选中窗口与目标段的最大 tIoU；
- Random 报告五个 seed 的逐 seed、均值和标准差；
- Oracle 与 Random 的差使用 paired bootstrap 95% 置信区间；
- All 只作为完整上下文和成本参考，不参与固定 K 的 Recall 公平比较。

### 5.4 D1 退出条件

1. `all` 精确复现 Fixed；
2. Oracle 的 Window Hit@K 为 100%；
3. Oracle 明显高于五个 Random seed，paired bootstrap 95% 置信区间下界大于 0；
4. 所有策略 timestamps、source index、窗口数和压缩率审计正确；
5. test 没有因下载失败而改变预注册阈值或样本定义。

D1 只证明 harness 正确，不证明动态窗口适合长视频，也不证明最终 QA 会提升。

## 6. D2：UnAV-100 纯官方上界门禁

这是决定 selector 路线是否值得继续的核心阶段。

### 6.1 数据顺序

1. 先获取官方 annotation、split、evaluation script 和特征清单。
2. 只在官方 train/validation 上做 100 视频接入 pilot。
3. pilot 覆盖短事件、长事件、多事件和重叠事件，但不得从 test 挑“好看的样本”。
4. 接入通过后扩展官方 validation。
5. 所有策略、预算、指标和阈值冻结后，官方 test 只运行一次。
6. 记录下架视频，但不得用其他数据集补足样本数。
7. 与 M4-IT、历史 AS-M4 和 AVE D1 清单做物理视频交叉审计；官方 test 全量结果与去重结果分开，去重结果为主。

### 6.2 评测单元

评测单元固定为 `(video_id, target_event_id)`：

- Energy、Onset、cached Eventness、Temporal scorer 和 Random 对同一视频只生成一份与 target event 无关的排名；
- 每个 target event 分别计算是否被该排名的预算窗口覆盖；
- Oracle 可以使用当前 target event 的官方边界，但只能作为上界；
- 不把事件类别文本输入确定性 selector；
- 不生成模板问题，不把 UnAV-100 伪装成 QA 数据集。

### 6.3 窗口预算与指标

为避免长短视频偏差，使用两类预算：

- 主预算：保留原视频有效窗口的 10% 和 25%；
- 辅助预算：固定 `K=4/8`，只用于与 D1 对齐。

主指标：

- Event Coverage@Budget；
- Window Recall@Budget；
- mean best tIoU；
- 首个命中排名；
- Oracle headroom；
- 相对 All 的窗口、token、延迟和显存变化。

`event-level mAP` 只有在候选段合并、类别预测、置信度和 tIoU 匹配协议与官方脚本完全对齐后才报告。

### 6.4 D2 通过条件

Oracle 主门禁必须同时满足：

1. 25% 预算下 Oracle Event Coverage 比五个 Random seed 均值至少高 15 个百分点；
2. Oracle 与 Random 的 paired bootstrap 95% 置信区间下界大于 0；
3. 短、长、多事件和重叠事件四组中至少三组方向一致；
4. 媒体缺失率和排除原因不造成类别或事件时长的明显选择偏差。

确定性策略另设诊断门禁：

1. 至少一个不使用 Oracle 的确定性策略比 Random 均值高 5 个百分点；
2. 该策略胜过至少 4/5 个 Random seed。

如果 Oracle 都没有足够 headroom，立即停止 selector 路线，不进入 LongVALE，不增加 scorer 复杂度。

如果 Oracle 通过但确定性策略失败，说明“压缩上界存在，但当前规则 selector 无效”，允许进入 D3，只训练 question-conditioned selector。

## 7. D3：LongVALE 单模块训练

只有 D2 Oracle 通过才启动。

### 7.1 数据与 split

- 只使用 LongVALE 官方数据；
- 官方 training split 内部按物理视频划分 train/validation；
- 官方 evaluation split 保持封存，只运行一次；
- 不加入 AVE、UnAV-100、AVQA、AVUT 或 M4-IT 样本；
- 不使用 LongVALE 与其他数据拼接的 SFT 文件；
- 训练前冻结 split manifest、媒体缺失清单和 SHA。
- 与 UnAV-100 test、AVQA test、M4-IT 和历史 AS-M4 清单做 YouTube ID 交叉审计；任何跨阶段重叠必须在训练前排除或在报告中单列，不得进入去重主指标。

### 7.2 最小实现

1. 新增 selector 专用训练入口；
2. 参数白名单只允许 `dynamic_window_selector.*`；
3. 冻结 M4/Qwen2、`lm_head`、视觉塔、BEATs、全部 projector、fusion、Gate 和 Event Aligner；
4. selector 输入为冻结的问题 embedding、冻结音频/视觉窗口表示、source index 和 timestamps；
5. 第一轮只优化窗口 ranking loss，不使用 LM loss；
6. checkpoint 选择和 early stopping 只看内部 validation；
7. 正确 query、shuffled query 和 empty query 使用相同音视频与窗口预算。

第一版 D3 使用冻结的缓存窗口特征，只验证 question-conditioned ranking，不宣称节省 BEATs。它可以减少后续 projector、fusion 和语言模型 token，但 BEATs 计算量仍按 All windows 记录。若未来要把 learned selector 前移到 BEATs 之前，必须另立“轻量前置 scorer/蒸馏”阶段重新做 D1–D4，不得直接沿用缓存模式的效率结论。

### 7.3 比较对象

- Audio-only selector；
- Video-only selector；
- Question-only selector；
- Audio+Video selector；
- Audio+Video+Question selector；
- D2 锁定的最佳确定性策略；
- Random 五个 seed；
- Oracle。

所有方法必须在 LongVALE 上重新运行，不能拿 D2 或 Stage2 的数值直接横向比较。

### 7.4 D3 通过条件

官方 evaluation 的 25% 预算下：

1. Audio+Video+Question 的 Event Coverage 至少高于 Random 均值 10 个百分点；
2. 至少高于最佳确定性策略 5 个百分点；
3. Oracle headroom recovery 至少为 0.30；
4. 正确 query 至少高于 shuffled query 5 个百分点，并高于 empty query；
5. Question-only 明显低于 Audio+Video+Question，排除文本捷径；
6. 平均保留率不超过 25%；
7. 全部非 selector 参数训练前后 SHA 不变。

D3 失败时停止 learned selector，不进入联合训练，也不通过解冻 projector 或 fusion 补救。

## 8. D4：AVQA 纯官方下游门禁

只有 D3 通过且 AVQA 官方分片全部下载、解压、校验完成后启动。

### 8.1 数据规则

- 只使用 AVQA 官方项目页提供的原始视频和标注；
- 严格使用官方 split；
- 不加入 AVUT、MUSIC-AVQA、AVE 模板题或 Stage2 样本；
- validation 只做格式和推理链路接入；
- test 在 selector、K、checkpoint 和解码配置全部冻结后只运行一次；
- 不根据 AVQA test 结果继续训练 LongVALE selector。
- 在解封 test 前，将 AVQA video ID 与 LongVALE selector train/validation、M4-IT 和全部历史 AS-M4 训练清单交叉比对；去重 test 是主结果，官方完整 test 只作可比性补充。

### 8.2 对照

- Video-only；
- Question+choices text-only；
- All audio windows；
- D2 锁定确定性 selector；
- D3 锁定 question-conditioned selector；
- Random 五个 seed；
- Silence；
- 五个固定错配音频映射。

AVQA 没有可靠的窗口级 Oracle 边界，因此禁止在 AVQA 上伪造 Oracle。

### 8.3 指标与门禁

分别报告：

- Overall Accuracy；
- View Accuracy；
- Sound Accuracy；
- Both Accuracy；
- 相对 All 的 gain、harm 和 net gain；
- 正确音频与五个错配音频的差；
- text-only 与 Video-only 的差，显式量化文本捷径；
- 空输出、首 token EOS、重复文本和非有限输出；
- 端到端延迟、峰值显存和实际 BEATs 编码窗口数。

正式通过条件：

1. Sound+Both 合并子集相对 All 的准确率差 95% paired bootstrap 置信区间下界大于 0；
2. View 子集相对 All 的下降不超过 1 个百分点；
3. 正确音频显著高于五个错配映射均值；
4. silence 与 `gate=0` 回退不退化；
5. 本轮 BEATs 计算效率结论只来自 `energy_topk` 和 `onset_topk`；D3 cached learned selector、cached Eventness 与 Temporal scorer 均不宣称节省 BEATs。
6. 必须完整报告 text-only；若 selector 的新增正确样本大多已被 text-only 答对，不得解释为音频选窗收益。

如果 LongVALE 定位通过但 AVQA 问答不提升，结论是“定位能力没有转化为当前 M4 的答案收益”。此时停止 selector 接入，优先检查音频语义目标、答案表示和融合位置。

## 9. D5：VGGSound-Sparse 独立同步诊断

D5 不参与 D1–D4 的总分，只回答“选中的稀疏窗口是否包含同步线索”。

- 使用官方 VGGSound-Sparse annotation；
- 使用官方支持的正负 offset 协议；
- 报告 Offset Classification Accuracy、Offset MAE 和同步窗口 Recall@Budget；
- 分别报告原始同步、提前、延后和窗口打乱；
- 不把同步准确率解释为 AVQA 答题准确率；
- 不把 VGGSound-Sparse 样本加入 LongVALE selector 训练。

## 10. 统一冻结和公平性规则

上界诊断阶段固定：

- M4/Qwen2；
- `lm_head`；
- 视觉塔；
- BEATs；
- 全部 projector 和 resampler；
- residual fusion；
- 固定推理 gate；
- Offset、learned Gate 和 Event Aligner 关闭；
- 确定性解码。

统一执行：

1. 全部参数 `requires_grad=False`；
2. `eval()` 与 inference mode；
3. 不创建 optimizer；
4. 输入、配置、模型和 manifest 保存 SHA；
5. 每条输出保存 source index、timestamps、score、实际窗口数、预算和随机 seed；
6. 选中窗口进入模型前按原始时间排序；
7. cache 上界模式与前置效率模式分开报告；
8. 所有 mismatch 使用五个固定映射；
9. 不在 test 上调 K、阈值、窗口长度或 scorer 权重；
10. 不用缺失媒体率较低的另一个数据集替换当前数据集。
11. 每个阶段同时保存“官方原始 split”和“物理视频去重 split”；前者用于对照官方数字，后者用于本项目主结论。

指标定义固定为：

- `Event Coverage@Budget`：至少一个选中 micro-window 与目标事件有正重叠的事件比例；
- `Positive-window Recall@Budget`：选中正窗口数除以全部正窗口数，先逐事件计算再做 macro average；
- `mean best tIoU`：每个目标事件与所有选中 micro-window 的最大 tIoU，再按事件 macro average；
- `Oracle headroom recovery = (selector - random_mean) / (oracle - random_mean)`；分母不大于 0 时该划分无效；
- 多事件视频先按事件计算，再按视频 macro average；同时提供 event-micro 结果作为补充，禁止只报更好看的聚合方式。

## 11. 决策树

```text
D0 失败
└─ 修 harness；禁止效果实验

D1 失败
└─ 修窗口、mask、索引、cache 或回退；禁止 UnAV-100

D2 Oracle 失败
└─ 终止 selector 路线
   └─ 转向音频语义目标、答案表示和融合位置

D2 Oracle 通过，规则策略失败
└─ 允许 D3，只训练 question-conditioned selector

D3 失败
└─ 停止 learned selector；禁止联合训练

D3 通过，D4 失败
└─ 定位可行但不能改善 M4 答案
   └─ 停止 selector 接入，重构语义与融合

D3 与 D4 均通过
└─ 才允许设计下一阶段 Semantic Contrast
```

## 12. 产物与提交

每个阶段使用独立目录：

```text
intersuit/harness/artifacts/dynamic_selector_d0_contract/
intersuit/harness/artifacts/dynamic_selector_d1_ave_official/
intersuit/harness/artifacts/dynamic_selector_d2_unav100/
intersuit/harness/artifacts/dynamic_selector_d3_longvale/
intersuit/harness/artifacts/dynamic_selector_d4_avqa/
intersuit/harness/artifacts/dynamic_selector_d5_sparse_sync/
```

每个目录保存：

- 官方来源和仓库 commit；
- split manifest 与 SHA；
- 物理视频去重报告；
- 历史使用排除清单；
- 媒体缺失和失败清单；
- 锁定配置；
- stdout、stderr 和开始时间；
- GPU、功率、精度模式和 batch size；
- 逐样本选择结果；
- 五个 Random/mismatch seed；
- 汇总 JSON；
- 中文报告；
- 失败样例；
- checkpoint SHA。

代码提交边界：

1. D0 harness 与单元测试；
2. D1 AVE 官方数据适配与门禁；
3. D2 UnAV-100 适配与上界结论；
4. D3 selector 专用训练入口；
5. D4 AVQA 一次性正式结论；
6. D5 同步专项结果。

checkpoint、权重、数据集、缓存、预测和训练日志不得加入 Git。

## 13. 立即执行顺序

1. 实现 D0 独立 harness，并修正 eventness 命名、source index/timestamp 审计和五 seed 对照。
2. 将正式选择单位冻结为原始 1 秒 micro-window，把现有多尺度实现降级为独立历史基线。
3. 生成 AVE 官方物理视频级 train/validation/test 候选清单，并在 M4 训练污染审计后冻结最终 validation/test 数量。
4. 下载缺失的 AVE 官方 validation 媒体；test 暂不解封。
5. 完成 D1 validation 后冻结所有策略与阈值。
6. 一次性运行 D1 官方 test。
7. D1 通过后，只下载 UnAV-100 annotation、split、官方脚本和 100 视频 train/validation pilot。
8. D2 Oracle 通过后才获取 LongVALE，并先完成跨数据集物理视频去重，再实现 selector-only 训练。
9. D3 通过且 AVQA 官方下载完整后，完成训练污染审计，再运行纯 AVQA 下游门禁。
10. D4 通过后，才讨论 Semantic Contrast；D4 失败则回到语义目标、答案表示和融合位置。

这条链路的核心不是“尽量把 selector 做成功”，而是尽早、明确地判断它究竟卡在接口、定位、条件选择，还是下游语义转化。
