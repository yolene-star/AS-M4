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

本计划不把所有公开数据集拼成一个“完整系列”后同时运行。不同数据集回答不同问题，必须分层使用：

1. 本地 Stage2 回答“选窗能否改善当前 M4 的最终问答结果”。
2. 强时间边界数据集回答“selector 是否选中了正确事件窗口”。
3. 长视频查询数据集回答“问题条件是否改变窗口排序”。
4. 同步专项数据集回答“模型学到的是时间同步还是仅有语义匹配”。
5. 没有窗口级标注的数据集只能作为下游问答或融合评测，不能单独支撑 selector 结论。

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
- [AVE 官方仓库](https://github.com/YapengTian/AVE-ECCV18)：利用秒级 dense 标注构建低成本 Oracle 和窗口召回 smoke。
- [AVVP/LLP 官方仓库](https://github.com/YapengTian/AVVP-ECCV20)：检查 audio、visual 与 audio-visual 事件的模态职责，可用于伪标签质量诊断；不作为当前 Oracle 主门禁。
- [AVUT 官方仓库（ACVUBench）](https://github.com/lark-png/ACVUBench)：当前 Stage2 只使用其本地整理子集；完整官方集保留为通过门禁后的音频中心问答评测。
- [MUSIC-AVQA 官方仓库](https://github.com/GeWu-Lab/MUSIC-AVQA)：当前 Stage2 只使用其本地整理子集；完整官方集用于后续乐器场景的时空推理复核。

### 2.2 第一优先级外部上界集

[UnAV-100 官方项目页](https://unav100.github.io/)及其[官方 Hugging Face 数据仓库](https://huggingface.co/datasets/ttgeng233/UnAV-100)是第一优先级的外部 selector 上界集。它包含未裁剪、多事件视频，并提供事件类别和精确时间边界，适合检验：

- All、Random Top-K、Energy Top-K、Eventness Top-K 与 Oracle Top-K；
- Window Recall@K、首个命中排名、Temporal IoU 和事件级 mAP；
- 不同事件长度、同视频多事件和重叠事件条件下的窗口预算敏感性。

UnAV-100 不提供自然语言问答，因此它只回答“是否选中正确事件窗口”，不能替代 Stage2 的最终问答门禁。接入顺序固定为：

1. 先下载并审计 annotation、split 和官方特征清单，不直接下载全部原始视频。
2. 构建 100 个视频的分层 pilot，覆盖短事件、长事件、多事件和重叠事件。
3. 核对媒体可用率、时间戳合法性、窗口映射和官方 split。
4. pilot 通过后才扩展完整评测，并记录数据版本或 commit SHA。

若 Stage2 Oracle 问答无收益、但 UnAV-100 的 Oracle/确定性 selector 窗口召回明显有效，结论应是“选窗本身可定位，但当前音频语义表示或融合无法把定位转化为答案收益”；仍然停止在 M4 中训练 selector，转向语义目标、答案表示和融合位置诊断。

### 2.3 Oracle 通过后的问题条件评测

只有本地 Stage2 Oracle 门禁通过后，才引入 question-conditioned selector 数据：

| 数据集 | 关键监督 | 在本计划中的职责 | 限制 | 官方入口 |
| --- | --- | --- | --- | --- |
| LongVALE | 长视频事件起止时间、多模态事件描述 | 长视频 question-conditioned Top-K、压缩率与窗口召回 | 描述可直接暴露事件语义，构造查询时必须防止答案泄漏 | [GitHub](https://github.com/ttgeng233/LongVALE) · [Hugging Face](https://huggingface.co/datasets/ttgeng233/LongVALE) |
| AVUT | 音频内容、事件定位、计数、片段匹配等八类任务 | 检查选窗是否改善音频中心问答与音视频对齐 | 主要是评测集，不用于大规模训练 selector | [GitHub](https://github.com/lark-png/ACVUBench) · [论文](https://arxiv.org/abs/2503.19951) |
| MUSIC-AVQA | 音频、视觉和音视频时空问答 | 检查乐器事件、先后顺序和空间关系 | 场景集中于音乐，不能代表通用声音 | [GitHub](https://github.com/GeWu-Lab/MUSIC-AVQA) · [项目页](https://gewu-lab.github.io/MUSIC-AVQA/) |
| QVHighlights | 自然语言查询、相关时间段、窗口显著性 | 验证问题条件窗口评分的架构能力 | 不是音频中心数据，不能证明音频贡献 | [GitHub](https://github.com/jayleicn/moment_detr) |

LongVALE 是问题条件长视频 selector 的主要扩展集；QVHighlights 只作为“问题是否真正改变窗口排序”的架构控制。二者都不能绕过 Stage2 的音频必要性、错配音频和最终 QA 门禁。

### 2.4 同步、稀疏事件与开放词汇专项集

以下集合不进入第一轮低成本主矩阵，只在对应研究假设被单独提出时使用：

| 数据集 | 最适合验证 | 计划位置 | 官方入口 |
| --- | --- | --- | --- |
| VGGSound-Sparse | 稀疏同步线索、人工音频 offset、同步窗口 Recall@K | selector 通过后，独立验证时间偏移与稀疏事件 | [GitHub](https://github.com/v-iashin/SparseSync) · [项目页](https://v-iashin.github.io/SparseSync) |
| AV-SyncBench | 时间一致性与语义一致性的解耦 | 新数据集复现和可用性审计通过后的补充同步诊断 | [项目与代码](https://fgt7t6g.github.io/AV-SyncBench) · [论文](https://arxiv.org/abs/2607.00726) |
| OV-AVEBench | 已见/未见开放词汇事件的时间定位 | 论文级 selector 泛化实验 | [GitHub](https://github.com/jasongief/ov-avel) |
| AVQA | Sound、View、Both 下游问答与音频必要性 | 融合和 Gate 的补充评测，不作为 selector 主真值集 | [官方项目页](https://mn.cs.tsinghua.edu.cn/avqa/) |

AVQA 视频较短且没有答案对应的窗口级时间边界，因此只能报告 All/Random/确定性 Top-K 的下游差异，不能把其结果解释为 selector 已学会时间定位。AV-SyncBench 发布较新，必须先审计许可、数据可获得性、评测脚本和基线复现，再决定是否进入正式报告。

### 2.5 数据集与结论的联合判定

| Stage2 Oracle QA | UnAV-100 窗口定位 | 结论与行动 |
| --- | --- | --- |
| 通过 | 通过 | 推进 LongVALE 上的 question-conditioned selector，并在 AVUT/MUSIC-AVQA 做下游复核 |
| 通过 | 未通过 | 暂停推进；优先排查 Stage2 小样本、Oracle 泄漏或窗口定义偏差 |
| 未通过 | 通过 | 停止 M4 selector 训练；重构音频语义目标、答案表示与融合位置 |
| 未通过 | 未通过 | 终止 selector 路线，不增加 scorer 复杂度 |

外部数据集不得用于反复调参后再回头报告同一测试集。每个数据集必须保存官方 split、下载来源、版本 SHA、媒体缺失清单和最终纳入样本清单。

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
