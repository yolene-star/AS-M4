# Dynamic Window Selector 独立重启与长视频稀疏事件诊断计划

日期：2026-07-25（Asia/Shanghai）

## 1. 当前状态与目标

AVE 官方 D1 已按原计划一次性执行，正式结论为 `test_failed`：Oracle 与五个 Random seed 的 Hit@4 差值为 `8.8571pp`，低于预注册的 `10pp` 门槛。该结论永久冻结，不因本计划改变。

本计划是独立的新研究路线，不是原 D2 的继续，也不重开 AVE D1。目标是回答两个更窄的问题：

1. 长视频、稀疏事件数据是否为窗口选择提供足够的随机基线动态范围；
2. 已知正确音频事件窗口是否能改善冻结 M4 的下游事件描述，再决定是否值得训练 question-conditioned selector。

在 N0 数据适配门禁通过以前，禁止下载新媒体、提取特征、运行模型预测、访问任何新 test 或训练 selector。

## 2. 永久冻结边界

以下事实不得被本计划覆盖：

- AVE D1 official test 只能访问一次，禁止重跑、改 K、改阈值、改 Random seed 或改 scorer 后重新判定；
- 原 `D2 LongVALE`、D3、D4 均因 D1 失败而不允许启动；
- AVE D1 的 test 结果只能作为历史诊断证据，不能作为新路线的 validation/test；
- `AVE_HF_*`、`AS_M4_STAGE2`、`dev760`、历史 temporal scorer 和已经观察过的本地集合均不具备新路线正式 test 资格；
- 任何新数据集都必须重新生成来源、split、物理视频 ID、窗口 schema、媒体 SHA 和污染审计；
- 新路线不能把新数据集的结果与 AVE 指标加权成一个总分，也不能用新结果追认 AVE D1 通过。

当前本地盘点未发现 LongVALE 或 UnAV-100 的官方标注、split、特征或媒体目录。已有的 AVQA 分片、AVE 和历史 AS-M4 资产不满足本路线的替代条件。

## 3. 阶段与停止关系

```text
N0 元数据与动态范围审计
  ├─ fail：停止新路线，保留 D1 失败
  └─ pass
       ↓
N1 官方 train/validation 窗口诊断
  ├─ fail：记录数据/指标不适配，不运行下游预测
  └─ pass
       ↓
N2 冻结 M4 的 Oracle 下游 utility pilot
  ├─ fail：停止 selector 训练，转向音频语义/答案表示/融合位置
  └─ pass
       ↓
N3 独立 confirm
  ├─ fail：停止 selector 训练
  └─ pass：才允许设计新的 selector 训练计划
```

N0–N3 每阶段独立目录、manifest、commit 和 review。任何阶段失败不得通过换 K、换样本、换指标或追加试验挽救。

## 4. N0：数据适配与动态范围硬门禁

### 4.1 允许的输入和操作

N0 只允许获取官方轻量元数据：数据集项目页、官方仓库 commit、标注、split、事件 timestamp、caption/sentence、视频 ID 清单、evaluation script 和文件 SHA。N0 不允许：

- 下载或解压视频、音频、模型 checkpoint 或全量特征；
- 访问官方 test 的媒体内容或运行模型；
- 根据任何预测结果选择样本；
- 将官方 training/validation/test 混合使用；
- 使用非官方镜像替代官方来源。

若官方元数据必须通过网络获取，必须先由用户批准网络权限，并在日志中记录 URL、获取时间、代理环境、来源 commit 和 SHA。网络失败时停止，不切换到不明来源。

### 4.2 候选数据集

候选顺序固定为：

1. **LongVALE**：优先用于下游 segment captioning Oracle utility；必须有官方事件 `timestamp` 与 `sentence`，并能按物理视频划分 pilot/confirm。
2. **UnAV-100**：用于未裁剪、多事件、长视频定位压缩诊断；不能单独证明 M4 下游收益。
3. 其他数据集：只有在前两者均不满足 N0，且重新提交独立计划后才可考虑。

AVE、AVQA、AVUT、MUSIC-AVQA 和历史 Stage2 不得作为本路线的候选替代。

### 4.3 必须计算的分布

对每个候选数据集的官方 train、validation 和预备 test 元数据，按物理视频 ID 去重后统计：

- 视频时长分布与候选 micro-window 数 `N`；
- 事件数量、事件持续时间、事件占视频比例；
- 单事件、多事件、重叠事件和事件间隙比例；
- 每个预算 `B ∈ {10%, 25%}` 以及固定 `K ∈ {2, 4, 8}` 下的有效窗口数；
- 每个视频正窗口数 `P`；
- 理论随机命中概率：

\[
P(\mathrm{Hit}) = 1 - \frac{\binom{N-P}{K}}{\binom{N}{K}}
\]

- Oracle headroom：`1 - theoretical_random_hit`；
- 按视频时长、事件占比和事件数量分层的 headroom；
- 固定 K 与比例预算之间的差异。

时间边界合法性采用固定规则：事件起点必须非负、终点必须大于起点；因容器帧率与小数舍入导致事件终点略超声明时长时，最多允许 `25ms` 的原始偏差，并在统计前将终点钳制到声明时长。所有钳制记录必须进入数据质量报告；超过 `25ms`、空事件或无法配对的 timestamp/caption 立即 fail-closed。该容差在查看动态范围结果前冻结。

### 4.4 N0 通过条件

候选数据集只有同时满足以下条件，才可进入 N1：

1. 至少一个官方 train/validation 开发池和一个物理视频互斥的候选 test 池；
2. 完成与 M4-IT、AS-M4、AVE D1、AVQA、UnAV-100 以及历史 harness 的物理视频 ID 交叉审计；
3. 在主预算 `25%` 下，理论 Random Event Coverage 不得在总体或任一主要事件占比分层中饱和到无法区分 Oracle；
4. Oracle 相对 Random 的理论 headroom 在总体上至少为 `15pp`，且每个主要时长/事件占比分层至少为 `10pp`；
5. 事件和视频数量满足 N2 pilot 的最低规模：至少 `100` 个事件、`50` 个物理视频；若不足，直接停止，不用其他数据补足；
6. 官方 timestamp、sentence/caption 和 evaluation script 的语义可验证；
7. 不能只依靠全局文本或视觉完成任务，至少能构造正确音频与固定错配音频对照；
8. 媒体可用率、许可证和下载方式可在不改变样本定义的前提下审计。

N0 任一条件失败，输出 `n0_failed_data_or_metric_incompatible`，不下载媒体、不运行 N1。

## 5. N1：官方 train/validation 窗口诊断

N1 只使用官方 train/validation 中经污染审计后可用的样本。窗口单位、尾窗、padding、source index 和 timestamp 语义必须在运行前冻结。

核心对照：`uniform`、`energy`、`onset`、`cached_eventness`、五个 Random seed、Oracle。条件 scorer 只有通过统一资格门禁才可加入。

主指标：

- Event Coverage@Budget；
- Positive-window Recall@Budget；
- mean best tIoU；
- 首个命中排名；
- Oracle headroom recovery；
- 实际保留窗口数、token、延迟和显存。

`Hit@K` 只能作为补充，不得单独作为 N1 晋级依据。所有指标按事件计算，并同时提供物理视频 cluster 结果。

N1 的目标不是证明某个规则有效，而是确认数据和指标具有可辨识性。若 Random 饱和、Oracle headroom 不足或长短视频分层方向不一致，立即停止。

## 6. N2：冻结 M4 的 Oracle 下游 utility pilot

N2 只在 N0/N1 通过后启动，且只访问独立的官方 training 内 holdout。pilot 与后续 confirm 按物理视频互斥冻结；任何预测查看前先冻结 manifest、SHA、prompt、预算、解码和 scorer。

固定冻结：M4/Qwen2、视觉塔、BEATs、projector、fusion、gate、lm_head 和解码配置。仅音频窗口按条件变化，视觉输入和非音频 token 保持一致。

核心对照：

- text-only；
- All audio windows；
- Random 五个 seed；
- Oracle 25%；
- Oracle-mismatch 五个固定一一映射；
- N1 锁定的一个确定性策略（若存在）。

全部结果标记为 `noncausal_upper_bound`，记录完整 clip lookahead；不解释为可部署的 strict-causal 能力。

N2 pilot 同时满足以下条件才可解封 confirm：

1. Oracle 25% 相对 All 的逐事件 METEOR 至少提高 `0.01`；
2. Oracle 25% 至少高于 Random 均值 `0.01`；
3. 正确音频 Oracle 高于错配音频均值；
4. 不新增空输出、首 token EOS、重复文本或非有限输出；
5. 视觉 token、问题 token 和非音频输入在所有条件间逐样本一致。

pilot 任一条件失败，输出 `n2_failed_oracle_no_downstream_utility`，停止 selector 训练。

## 7. N3：独立 confirm 与 selector 训练授权

N3 confirm 只运行预注册的缩减矩阵。正式通过必须同时满足：

- Oracle 25% 相对 All 的 METEOR 提升至少 `0.02`，cluster paired bootstrap 95% CI 下界大于 0；
- Oracle 25% 相对 Random 均值至少 `0.02`，且胜过至少 4/5 个 seed；
- 正确音频相对错配音频至少 `0.02`，CI 下界大于 0；
- Oracle 25% 相对 text-only 至少 `0.03`，CI 下界大于 0；
- BLEU-4、ROUGE-L、CIDEr 至少两个指标与 METEOR 同方向；
- 无新增输出异常，All 与 Top-K 的非音频输入和 token budget 公平；
- test 只运行一次，review 全部通过。

只有 N3 通过，才允许另立 selector 训练计划。训练计划必须重新冻结：参数白名单、strict-causal 状态、train/validation、预算、ranking loss、早停和官方 evaluation。N3 失败时停止 selector 路线，转向音频语义目标、答案表示和融合位置。

## 8. 污染、来源和资格规则

每个候选资产必须生成 `asset_registry.json` 与 `asset_qualification.json`，记录：

- 官方 URL、仓库 commit、获取时间和 SHA；
- 物理视频 ID 清单及交叉审计 SHA；
- train/validation/test 边界；
- 窗口长度、hop、尾窗、padding、timestamp 和 source index；
- 特征模型、checkpoint、预处理和输入维度；
- 原始 decision、用途和允许进入的阶段。

任何 `diagnostic_failed`、`schema_incompatible`、`missing_inputs` 或 `data_ineligible` 资产都必须 fail-closed，不得零填充、插值或静默重采样进入正式矩阵。

## 9. 固定统计和运行规则

- Random seed 固定为 `20260720`–`20260724`；
- bootstrap 固定 `10000` 次、seed `20260724`，按物理视频 cluster 重采样；
- 所有 Python 命令使用 `conda run -n M4`；
- 所有 GPU 命令显式设置 `CUDA_VISIBLE_DEVICES`；
- N0 不使用 GPU；N1 之前不下载媒体；
- test 访问前冻结 runner、manifest、配置、prompt、scorer 和资产资格；
- checkpoint、媒体、数据集、特征、缓存、预测和日志不得加入 Git；
- 每阶段结束执行 contract、数据/test、指标公平性、运行产物和 Git 范围 review。

## 10. 产物与当前首个动作

独立产物目录固定为：

```text
intersuit/harness/artifacts/dynamic_selector_restart_n0/
intersuit/harness/artifacts/dynamic_selector_restart_n1/
intersuit/harness/artifacts/dynamic_selector_restart_n2/
intersuit/harness/artifacts/dynamic_selector_restart_n3/
```

当前首个动作只应生成 N0 的：

- `candidate_sources.json`；
- `metadata_manifest.jsonl`；
- `physical_id_cross_audit.json`；
- `dynamic_range_report.json`；
- 中文 `report.md`；
- `review.md`。

在这些产物通过 `review_passed` 前，不得创建 N1 目录中的预测或特征资产。

## 11. N0 冻结结果

N0 已使用官方 LongVALE annotation JSON 和 UnAV-100 annotation parquet 完成元数据审计；未下载媒体、特征或 checkpoint，未运行模型预测。

冻结结论：

- LongVALE：`n0_failed_data_or_metric_incompatible`。training 的 25% 预算总体 Oracle headroom 为 `14.4965pp`，低于 `15pp` 门槛；事件占比最高四分位的 headroom 仅 `0.9535pp`，低于 `10pp` 分层门槛。
- UnAV-100：`n0_failed_data_or_metric_incompatible`。train 的 25% 预算总体 headroom 为 `19.4441pp`，但事件占比最高四分位仅 `0.0075pp`；同时与显式历史 AVE/Stage2 清单重叠 `410` 个物理视频。
- LongVALE 与 UnAV-100 之间没有物理视频 ID 重叠；LongVALE 与本轮显式历史清单没有发现重叠。
- LongVALE training 有 `16` 条、eval 有 `2` 条事件终点因帧级小数舍入略超声明时长，最大偏差小于冻结的 `25ms` 容差，均已记录并钳制；没有静默丢弃事件。
- AVQA 本地分片尚未形成可审计的完整物理 ID 清单，因此跨 AVQA 污染审计未完成；该缺口不改变两个候选已先在动态范围门禁失败的结论。

当前决策：`N0_failed_stop_before_N1`。禁止下载 LongVALE/UnAV-100 媒体、运行 N1、运行冻结 M4 预测或训练 selector。若未来要只研究低事件占比子群，必须另立新计划和独立数据边界，不能事后修改本次 N0 的全分层门槛。
