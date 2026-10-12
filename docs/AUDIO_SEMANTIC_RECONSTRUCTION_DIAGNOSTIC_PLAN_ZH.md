# AS-M4 音频语义重构低成本诊断计划

日期：2026-07-25（Asia/Shanghai）

## 1. 计划定位与当前决策

本计划是 Dynamic Window Selector V2 的条件性 blocker 修复子计划，不是当前主执行入口，
也不是对旧计划的补丁。旧 checkpoint 的 dev760 门禁只证明该音频路径不适合解释 Oracle
下游效用；它没有否决 selector 的独立定位上界实验。

本计划只有在以下条件同时成立时才进入执行：

1. Selector V2 的 V1 已证明 Oracle Top-K 存在定位 headroom；
2. 准备进入 V2 下游效用时，冻结音频路径 selectivity 资格失败；
3. 用户授权修复该 blocker，且仍保持 M4/视觉塔/BEATs 主体冻结。

当前决策：

```text
selector_localization_route: ACTIVE_MEDIA_GATED
selector_oracle_downstream_route: BLOCKED_BY_AUDIO_PATH
joint_training: FORBIDDEN
formal_test: FORBIDDEN
plan_role: CONDITIONAL_BLOCKER_REMEDIATION
```

本子计划不替代“怎样选到更好的窗口”这一主问题，只负责依次回答：

1. 冻结 BEATs 表示是否包含可泛化的音频事件语义；
2. 当前 projector 是否保留、破坏或错误旋转了这些语义；
3. 当前答案表示是否是合法的音频语义目标；
4. 正确音频在不依赖 selector 的条件下是否能对回答产生可重复的因果增益；
5. 只有前四问通过后，才返回 Selector V2 的 V2 Oracle 下游效用，不直接跳到 learned selector。

## 2. 冻结失败证据

基础证据锁：

```text
intersuit/configs/audio_semantic_reconstruction_basis_lock.json
```

生成脚本：

```text
conda run -n M4 python intersuit/scripts/audit_audio_semantic_reconstruction_basis.py
```

### 2.1 dev760 已冻结结论

| 条件 | 正确数 | 相对 video-only gain/harm/net |
| --- | ---: | ---: |
| video-only | 254/760 | - |
| correct audio | 251/760 | 7/10/-3 |
| mismatched audio | 259/760 | 13/8/+5 |
| silence | 254/760 | 0/0/0 |
| gate0 | 254/760 | 0/0/0 |

- correct-only vs mismatch：5；
- mismatch-only vs correct：13；
- correct 平均音频残差/视觉特征范数比：`0.0005234380`；
- mismatch 对应值：`0.0005233470`；
- correct 与 mismatch 幅度近似相同，当前链路不具备语义选择性；
- dev760 已用于一次正式门禁，禁止作为新路线调参集或 gate sweep 集。

### 2.2 训练目标与数据的结构性问题

冻结 semantic-contrast 训练清单共 2430 条：

| 问题 | 数量 | 影响 |
| --- | ---: | --- |
| 缺少 `event_label` | 1230 | 无法保证负音频语义不同 |
| 负样本同样缺少 label | 1230 | “semantic negative”退化为不同媒体音频 |
| 纯视觉任务 | 753 | 音频被错误拉向视觉答案 |
| A–D 单字母答案 | 664 | 字母 embedding 不代表选择项语义 |
| train/dev 物理媒体重叠 | 0 | split 本身通过 |

当前实现把有效答案 token 的输入 embedding 无上下文平均后作为 target。这个 target 不包含
问题条件，也没有把多选字母映射回选择项文本。semantic ranking 直接约束 scene-audio
projector，但不直接约束 fusion；fusion 主要通过语言模型 loss 间接更新。推理时，
`beats_simple_residual` 又把同一个帧级音频残差广播到该帧全部空间视觉 token。训练目标和
推理接口因此没有形成可验证的一致语义契约。

### 2.3 已有旁证

AVE_HF_300 上已有音画 projector 诊断：

- 最小 4 类×4 视频可过拟合到 rank-first=1.0；
- 独立验证 semantic projector rank-first=0.4625，margin=-0.00523；
- balanced InfoNCE 一轮后 class Recall@1 提升，但 exact cross-label rank-first=0.3164，
  margin=-0.08333；
- 说明优化器与数值链路可工作，但跨视频泛化和训练目标仍失败；
- 既有代码把每个视频的全部 19–20 个窗口都复制为该视频事件标签，但本地 manifest 的
  `start_seconds` 是 YouTube 源片段偏移，不是可靠的本地事件边界；这些实验混入潜在背景
  窗口，是弱标签音画实例匹配，不等价于“事件窗口语义可分”，不能直接作为 R1 结论。

## 3. 故障假设树

```text
正确音频没有优于错配音频
├─ H1 冻结 BEATs 表示本身不可分
│  ├─ 标签/音频片段噪声
│  ├─ window pooling 丢失事件
│  └─ domain shift
├─ H2 projector 破坏可分语义
│  ├─ 目标空间错误
│  ├─ 样本/类别不均衡
│  └─ arbitrary negative 或 false negative
├─ H3 答案语义目标错误
│  ├─ A-D 字母
│  ├─ 纯视觉答案
│  ├─ 同义词/复合标签
│  └─ 无问题条件的 token 均值
├─ H4 fusion 接口错误
│  ├─ 音频伪装成视觉残差
│  ├─ 广播到所有空间 token
│  ├─ 融合位置过早
│  └─ 残差过小或无 modality identity
└─ H5 下游评分/生成协议不敏感
   ├─ label_terms 只测包含关系
   ├─ 生成答案同义但判错
   └─ audio-required 与 visual 子集混合稀释
```

实验必须按 H1→H5 顺序排除。不得通过放大 gate、同时解冻 projector/fusion/Qwen 或更换
大数据集跳过上游失败。

## 4. 数据集职责与资格

### 4.1 AVE_HF_EXPANDED：主诊断数据

当前本地状态：

- 1000 个视频与 1000 个 16 kHz 单声道音频均存在；
- 媒体有效率 1000/1000；
- 28 类 diverse-label 抽样；
- 既有 AVE_HF_300 包含 300 视频的窗口级 BEATs 768 维和 CLIP 1024 维特征；
- 只使用官方/派生的事件类别与物理视频 split，不使用模板 QA 作为语义 ground truth。

关键限制：本地字段只有源视频 clip 的 `start_seconds`，没有可靠事件起止区间。因此
AVE_HF_EXPANDED 只能做 clip-level 弱标签音频语义诊断；不得把某个 1 秒窗口宣称为官方
positive event window，也不得用全部窗口复制标签后声称完成 window-level 监督。

现有 300 样本音频特征由锁定 BEATs SHA 生成，但 contract 是
`window_sec=1.0, hop_sec=0.5`，与本计划正式 `1.0/1.0` 不一致。它们只能用于 evaluator
开发、读取和数值预检；不得直接产出正式 R1 指标。正式 R1 必须重提取 1.0/1.0 特征。

职责：R1 clip-level 冻结表示诊断、R2 目标表示诊断、R3 clip-level projector-only 小训练、
R4 轻量融合开发。

限制：这是本地诊断集合，不产生 AVE 官方 test 新结论。AVE D1 的 `test_failed` 永久冻结，
不得重跑或改判。

### 4.2 AS_M4_STAGE2：只读历史与下游 reserve 构建来源

- 旧 train/dev 只用于解释历史失败；
- 旧 dev760 不参与新路线调参；
- 缺 label、纯视觉和 A–D 字母样本不得进入 R1–R3 语义训练；
- 若 R5 需要人类 QA，只能从未被新路线训练、校准或旧 gate 搜索使用的物理媒体中另建
  `reserve_new` 与 `dev_new`，并在运行前冻结 SHA。

### 4.3 UnAV-100：外部事件语义验证候选

已验证：格式、标签映射、去污染 manifest 和官方 temporal mAP adapter。当前未验证：媒体。

```text
format_verified: true
metric_verified: true
media_verified: false
```

职责：媒体资格通过后，用 validation 做跨数据集音频事件可分性与 window-level 语义/定位
辅助验证；test 只在
结构、checkpoint、阈值全部冻结后一次性运行。媒体未通过前不得写成“可执行数据集”。

### 4.4 LongVALE：暂不进入本路线

LongVALE 的 caption 格式和官方 caption 指标已验证，但媒体未验证。它回答长视频 segment
captioning，不适合在 H1–H3 尚未通过时承担音频语义表示诊断。只有 R5 正确音频效用通过后，
才重新评估是否将其用于外部下游效用。

### 4.5 明确排除

- MUSIC-AVQA：物理视频 split 严重重叠且已有历史污染；
- AVUT：可以用于最终人类 QA 观察，但 A–D 字母不能作为音频语义训练目标；
- QVHighlights：不能单独证明音频语义；
- AVQA：媒体与官方指标链未完成资格验证；
- 任何只有 YouTube ID、未验证媒体可用性的集合：不得替代当前 blocker。

## 5. 完整执行工作流

### R0：证据、代码和数据 contract

目标：保证后续实验回答的是同一个问题。

必须完成：

1. 重新生成基础锁并核验 SHA；
2. 固定音频 window 为原始 1 秒、hop 1 秒、tail padding + `valid_sample_count`；
3. 固定物理媒体 split，train/validation/test 零重叠；
4. 建立 canonical label 表，保存原标签、规范标签、同义词和 label ID；
5. 多选题必须保存 `answer_letter` 与 `answer_choice_text`，语义目标只能用后者；
6. 每个样本保存 `audio_required`，纯视觉样本不得参与音频语义 loss；
7. 所有 evaluator 先过 golden fixture；
8. 锁定 seed、特征 extractor/checkpoint SHA、manifest SHA 和代码 SHA。

此外必须给监督粒度加显式字段：`supervision_level=clip|window`。AVE_HF 当前只能写
`clip`；没有可靠区间的数据不得构造 `window` positive/negative 标签。

门禁：任何字段缺失、媒体不可解码、split 重叠、label mapping 不稳定或 golden 失败，停止。

### R1：冻结音频表示可分性上界

不训练 M4，不接 fusion，不生成答案。依次评测：

| 表示 | 目的 |
| --- | --- |
| raw BEATs clip mean pool | 判断基础 encoder 是否包含 clip-level 类别信息 |
| raw BEATs clip robust pool | 检查均值是否被背景窗口稀释；规则必须预注册 |
| 当前 scene-audio projector 输出 | 判断 projector 是否破坏语义 |
| 随机线性 projector | 负对照 |
| Oracle label prototype | 指标上界与 evaluator sanity |

AVE_HF 的每个视频只生成一个 clip-level 表示。禁止把其 19–20 个窗口分别作为同一标签的
独立训练/评测样本。若使用 robust pool，只允许在训练 split 冻结规则（例如按 RMS 取固定
Top-M 后平均），validation 不参与选择 M。

主指标：

- macro class Recall@1；
- balanced accuracy；
- positive-vs-hard-cross-label rank-first；
- margin mean 与 bootstrap 95% CI；
- 每类 Recall@1、样本数和混淆矩阵；
- 3 个固定 seed 的均值、标准差与 worst seed。

R1 晋级条件必须同时满足：

```text
raw BEATs macro Recall@1 >= majority baseline + 20pp
raw BEATs rank-first >= 0.65
raw BEATs margin 95% CI lower bound > 0
至少 80% 合格类别 Recall@1 > random baseline
```

若 raw BEATs clip-level 失败：停止 projector/fusion/selector，优先修媒体片段、标签和
encoder；允许更换
数据集，但新数据集必须重新走 R0，不能把换数据当作自动修复。

若 raw 通过而当前 projector 失败：进入 R2/R3；若二者均通过，仍需 R2 验证目标空间。

### R2：答案与问题语义目标诊断

保持音频表示冻结，不训练模型。比较：

1. 旧目标：答案 token input embedding 均值；
2. canonical event-label 文本 embedding；
3. 多选项完整文本 embedding；
4. question + canonical answer 的条件表示；
5. label-description prototype（仅使用训练 split 文本定义）。

仅在 `audio_required=true` 且有 canonical event label 的样本上评测。不得包含纯视觉任务、
单字母答案或未知语义负样本。

主指标仍为 correct-vs-hard-negative rank-first、margin 与跨类 Recall@K。R2 晋级条件：至少一个
合法目标在 validation 上 rank-first≥0.65、margin CI 下界>0，并比旧目标提升≥10pp。

若没有目标通过：停止 fusion 训练，重新设计音频语义任务或引入经过资格审查的音频文本
encoder；不得直接调 gate。

### R3：最小 projector-only 训练

前提：R1 证明 raw BEATs 可分，R2 证明合法目标存在。

冻结：Qwen2/M4、视觉塔、BEATs、现有视频 projector、fusion、selector、gate。

只训练一个新编号的 clip-level audio semantic projector，旧 checkpoint 不覆盖。比较：

- supervised class prototype contrast；
- class-balanced masked InfoNCE；
- margin ranking with hard cross-label negatives；
- 不训练，作为 frozen baseline。

负样本必须满足：不同物理媒体、不同 canonical label、媒体有效、同 split；同类不同视频不得
当负样本。batch 必须类别均衡。

每个视频每个 epoch 只贡献一个由预注册 pooling 得到的 clip 表示，不能用窗口数量扩大样本量。
window-level projector 训练必须等待 UnAV 等带可靠区间的数据通过媒体资格。

执行阶梯：

```text
CPU/单卡 forward contract -> 2-step overfit -> 20-step -> 1 epoch -> 3 seeds
```

主门禁：

```text
validation rank-first >= 0.70
validation margin 95% CI lower bound >= 0.02
macro Recall@1 >= raw BEATs + 5pp，或在参数压缩条件下不低于 raw - 1pp
worst-seed rank-first >= 0.65
train-validation gap <= 20pp
```

R3 失败时停止，不接 M4。若训练集通过而验证集失败，判定泛化失败，不能用更多 step 掩盖。

### R4：融合位置的冻结下游探针

前提：R3 通过。仍不训练 Qwen2、视觉塔或 BEATs。使用同一 projector checkpoint，低成本比较：

| 方案 | 说明 |
| --- | --- |
| F0 video-only | 原始基线 |
| F1 current residual | 当前帧级残差广播，诊断对照 |
| F2 dedicated audio tokens | 音频作为有 modality identity 的独立前缀 token |
| F3 late gated summary | 在视觉压缩后、进入 LM 前加入少量音频 summary token |
| F4 mismatched/silence | 每个方案的因果负对照 |

此阶段先做无训练 logit/probe 实验；确有上界后只允许训练 fusion adapter，仍冻结 M4 主体。
每个方案必须保持相同音频窗口、projector、生成参数和样本顺序。

晋级条件：在新 reserve 上，correct 相对 video-only net≥0、correct>mismatch，且
correct-only>mismatch-only；silence 与 gate0 回退≥99%。若只有放大残差才改变输出，但
correct 不优于 mismatch，判定 fusion 无语义效用，停止。

### R5：新的下游因果效用门禁

不得复用 dev760 调参。先冻结 `reserve_new`、`dev_new`：

- 物理媒体与 R1–R4 训练/校准集合零重叠；
- 主报告只含 audio-required 和 audio-visual-required；
- visual-only 单独作为“不应受益/不应受损”安全集；
- correct、hard mismatch、same-class-different-instance、silence、video-only、gate0 六条件齐全；
- 先在 reserve_new 选择一次固定配置，再在 dev_new 一次性运行。

主门禁：

```text
correct accuracy >= video-only
correct accuracy > hard mismatch
correct net gain >= 0
correct net gain > mismatch net gain
correct-only > mismatch-only
audio-required 子集同样全部通过
silence/gate0 exact fallback >= 99%
```

同时报告 scorer sensitivity：严格 label、同义词 canonicalization、选择题 exact match。主判定
必须预注册一个 scorer，其他只做敏感性分析。

### R6：返回 Selector V2 主线

只有 R5 全部通过，才返回既有 Selector V2 计划，使用新冻结且合格的音频 checkpoint 运行
V2 Oracle Top-K vs All 下游效用。不得新建另一套 selector 定义，也不得直接跳到
question-conditioned selector-only 训练。任何 R1–R5 失败都不允许通过 learned selector 绕过。

## 6. 实验资源与冻结策略

- R0/R1/R2：优先 CPU 或已有离线特征；不得启动正式训练；
- R3：单卡开发，显式 `CUDA_VISIBLE_DEVICES=1` 或经检查后的单卡；
- R4/R5：先单卡 smoke；只有内存或吞吐确有必要才使用稳定 `0,2,3,4`；
- 所有 Python 命令必须 `conda run -n M4`；
- 任何 GPU 实验前运行 preflight，保存 stdout/stderr/config/start time，结束后检查内核异常；
- 不覆盖 baseline、旧 semantic checkpoint 或 selector lock；
- 不提交媒体、特征、checkpoint、缓存和日志。

## 7. 产物契约

每个阶段使用独立目录：

```text
intersuit/harness/artifacts/audio_semantic_reconstruction_r{stage}_{name}/
```

必须包含：

- `config.json`；
- `manifest_lock.json`；
- `metrics.json`；
- `per_sample.jsonl`；
- `review.md`；
- `decision.json`；
- checkpoint SHA（若有）；
- 运行环境版本与 Git commit；
- PASS/FAIL 和明确下一步。

FAIL 不删除结论性 metrics/review；只清理可重建的临时特征和无用短步 checkpoint。

## 8. 停止条件与决策表

| 最早失败阶段 | 结论 | 下一步 |
| --- | --- | --- |
| R0 | 数据/contract 不可信 | 修数据与 evaluator |
| R1 raw BEATs 失败 | encoder/数据不具备语义基础 | 修片段/标签，或审查新数据/encoder |
| R2 失败 | 答案语义目标不成立 | 重构目标表示，不训练 fusion |
| R3 失败 | projector 不泛化 | 停止当前 projector 目标 |
| R4 失败 | 融合接口不能利用语义 | 重构 fusion 位置或独立音频 token |
| R5 失败 | 下游无因果效用 | 不恢复 selector |
| R5 通过 | 正确音频路径成立 | 返回 Selector V2 的 Oracle 下游效用 |

任何阶段都禁止用以下方式“救结果”：

- 在正式 dev/test 上继续 sweep；
- 同时解冻全部新增模块；
- 用更大数据集替代失败原因分析；
- 将 visual-only 与 audio-required 混合后只报总分；
- 只报告正确音频有若干独有样本而忽略 mismatch 更高；
- 修改 scorer 后追认旧结果通过。

## 9. 本子计划的进入条件与首步

当前主线尚未完成 Selector V2 的 V1，因此本子计划目前不应抢先执行。待 V1 Oracle 定位
headroom 通过后，本子计划才授权进入 R0→R1 的离线诊断准备：

1. 冻结 AVE_HF_EXPANDED 1000 媒体的物理 ID split、canonical label mapping 和
   `supervision_level=clip`；
2. 使用已有 300 样本 1.0/0.5 特征开发 evaluator 与 golden，但不产生正式指标；
3. 为同一 300 媒体按锁定 BEATs SHA 重提取 1.0/1.0 特征并冻结 manifest；
4. 实现每视频一个表示、不训练的 audio-only class centroid/retrieval evaluator 与 golden；
5. 先运行 300 样本正式 contract pilot；
6. pilot 通过后再为剩余合格媒体提取特征；
7. 在 R1 decision review 前，不实现 projector 训练、fusion 修改或 selector。

## 10. 2026-07-27 实际执行状态

本子计划已在 Selector V1 全量通过后进入执行，并严格停止于 R2：

```text
R0: PASS
R1 pilot300: PASS
R1 expanded train/validation: PASS
R2: FAIL
R3-R6: NOT_AUTHORIZED
formal_test: NOT_ACCESSED
training: NOT_PERFORMED
```

R1 证明冻结 BEATs 表示和当前 scene-audio projector 均保留 clip-level 事件类别结构；
R2 则证明当前 projector 输出没有与旧 Qwen assistant-answer token 目标或三种预注册合法
文本目标形成可泛化对齐。没有合法目标通过 `rank-first`、margin CI 和相对旧目标提升的
合取门禁。

因此执行第 8 节的 R2 停止规则：不得进入 R3 projector-only 训练，不得修改 fusion，
不得运行 gate sweep、selector 或 V2 LongVALE。后续若要继续，必须先提出新的、可资格
审查的音频语义目标（例如独立音频文本 encoder），更新计划并重新走 R2；不能用当前失败
结果授权训练。

完整执行 review：
`docs/AUDIO_SEMANTIC_RECONSTRUCTION_R0_R2_EXECUTION_REVIEW_ZH.md`。
