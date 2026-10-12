# Dynamic Window Selector 独立重启与长视频稀疏事件诊断计划

日期：2026-07-25（Asia/Shanghai）

主线更新：2026-08-02（Asia/Shanghai）

计划版本：`restart v1.2 + OpenFLAM multi-domain fallback`

## 1. 当前状态与目标

AVE 官方 D1 已按原计划一次性执行，正式结论为 `test_failed`：Oracle 与五个 Random seed 的 Hit@4 差值为 `8.8571pp`，低于预注册的 `10pp` 门槛。该结论永久冻结，不因本计划改变。

本计划是独立的新研究路线，不是原 D2 的继续，也不重开 AVE D1。目标是回答两个更窄的问题：

1. 长视频、稀疏事件数据是否为窗口选择提供足够的随机基线动态范围；
2. 已知正确音频事件窗口是否能改善冻结 M4 的下游事件描述，再决定是否值得训练 question-conditioned selector。

在 N0 数据适配门禁通过以前，禁止下载新媒体、提取特征、运行模型预测、访问任何新 test 或训练 selector。

截至 2026-07-28，N0 已按第 11 节冻结为 `N0_failed_stop_before_N1`。因此 LongVALE /
UnAV-100 selector 重启路线已经停止，N1–N3 未获授权。当前主线动作改为第 12 节定义的
OpenFLAM 音频语义修复分支；该分支不是 N1 的替代实现，也不能反向追认 AVE D1 或 N0 通过。

## 2. 永久冻结边界

以下事实不得被本计划覆盖：

- AVE D1 official test 只能访问一次，禁止重跑、改 K、改阈值、改 Random seed 或改 scorer 后重新判定；
- 原 `D2 LongVALE`、D3、D4 均因 D1 失败而不允许启动；
- AVE D1 的 test 结果只能作为历史诊断证据，不能作为新路线的 validation/test；
- `AVE_HF_*`、`AS_M4_STAGE2`、`dev760`、历史 temporal scorer 和已经观察过的本地集合均不具备新路线正式 test 资格；
- 任何新数据集都必须重新生成来源、split、物理视频 ID、窗口 schema、媒体 SHA 和污染审计；
- 新路线不能把新数据集的结果与 AVE 指标加权成一个总分，也不能用新结果追认 AVE D1 通过。

计划启动时的本地盘点未发现 LongVALE 或 UnAV-100 的官方标注、split、特征或媒体目录；
随后 N0 只取得并审计了第 11 节记录的官方轻量标注元数据，仍未下载媒体或特征。已有的
AVQA 分片、AVE 和历史 AS-M4 资产不满足本路线的替代条件。

## 3. 阶段与停止关系

```text
N0 元数据与动态范围审计
  ├─ fail：停止 selector 新路线，保留 D1 失败；只允许转入第 12 节音频语义修复
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
- N0–N3 selector 路线与后续学生/M4 操作使用 `conda run -n M4`；
- 第 12 节 OpenFLAM 教师推理只使用隔离的 `openflam_teacher` 环境，并通过磁盘 tensor/JSON
  与 M4 交换；禁止在 `M4` 环境安装 OpenFLAM 依赖；
- 所有 GPU 命令显式设置 `CUDA_VISIBLE_DEVICES`；
- N0 不使用 GPU；N1 之前不下载媒体；
- test 访问前冻结 runner、manifest、配置、prompt、scorer 和资产资格；
- checkpoint、媒体、数据集、特征、缓存、预测和日志不得加入 Git；
- 每阶段结束执行 contract、数据/test、指标公平性、运行产物和 Git 范围 review。

## 10. N0 产物与历史首个动作

独立产物目录固定为：

```text
intersuit/harness/artifacts/dynamic_selector_restart_n0/
intersuit/harness/artifacts/dynamic_selector_restart_n1/
intersuit/harness/artifacts/dynamic_selector_restart_n2/
intersuit/harness/artifacts/dynamic_selector_restart_n3/
```

N0 执行前冻结的首个动作只生成：

- `candidate_sources.json`；
- `metadata_manifest.jsonl`；
- `physical_id_cross_audit.json`；
- `dynamic_range_report.json`；
- 中文 `report.md`；
- `review.md`。

这些产物已形成第 11 节的冻结失败结论，因此不得创建 N1 目录中的预测或特征资产。
第 12.5 节的 OpenFLAM A1 轻量资产审计也已完成；EPIC-SOUNDS 媒体已单独获准按流式
HDF5 事务准备，但正式教师资格和学生训练仍未授权。

## 11. N0 冻结结果

N0 已使用官方 LongVALE annotation JSON 和 UnAV-100 annotation parquet 完成元数据审计；未下载媒体、特征或 checkpoint，未运行模型预测。

冻结结论：

- LongVALE：`n0_failed_data_or_metric_incompatible`。training 的 25% 预算总体 Oracle headroom 为 `14.4965pp`，低于 `15pp` 门槛；事件占比最高四分位的 headroom 仅 `0.9535pp`，低于 `10pp` 分层门槛。
- UnAV-100：`n0_failed_data_or_metric_incompatible`。train 的 25% 预算总体 headroom 为 `19.4441pp`，但事件占比最高四分位仅 `0.0075pp`；同时与显式历史 AVE/Stage2 清单重叠 `410` 个物理视频。
- LongVALE 与 UnAV-100 之间没有物理视频 ID 重叠；LongVALE 与本轮显式历史清单没有发现重叠。
- LongVALE training 有 `16` 条、eval 有 `2` 条事件终点因帧级小数舍入略超声明时长，最大偏差小于冻结的 `25ms` 容差，均已记录并钳制；没有静默丢弃事件。
- AVQA 本地分片尚未形成可审计的完整物理 ID 清单，因此跨 AVQA 污染审计未完成；该缺口不改变两个候选已先在动态范围门禁失败的结论。

当前决策：`N0_failed_stop_before_N1`。禁止下载 LongVALE/UnAV-100 媒体、运行 N1、运行冻结 M4 预测或训练 selector。若未来要只研究低事件占比子群，必须另立新计划和独立数据边界，不能事后修改本次 N0 的全分层门槛。

## 12. N0 失败后的 OpenFLAM 音频语义修复主线

### 12.1 路线定位

本节落实第 3、6、7 节中的既定回退方向：selector 数据与指标门禁失败后，先修复音频语义
目标、答案表示和融合位置，再决定是否值得重新研究窗口选择。

OpenFLAM 在这条路线中是**冻结、离线、研究期音频—文本语义教师**，不是 selector 本体：

```text
OpenFLAM audio encoder: audio -> shared 512-d embedding
OpenFLAM text encoder: canonical prompt -> shared 512-d embedding
```

它只用于离线提供事件语义先验和 evaluator。它不得：

- 直接决定最终答案或 question relevance；
- 把 top-1 事件当作精确伪标签；
- 把 32 个 local embeddings 自动解释成 32 个精确时间窗；
- 替代 BEATs、M4/Qwen2、视觉塔、fusion、gate 或 selector；
- 进入正式 M4 推理 graph、正式学生 checkpoint 或在线依赖；
- 因回顾性指标较高而直接授权 projector、bridge、fusion、gate 或 selector 训练。

正式推理仍只保留 BEATs、合格的学生 projector/bridge 和后续单独获准的窗口策略。

OpenFLAM 的详细执行契约位于分支 `codex/openflam-teacher`：

```text
docs/OPENFLAM_AUDIO_SEMANTIC_TEACHER_ADAPTATION_PLAN_ZH.md
基线 commit: d6f5154
A1 元数据资格 commit: 41c639f
A1.5 媒体容量预检 commit: 4483f14
A1.6 流式 HDF5 事务与 canary commit: 32b539f
A1.6 实时进度监控与 8-worker 状态 commit: 2eac23b
```

该分支的低层工程锁、预处理和教师指标服从其阶段 `decision.json`；本文件负责冻结它与
Dynamic Window Selector 主线的关系和返回条件。任何 OpenFLAM 结果都不能改写本文件第 1、
2、11 节的历史结论。

### 12.2 当前已完成证据与正式状态

截至本次更新：

```text
OpenFLAM 独立环境与官方资产锁：PASS
CPU T0 冻结推理：PASS
O-T0H 源码等价、完整 state_dict、local scorer：PASS
纯 CUDA 音频前端：FAIL，禁止正式提取
CPU 音频前端 + GPU HTSAT/投影：PASS，已锁定
同一已消费 AVE validation 上相对 CLAP：+17.3469pp，回顾性胜出
A1 双轴轻量元数据审计：PASS
A1.6/A1.8 正式媒体与 teacher view：PASS
Q-general / STARSS23 clip + T2C：PASS
Q-target / EPIC-SOUNDS clip + T2C：FAIL
v1.1 双轴正式聚合：TEACHER_REJECTED
v1.2 核心多域资格：NOT_QUALIFIED，等待全新 A1-MD 资产审计
学生训练：未授权
test_locked：未访问
```

回顾性 AVE 比较只允许把 OpenFLAM 定为下一轮主候选，不能写成正式教师 PASS。原因是 AVE
来自 AudioSet，而公开 OpenFLAM 使用 AudioSet 派生数据和 AudioSet Strong 训练；官方没有
提供完整训练 physical media manifest，预训练物理重叠只能记为 `UNKNOWN_POSSIBLE`。

当前冻结状态为：

```text
historical_v1_1_decision = TEACHER_REJECTED
general_capability = PASS
kitchen_domain_capability = FAIL
core_multidomain_capability = NOT_QUALIFIED
student_training_authorized = false
selector_training_authorized = false
```

### 12.3 三类数据必须分开

后续不得再用“新数据”同时指教师考试、学生训练和 selector 评测。三类数据的职责固定为：

#### A. 教师资格数据

只用于冻结 OpenFLAM 推理和 PASS/FAIL，不训练任何参数：

```text
Q-general
  -> 教师公开预训练来源外的通用音频语义、置信度和真实时间定位资格

Q-target
  -> 非 AudioSet 来源、接近 AS-M4 目标域的新媒体迁移资格
```

`Q-general` 与 `Q-target` 必须物理隔离、分别一次性评估，并同时通过 clip 语义与置信度
门禁，才能授权学生 clip projector。若要使用 local/window 教师目标，还必须有独立真实
segment 时间标注并通过时序门禁。

ASFX-SED 已在 A1 轻量审计中排除，不得作为 `Q-general`。FLAM 论文虽然声明其
event/background 在训练时未见，但同一论文已经用它报告主表、检索、消融、阈值曲线和附录
结果；它属于教师作者已经消费的评测 benchmark，不是新的独立资格考试。

2026-07-28 已在任何正式教师推理前冻结两套替代资产：

```text
Q-general = STARSS23 v1.1.0，MIT
  metadata SHA256 =
    2fa848545a520b4a9d490459de7f079aa49ae16daf0affc859c016f5146981d6
  development = 官方 dev-train，90 clips
  qualification = rooms 2/10/16/23，43 clips
  confirm = rooms 8/15/24，35 clips
  13 类真实 100 ms 强标注；class 0..10 为硬门槛，11/12 只诊断

Q-target = EPIC-SOUNDS commit 57a922f...，CC BY-NC 4.0
  annotation SHA256 =
    eec068f121a52602f285f3aa888029d8cabe1b667ed90ab8f99db5226a7ce3f2
  development = 官方 train，495 videos / 60055 events
  qualification = validation video hash 偶数侧，75 videos / 4402 events
  confirm = validation video hash 奇数侧，62 videos / 3633 events
  两侧 video overlap = 0；18 个足量类别为硬门槛，其余只诊断
```

EPIC-SOUNDS 的冻结规则是 `sha256("20260728:" + video_id) % 2`。两套来源均不在
OpenFLAM 已声明训练源和 FLAM 论文/仓库评测表中，物理来源也不同；但官方 README 使用
`including` 且未公开逐媒体训练 manifest，所以只能写“无已声明 dataset-level 重叠”，
未声明训练重叠仍为 `not independently verifiable`，不能宣称绝对零风险。

以下资产不得改名或重切后冒充新教师资格集：

- AVE、AVE_HF、原 196 条 validation 与 `test_locked`；
- 已参与教师开发的 524 development 和 84 confirm；
- LongVALE / UnAV-100（其 selector N0 失败结论与用途永久冻结）；
- AVQA、AVUT、MUSIC-AVQA、AS_M4_STAGE2、dev760 和已观察历史 harness。

#### B. 学生训练数据

只有教师资格通过后才允许建立。它用于训练 BEATs 后的 semantic projector、scene-audio
bridge 或其他明确白名单模块。OpenFLAM 始终冻结，优先离线预计算目标，不参与反向传播。

学生训练集不得与 `Q-general`、`Q-target` 的正式 qualification/confirm 物理重叠，也不得
把教师资格集同时用于阈值选择、早停和正式报告。低置信 teacher audio target 必须拒绝或
降权；canonical text prototype、teacher clip relational target 和 local/window target
必须按教师能力锁分别授权。

#### C. Selector 返回评测数据

只有音频语义链通过第 12.4 节的下游因果效用门禁后，才能另立计划寻找。它仍需重新执行
类似 N0 的动态范围、污染、timestamp、caption 和 Oracle headroom 审计。不得自动复用
LongVALE、UnAV-100 或教师资格数据，也不得把教师资格 PASS 当成 selector 数据适配 PASS。

### 12.4 阶段与逐级授权

后续固定为：

```text
A0 OpenFLAM O-T0/T0H 工程资格
  -> 已完成 PASS，但不授权训练

A1 Q-general / Q-target 元数据、许可证、预训练源和 split 审计
  ├─ 任一轴无合法资产：formal NOT_QUALIFIED，停止学生训练
  └─ 两轴资产锁定
       ↓
A2 OpenFLAM O-T1/T2 正式教师资格
  ├─ 任一轴 clip/T2C FAIL：淘汰或停止教师路线
  ├─ clip/T2C PASS、window FAIL/NOT_QUALIFIED：只允许 clip/text + MIL
  └─ clip/T2C/window PASS：允许申请 clip + local/window 目标
       ↓
A3 BEATs 学生 semantic projector（O-R3C/R3W）
  ├─ FAIL：停止 bridge/fusion/selector
  └─ PASS
       ↓
A4 scene-audio token 与 Qwen bridge（O-R4E/R4B）
  ├─ contract/目标 FAIL：停止下游接入
  └─ PASS
       ↓
A5 下游因果效用（O-R5）
  ├─ correct audio 不优于 mismatch/shift/silence：停止 selector
  └─ 音频语义产生稳定、可归因的回答收益
       ↓
A6 返回 selector 的 Oracle 资格
  -> 另立新数据与新 N0/N1/N2/N3 计划
  -> Oracle PASS 后才允许讨论 learned question-conditioned selector
```

授权约束：

1. A0 PASS 只说明教师工程路径可复现。
2. 只有 `Q-general PASS AND Q-target PASS` 才能授权 O-R3C。
3. O-T2W 失败时禁止 teacher local/window embedding，但不自动否定已通过的 clip/text 路线。
4. O-R3C/R3W PASS 只证明学生表征可学，不能证明 Qwen 会使用 audio token。
5. O-R4E/R4B PASS 只证明接口与显式目标成立，不能证明回答质量受益。
6. O-R5 必须包含 correct、mismatch、shift、silence、text-only 和 All audio 对照；只有
   correct audio 的配对收益稳定为正，才允许进入 A6。
7. A6 必须重新审计数据动态范围和 Oracle headroom，不能恢复本次失败的 N1–N3。

### 12.5 当前停止点与下一动作

A1 的轻量资产审计与计划冻结已完成，标准产物目录为：

```text
intersuit/harness/artifacts/openflam_teacher_q_asset_audit/
```

已生成：

- `candidate_sources.json`：Q-general/Q-target 官方候选、版本和 URL；
- `license_and_usage_review.json`：许可证、研究/发布边界；
- `teacher_pretraining_source_exclusion.json`：与 OpenFLAM 公开训练数据源的排除审计；
- `ontology_mapping_proposal.json`：事件标签、同义词、不可映射类别；
- `physical_overlap_audit_plan.json`：与所有历史媒体的 ID/SHA 审计方法；
- `split_and_power_proposal.json`：development/qualification/confirm 与统计功效；
- `decision.json`、中文 `report.md` 和 `review.md`。

当前 decision 已输出：

```text
status = asset_metadata_review_passed_stop_before_media
asset_metadata_review_passed = true
q_general_metadata_locked = true
q_target_metadata_locked = true
media_download_authorized = false
formal_teacher_qualification_authorized = false
student_training_authorized = false
selector_training_authorized = false
```

这个 A1 decision 只解除“没有合法候选”的 blocker，不构成 `Q-general PASS` 或
`Q-target PASS`。它规定主线单独授权后才可下载冻结媒体；下载后仍须先做官方媒体 ID、
原始 SHA256、确定性解码 waveform SHA256、时长、真实 segment、许可证和
qualification/confirm 零物理重叠审计。上述审计通过后仍需单独授权 A2 正式教师资格，
不能凭项目页描述或 A1 元数据结果直接运行 OpenFLAM。

2026-07-28 获准继续后的 A1.5/A1.6 媒体状态为：

```text
STARSS23 foa_dev.zip：
  3406085425 bytes
  official MD5 316b834ee6393c22862c314cc3c7ebb0 = PASS
  local SHA256 b8a4a51409982bd567529f06bd37399f6b404cdf035cb7adce52eb32d12bc81b
  ZIP full CRC = PASS，168 WAV
  原始 archive 已锁；尚未解压、解码或运行教师

EPIC-SOUNDS 632 个对应官方视频：
  development = 495 files / 866.995 GiB
  qualification = 75 files / 93.944 GiB
  confirm = 62 files / 77.198 GiB
  total = 1114690667253 bytes / 1038.137 GiB
  632/632 均有官方 URL 和 MD5
  全量原视频禁止同时落盘
  status = streaming_hdf5_running
  service = openflam-epic-hdf5-v3.service
  workers = 8
  minimum free space = 150 GiB
  30 秒聚合实测速率 = 4.50 MiB/s
  瞬时 ETA = 约 2 天 17 小时

P03_15 完整 canary：
  official MP4 bytes = 39548685
  official MD5 539850bfd3ec4afe59266f31088953f0 = PASS
  HDF5 = key P03_15 / shape [251904] / float32 / 24000 Hz
  shard SHA256 ac0b4b438a5062d9379590d478276c231996bccbcddd61e48be32c9d64151e96
  HDF5 full readback = PASS
  MP4/WAV delete after verify = PASS
  failure atomicity/resume/disk threshold tests = 6/6 PASS
```

完整 EPIC 原视频本地并存仍然禁止。已冻结的流式事务固定为：
`.part 断点续传 -> 官方 MD5 -> FFmpeg 24 kHz/mono/PCM16 -> 每视频 float32 HDF5 shard
-> 原子提交和全量回读 -> fsync 账本 -> 删除 MP4/WAV`。632 个 train+validation shard
全部通过后才合并并验证单体 `EPIC_audio.hdf5`；额外 44 个 recognition test 视频不在
本次范围，不得下载或访问。任一 worker 失败时不再派发新视频，保留 `.part` 或已验证源
文件供恢复。

首轮长期下载受 Bristol 单连接限速影响，已从 4 workers 安全断点切换到 8 workers；
当时 30 秒采样为 4.50 MiB/s、ETA 约 2 天 17 小时。该 transient service 随后于
2026-07-28 06:02 因读取超时且没有重试而停止在 16/632。

OpenFLAM 分支已用 commit `4d3275b` 增加断点网络重试、提前 EOF 检测、失败视频账本和
受限自动重启 unit，并用 commit `fddf04b` 改为 `HTTPResponse.read1()` 增量落盘。
31 项 OpenFLAM CPU 测试和官方 `P01_01` 最后 1 字节真实 Range canary 通过。持久
8-worker service 于 18:36:15 恢复后保持 `active/running`，三路真实超时均从原断点
自动重试，没有触发服务重启。两次 20 秒聚合速度为 175.98 KiB/s 和 168.78 KiB/s；
独立官方源 1 MiB Range 探针也只有约 19.7 KiB/s，说明当前约 70–73 天的瞬时 ETA
来自 Bristol 官方源/链路限速，不是 Python 下载器再次停滞。不得为了追求速度违反冻结
协议增加 worker 或切换到未审计来源。

2026-07-28 19:52 在用户明确要求后，下载源仍保持同一 Bristol 官方 URL 和官方 MD5
门禁，仅把传输出口从本机直连切换为显式 Mihomo mixed proxy
`http://127.0.0.1:7890`。下载器新增 `--proxy-url`，不读取继承的代理环境变量；8 个
既有 `.part` 均从原字节位置续传。Mihomo 单连接 1 MiB Range 探针约 210.7 KiB/s，
切换后的两轮 8-worker 聚合采样分别约 4.19 MiB/s 和 4.28 MiB/s，瞬时 ETA 降至约
2 天 19–20 小时。服务保持 `active/running`、`NRestarts=0`，Python 下载连接只到
本机 `127.0.0.1:7890`；代理切换没有改变媒体来源、MD5、解码或 HDF5 验证契约。

2026-07-29 发现原有“单视频 5 次瞬态重试 + 任一 worker 失败后停止派发”的组合会在
Mihomo 长连接偶发提前 EOF 时逐步耗尽 worker：`P02_09`、`P02_109`、`P01_18`
耗尽重试后，联网 worker 一度只剩 2 路，聚合速度约 1.14 MiB/s。commit `0d28a7f`
把 `--download-retries -1` 定义为仅对 Timeout、提前 EOF、连接错误和可重试 HTTP
状态进行无限断点重试；404、其他非瞬态错误和最终 MD5 不匹配仍会失败。无损重启后
8 条代理连接恢复，8 个 `.part` 同时增长，聚合约 4.48 MiB/s，瞬时 ETA 约 2 天
11 小时。

`intersuit/scripts/watch_epic_sounds_progress.py` 显示已验证 HDF5 数量、精确总源视频字节
百分比、聚合实时速度、每路速度和 ETA。服务运行只说明媒体准备已开始，不构成 Q-target
媒体 PASS；必须等最终 HDF5 key 集合、dtype、样本数、SHA、标注索引和 split 零重叠审计
完成后，才能申请 A2。

当前禁止：

- 访问 AVE/旧 validation/test_locked 继续调 OpenFLAM；
- 训练 OpenFLAM、BEATs projector、bridge、fusion、gate 或 selector；
- 把 OpenFLAM 事件相似度当作 question relevance；
- 因回顾性 `+17.3469pp` 调低资格门槛；
- 下载 LongVALE/UnAV-100 媒体或恢复 N1；
- 创建 learned selector 训练配置。

### 12.6 停止与回退

| 失败位置 | 必须停止 | 允许返回 |
| --- | --- | --- |
| A1 无合法 Q-general/Q-target | 全部学生训练 | 新来源元数据审计 |
| A2 clip 或 T2C FAIL | projector/bridge/fusion/selector | development 根因分析；不得复用同一 qualification 调门槛 |
| A2 window FAIL/NOT_QUALIFIED | local/window teacher | clip/text prototype + MIL |
| A3 projector FAIL | bridge 与下游接入 | projector 目标和数据粒度 |
| A4 token/bridge FAIL | fusion 与 selector | contract、答案表示和显式目标 |
| A5 无因果收益 | selector 全路线 | 音频目标、答案表示和融合位置 |
| A6 新数据 N0/Oracle FAIL | learned selector | 新的独立研究问题，不追认历史结果 |

任何阶段失败都必须保留行为回退：`enable_scene_audio=0` 时插入 0 个 scene-audio token；
权重回退必须显式加载保留的 12k/32k no-freeze 基线，不能用“冻结参数”冒充恢复旧权重。

许可证边界同样是硬停止条件。OpenFLAM 采用 Adobe Research License，仅限非商业研究；
OpenFLAM 特征、蒸馏学生权重和相关 checkpoint 在独立许可证 review 前统一标记
`research_only_noncommercial`，不得混入商业发布包。

### 12.7 通用 AS-M4 的多域教师资格修订（v1.2）

本节在 2026-08-02 的 Q-general/Q-target 正式事务完成后新增，因此只约束未来新实验，
不回顾性修改第 12.2～12.6 节所记录的预注册规则、原始指标或阶段 decision。

用户已明确最终 AS-M4 **不是厨房专用模型**。EPIC-SOUNDS 来自 EPIC-KITCHENS-100 的
第一视角厨房录音，其 44 类重点是厨房动作、物体交互和材料碰撞。它适合作为厨房域压力
测试，但不能单独代表通用 AS-M4 的完整目标分布。旧 v1.1 以
`Q-general PASS AND EPIC Q-target PASS` 作为全局授权条件，所得
`TEACHER_REJECTED` 仍是该版本下有效且不可改写的历史结论；其可解释范围固定为：

```text
公开 OpenFLAM：STARSS23 通用语义/置信度 PASS
公开 OpenFLAM：EPIC-SOUNDS 厨房细粒度域 FAIL
旧双轴全局授权：TEACHER_REJECTED
不能外推为：OpenFLAM 在所有通用 AS-M4 场景均无教师价值
```

未来若重启教师路线，资格结构改为三层：

```text
Q-general
  -> 教师预训练来源外的通用声音语义与置信度基础资格

Q-core-multidomain
  -> 与最终 AS-M4 数据配比一致的多域核心资格
  -> 至少覆盖：非厨房室内、户外/交通、机械/工具、动物/自然、音乐/人声中的四组
  -> 每个物理媒体只属于一个 qualification/confirm split

Q-domain-diagnostic
  -> EPIC-SOUNDS 厨房域及其他专项压力测试
  -> 产生域级能力锁，不再单独拥有全局否决权
```

新版授权必须满足：

1. Q-general 保持 PASS，且其资产、输出和历史 decision 不被重新切分或调参；
2. Q-core-multidomain 使用全新、合法、未被当前开发过程消费的物理媒体，并在任何教师输出
   前冻结来源、领域构成、类别映射、prompt、门槛、bootstrap、qualification/confirm 和账本；
3. 每个核心域单独报告 rank-first、macro recall、相对 random/majority 的增益、margin CI、
   T2C coverage、增强稳定性和物理 cluster 覆盖，禁止只报混合总体均值；
4. 全局门槛按最终 AS-M4 的冻结任务权重聚合，同时设置域级最低线和 catastrophic-fail 规则；
   具体数值只能由独立 development split 预注册，不能从 qualification/confirm 结果倒推；
5. 某个 Q-domain-diagnostic 失败时，只把该域写入 `disallowed_teacher_domains`，禁止为该域
   生成学生伪标签或宣称教师合格；它不再自动淘汰已通过的通用/核心域能力；
6. 若某个域属于最终模型的明确核心部署分布，则必须在运行前将其提升为
   Q-core-multidomain 硬门槛，不能在失败后降级成 diagnostic；
7. 在新的多域资产、预注册门槛、执行器、聚合器和独立 review 全部完成前，
   `student_training_authorized=false`，不得仅凭 STARSS23 PASS 直接训练 projector；
8. 多域教师资格通过只授权限定域内的离线 clip/text relational target。是否接入 AS-M4
   仍须依次通过 O-R3C、O-R4E/O-R4B 和 correct/mismatch/shift/silence/video-only 的 O-R5
   下游因果效用门禁。

建议的新状态字段为：

```json
{
  "historical_v1_1_decision": "TEACHER_REJECTED",
  "general_capability": "PASS",
  "kitchen_domain_capability": "FAIL",
  "core_multidomain_capability": "NOT_QUALIFIED",
  "allowed_teacher_domains": [],
  "disallowed_teacher_domains": ["kitchen_fine_grained_actions"],
  "student_training_authorized": false,
  "authorized_next_stage": "A1_MULTIDOMAIN_ASSET_AUDIT"
}
```

第 12.3、12.4 和 12.6 节中的“双轴任一失败即全局停止”继续解释旧 v1.1 事务；未来新事务
以本节的分层、多域、域级白名单规则为准。不得覆盖旧 decision，必须使用新的 run name、
artifact root、计划版本和 Git commit。
