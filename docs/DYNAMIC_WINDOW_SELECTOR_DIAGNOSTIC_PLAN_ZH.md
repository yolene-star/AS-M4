# Dynamic Window Selector 可证伪诊断行动计划

日期：2026-07-24（Asia/Shanghai）

## 1. 当前判断

- 当前 selector 是无训练参数的规则模块，基于特征变化、能量、onset 和迟滞阈值。
- 现有 dynamic 模式已经直接进入模型推理链路。本轮不沿用该入口做正式训练，而是建立独立诊断 harness。
- 训练框架目前只能解冻整个 `streaming_av_module` 或 fusion，不能只训练 selector。
- Temporal scorer 三个 seed 均未通过原门禁，只能作为失败基线，不能作为默认方案。
- Stage2 清单没有时间边界。对齐 dense AVE 标注后，Oracle 可用的局部事件样本为：
  - reserve：22 条音频 QA；
  - 正式 dev760：53 条音频 QA。
- 其余多数 AVE 事件覆盖全片，Oracle 会退化为 All windows，不能用于验证窗口选择上界。
- 主评测权重固定为 Semantic Contrast `checkpoint-150`，它代表当前最新语义尝试。若 Oracle 失败，再使用 Fixed BEATs checkpoint 做一次仅包含 All/Oracle 的归因复核。

## 2. 评测数据集设计

本节的外部数据集候选严格限定为调研结论中的八个集合：LongVALE、UnAV-100、OV-AVEBench、QVHighlights、VGGSound-Sparse、AV-SyncBench、AVE 和 LLP。不同数据集回答不同问题，不把它们一次性全部下载或拼成同一主指标。

### 2.1 当前本地必跑集合

`AS_M4_STAGE2` 是由多个来源整理出的本地诊断组合，不是 AVE、AVUT 或 MUSIC-AVQA 的官方完整版本。

| 划分 | QA 数 | 来源组成 | 任务组成 | 用途 |
| --- | ---: | --- | --- | --- |
| Reserve | 326 | AVE 144、AVUT 105、MUSIC-AVQA 77 | audio 134、audio_visual 101、visual 91 | 调试与门禁筛选 |
| 正式 Dev | 760 | AVE 352、AVUT 249、MUSIC-AVQA 159 | audio 316、audio_visual 228、visual 216 | 锁定配置后一次性确认 |
| Oracle Reserve | 22 | 本地 AVE dense 局部事件 | audio 22 | 小样本下游上界筛选 |
| Oracle Dev | 53 | 本地 AVE dense 局部事件 | audio 53 | 小样本下游上界确认 |

正式 Dev 固定使用原 dev manifest 去掉前 20 条 smoke 样本后的 760 条，不再写作 `dev780`。Oracle 子集只用于比较 All、Random、确定性策略与 Oracle 的下游问答差异，不能代表完整数据分布。

本地数据的职责固定如下：

- `AS_M4_STAGE2`：报告最终 QA、audio/audio_visual/visual 分项、静音、错配音频和行为回退。
- [AVE 仓库](https://github.com/YapengTian/AVE-ECCV18)：利用 1 秒级窗口标注构建低成本 Oracle、Top-K、mask 和损失 smoke。
- [AVUT 仓库](https://github.com/lark-png/ACVUBench)：作为 selector 是否改善音频中心问答的现有下游复核。
- [MUSIC-AVQA 仓库](https://github.com/GeWu-Lab/MUSIC-AVQA)：作为乐器场景下细粒度时空问答的现有下游复核。

### 2.2 外部候选优先级与仓库

| 优先级 | 数据集 | 主要监督 | 最适合检验 | 仓库或官方入口 |
| --- | --- | --- | --- | --- |
| S | LongVALE | 长视频事件起止时间和事件描述 | 问题条件动态选窗、长视频压缩 | [GitHub](https://github.com/ttgeng233/LongVALE) · [Hugging Face](https://huggingface.co/datasets/ttgeng233/LongVALE) |
| S | UnAV-100 | 多事件精确时间边界和事件类别 | Oracle Top-K、事件窗口召回 | [项目与下载](https://unav100.github.io/) · [Hugging Face](https://huggingface.co/datasets/ttgeng233/UnAV-100) |
| A | OV-AVEBench | 人工段级标注和开放词汇划分 | 未见事件上的 selector 泛化 | [GitHub](https://github.com/jasongief/ov-avel) |
| A | QVHighlights | 查询相关时间段和窗口显著性 | Question-conditioned selector | [GitHub](https://github.com/jayleicn/moment_detr) |
| A | VGGSound-Sparse | 稀疏同步线索和人工时间偏移 | 稀疏事件发现、音视频同步 | [GitHub](https://github.com/v-iashin/SparseSync) |
| A- | AV-SyncBench | 时间一致性与语义一致性解耦标注 | 错位修正、同步置信度 | [项目、代码与数据](https://fgt7t6g.github.io/AV-SyncBench) |
| B | AVE | 1 秒级音视频事件标签 | 代码、损失、Top-K 和 Oracle smoke | [GitHub](https://github.com/YapengTian/AVE-ECCV18) |
| B | LLP | 弱视频级标签和验证/测试 dense 标注 | 伪标签 selector、模态区分 | [GitHub](https://github.com/YapengTian/AVVP-ECCV20) |

### 2.3 第一轮：低成本可证伪实验

第一轮只使用本地 AVE 和外部 UnAV-100：

- AVE 验证 selector forward、Top-K 索引、mask、Oracle overlap 和监督损失实现正确。
- UnAV-100 验证未裁剪多事件视频中的 Oracle 上界与真实窗口召回。
- 两个数据集统一比较 All、Random Top-K、Energy Top-K、Eventness Top-K 和 Oracle Top-K。
- 统一报告 Window Recall@K、首个命中排名、Temporal IoU、事件级 mAP 和保留窗口比例。

UnAV-100 的接入顺序固定为：

1. 只获取 annotation、split 和官方特征清单，不直接下载全部原始视频。
2. 构建 100 个视频的分层 pilot，覆盖短事件、长事件、多事件和重叠事件。
3. 核对媒体可用率、时间戳合法性、窗口映射和官方 split。
4. pilot 通过后才扩展完整评测，并记录数据版本或 commit SHA。

UnAV-100 没有自然语言问题，只能回答“是否选到正确事件窗口”，不能替代 Stage2 的最终问答门禁。

### 2.4 第二轮：问题条件动态选窗

只有 Stage2 和第一轮 Oracle 上界均明确通过，才引入 LongVALE：

- 训练并比较 Question-conditioned、Audio-conditioned、Video-conditioned 和 Audio+Video+Question Top-K。
- 以精确事件边界和描述监督长视频 selector。
- 报告 Window Recall@K、mAP@tIoU、保留窗口比例和单位视频推理耗时。
- 优先在人工检查的 evaluation split 上报告主要结果。
- 事件描述构造查询时必须防止直接泄露答案或类别标签。

QVHighlights 只作为问题条件架构控制：它用于确认同一视频在不同查询下会得到不同窗口排序，但不能单独证明音频贡献。

### 2.5 第三轮：错位与鲁棒性

在 selector 上界和问题条件选择均通过后，使用 VGGSound-Sparse 或 AV-SyncBench 进行独立专项实验：

- 人工加入音频提前/延后、窗口打乱、音频缺失、随机错配和局部噪声。
- 报告 Offset Classification Accuracy、Offset MAE、同步窗口 Recall@K 和扰动前后性能变化。
- 明确区分 Selector、Alignment 和 Gate：分别负责找窗口、修正偏移和抑制不可靠音频。
- AV-SyncBench 发布较新，必须先审计许可、数据可获得性、评测脚本和基线复现，再决定是否进入正式报告。

### 2.6 第四轮：论文级泛化与伪标签

- OV-AVEBench 报告 seen/unseen Window Recall@K、segment-level F1、event-level F1 和调和平均，检查 selector 是否只记住封闭事件类别。
- LLP 用于教师模型、跨模态相似度和时序一致性伪标签路线；其弱标签训练集不作为 Oracle 真值，人工 dense 验证/测试标注只用于质量评估。
- AVUT 检查选窗是否改善音频中心问答，MUSIC-AVQA 检查乐器场景时空推理；二者仍属于下游结果，不替代窗口级定位指标。

### 2.7 联合判定与停止规则

| Stage2 Oracle QA | UnAV-100 窗口定位 | 结论与行动 |
| --- | --- | --- |
| 通过 | 通过 | 推进 LongVALE question-conditioned selector，再做同步与泛化专项 |
| 通过 | 未通过 | 暂停推进；优先排查 Stage2 小样本、Oracle 泄漏或窗口定义偏差 |
| 未通过 | 通过 | 停止 M4 selector 训练；重构音频语义目标、答案表示与融合位置 |
| 未通过 | 未通过 | 终止 selector 路线，不增加 scorer 复杂度 |

每个外部数据集必须锁定官方 split、下载来源、版本或 commit SHA、媒体缺失清单和最终纳入样本清单。不得在测试集上反复调参后回头报告同一结果。

## 3. 第一阶段：建立独立上界 Harness

本阶段不修改正式训练 launcher，只增加诊断脚本、配置和测试。

统一支持以下策略：

| 策略 | 精确定义 |
| --- | --- |
| `all` | 保留全部有效 1 秒窗口，验证与现有 Fixed 推理完全一致 |
| `uniform_topk` | 按时间均匀选择 K 个窗口，控制“仅减少 token”带来的影响 |
| `energy_topk` | 按 waveform RMS 排序，分数相同时选择更早窗口 |
| `eventness_topk` | 使用当前确定性 feature-change、energy 和 onset 分数，不启用未训练的 Event Detector |
| `temporal_conf_topk` | 使用冻结 Temporal scorer 的 margin 排序；`margin >= 0.5` 标记为高置信，并单独报告覆盖率 |
| `oracle_topk` | 按窗口与答案事件标注的重叠时长排序，只在局部事件边界子集执行 |
| `random_topk` | 使用五个固定 seed，输出均值、方差和逐 seed 结果 |

固定实验协议：

- 主预算为 `K=4`。
- `K=8` 仅用于 Oracle、Random 和 Uniform 的敏感性复核。
- 所有策略直接选择原始音频窗口，再经过冻结的 BEATs、projector、fusion 和 M4。
- 本轮属于非因果上界实验，使用独立命名空间，并明确记录 lookahead 为完整 clip。
- 非因果上界结果不能解释为可部署 selector。
- 每条输出保存：
  - 窗口索引；
  - 窗口时间戳；
  - 选择分数；
  - 实际选中数量；
  - 压缩率；
  - Oracle overlap；
  - 随机 seed；
  - 输入 SHA。
- 不更新 `run_as_m4_stage.sh`。
- 不创建训练 checkpoint。

## 4. 冻结与实验配置

以下模块和设置在整个上界实验中保持不变：

- M4/Qwen2；
- `lm_head`；
- 视觉塔；
- 视觉 projector 和 resampler；
- BEATs；
- scene-audio projector；
- residual fusion；
- 固定推理 gate `0.005`；
- Offset 关闭；
- learned Gate 关闭；
- Event Aligner 关闭；
- 确定性解码；
- 现有 `label_terms` scorer。

诊断 harness 启动后必须执行以下断言：

1. 全部参数均为 `requires_grad=False`。
2. 推理前后模型参数 SHA 保持不变。
3. `all` 策略在相同样本上逐条复现既有 Fixed 预测。
4. 不同策略复用同一批预计算 BEATs 特征，避免重复编码和数值漂移。
5. 不允许隐式启用 Offset、learned Gate、Event Aligner 或其他新增模块。

## 5. 执行顺序与门禁

### 5.1 CPU 与单卡 Smoke

1. 增加以下 CPU 单元测试：
   - 策略排序；
   - padding 和无效窗口屏蔽；
   - tie-break；
   - 随机种子复现；
   - Oracle overlap；
   - 缺失 Oracle 标注时显式拒绝；
   - `K` 大于有效窗口数时的行为；
   - silence 不产生非有限结果。
2. 使用物理 GPU 4 执行 8 条 smoke。
3. 验证 `all` 精确复现既有 Fixed 结果。
4. 验证 silence 和 `gate=0` 精确回退。
5. 验证全部诊断无 NaN/Inf。
6. 保存 stdout、stderr、配置、开始时间和 GPU/功率状态。

任何 smoke 条件失败时，只允许修复 harness，不进入效果实验。

### 5.2 Reserve 上界筛选

在 Oracle 可用的 22 条 reserve audio QA 上运行完整策略矩阵。

为了防止 Oracle 标签或时间信息形成虚假收益，需要把目标样本的 Oracle 时间戳原样应用到 mismatched audio，作为负对照。

进入 dev 的最低条件：

1. Oracle K4 相对 All 的净增益至少为 `+2` 题。
2. Oracle 正确音频至少比 Oracle-mismatched 多答对 `2` 题。
3. Oracle 至少比五个 Random seed 的平均结果多答对 `2` 题。
4. Oracle 必须胜过至少 `4/5` 个 Random seed。
5. 不得新增空输出、首 token EOS、重复文本或非有限诊断。

如果任一条件失败：

1. 使用 Fixed BEATs checkpoint 仅重跑 All 和 Oracle。
2. 不调整 K、gate、阈值或数据子集。
3. 如果 Fixed BEATs checkpoint 仍未通过，则停止 selector 路线。

### 5.3 Dev 一次性确认

只有 reserve 门禁通过后，才能对锁定的 53 条局部事件正式 dev audio QA 执行一次确认。

正式通过条件：

1. Oracle K4 相对 All 的净增益至少为 `+4` 题。
2. Oracle-correct 至少比 Oracle-mismatched 多答对 `3` 题。
3. Oracle 至少比 Random 均值多答对 `4` 题。
4. Oracle 必须胜过至少 `4/5` 个 Random seed。
5. K8 与 K4 的收益方向一致。
6. 不得新增空输出、首 token EOS、重复文本或非有限诊断。
7. 报告 paired bootstrap 置信区间和逐题 gain/harm。
8. 不得根据 dev 结果重新调整 K、margin 阈值或 gate。

Oracle 正式通过后，还需要将最佳确定性非 Oracle 策略运行在正式 dev760 上：

- 视觉子集相对 All 最多下降 `1` 题；
- silence 和 `gate=0` 保持精确回退；
- 输出有效性不得退化；
- 正确音频必须优于错配音频；
- 必须报告 audio、audio_visual 和 visual 三类结果。

## 6. 分叉决策

### 6.1 Oracle 明显通过

后续按照单变量原则分阶段推进。

#### 阶段 A：Question-conditioned Selector

1. 新增 `dynamic_window_selector` 专用可训练入口。
2. 禁止通过 `streaming_av_module` 解冻整个流式模块。
3. 构建严格因果、question-conditioned scorer。
4. scorer 输入只允许包含：
   - 冻结 BEATs 当前及历史窗口特征；
   - 当前及历史能量和事件特征；
   - 冻结的问题 embedding；
   - 相对时间信息。
5. 第一轮只训练 selector。
6. 监督目标使用 dense Oracle 窗口 ranking。
7. 不使用 LM loss。
8. 不更新 M4/Qwen2、视觉塔、BEATs、projector 或 fusion。
9. 训练前后审计所有可训练参数名和参数数量。

#### 阶段 B：重新设计 Semantic Contrast

只有 question-conditioned selector 单独通过门禁后，才进入 Semantic Contrast 重构。

当前 Semantic Contrast 使用全局音频表示和 detached 答案 token 表示。下一版应改为：

- 选中窗口与问题—答案联合表示进行匹配；
- 正样本为与答案相关的 Oracle 窗口；
- 同视频非答案窗口作为局部困难负样本；
- 跨视频相似或混淆事件作为语义困难负样本；
- 不把 silence 当作主要语义负样本；
- Semantic Contrast 阶段冻结 selector；
- 每个阶段只改变一个核心变量，不能同时训练 selector、projector、fusion 和语言模型。

### 6.2 Oracle 未通过

如果 Semantic Contrast 和 Fixed BEATs 两个 checkpoint 的 Oracle 都未通过：

1. 明确终止 selector 路线。
2. 不训练 learned selector。
3. 不通过调 K、gate 或增加 selector 复杂度继续尝试。
4. 依次诊断以下表示是否包含答案语义：
   - BEATs 原始特征；
   - scene-audio projector 输出；
   - residual fusion 后表示；
   - 最终答案相关隐藏状态。
5. 优先重构：
   - 音频语义训练目标；
   - 问题—答案表示；
   - 正负样本定义；
   - 音频与视觉的融合位置。
6. 每次只验证一个研究假设，不能同时修改语义目标和融合结构。

## 7. 结果指标

每种策略至少报告：

- 总样本数；
- 正确数和准确率；
- 相对 All 的 gain、harm 和 net gain；
- 相对 video-only 的 gain、harm 和 net gain；
- 正确音频与错配音频的准确率差；
- 正确音频独有答对数；
- 错配音频独有答对数；
- 空输出数；
- 首 token EOS 数；
- 重复文本数；
- 非有限诊断数；
- 平均选中窗口数；
- 音频 token 压缩率；
- Oracle 窗口召回率；
- selector 分数分布；
- Random 五个 seed 的均值和方差；
- K4/K8 敏感性结果。

## 8. 产物与命名

新实验使用独立命名空间，例如：

```text
intersuit/harness/artifacts/dynamic_selector_upper_bound_v1/
```

目录中至少保存：

- 锁定配置；
- manifest SHA；
- Oracle 子集清单；
- 窗口选择 JSONL；
- 各策略预测；
- 汇总 JSON；
- 中文结果报告；
- stdout；
- stderr；
- 开始时间；
- GPU 和功率记录；
- 失败样例；
- checkpoint SHA。

中文结果报告必须明确标注：

> 本实验是非因果 Dynamic Window Selector 诊断上界，不是正式训练结果，也不是可部署方案。

## 9. 提交边界

以下阶段分别创建独立 Git commit：

1. 诊断 harness、Oracle 子集构建器和单元测试完成。
2. CPU 测试与 8 条单卡 smoke 通过。
3. Reserve 上界实验完成并做出是否进入 dev 的决定。
4. Dev 正式确认完成，或 selector 路线被正式停止。

不得把 checkpoint、权重、数据集、缓存、预测、训练日志或其他运行资产加入 Git。

## 10. 下一步

下一步先实现独立诊断 harness 与 Oracle 子集构建器，然后完成 CPU 测试和 8 条单卡 smoke。当前本地门禁稳定后，只获取 UnAV-100 的 annotation、split 和特征清单，构建 100 视频 pilot；不直接下载全部公开数据集。

在这些门禁全部通过前：

- 不启动 selector 训练；
- 不修改正式训练 launcher；
- 不解冻 M4/Qwen2、视觉塔、BEATs、projector 或 fusion；
- 不进入 question-conditioned selector；
- 不重新设计 Semantic Contrast。
