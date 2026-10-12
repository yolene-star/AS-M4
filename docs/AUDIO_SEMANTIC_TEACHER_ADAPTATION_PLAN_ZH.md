# AS-M4 音频语义教师资格审查与端到端适配计划

日期：2026-07-27（Asia/Shanghai）

计划版本：`R2+ v1.2`

## 1. 计划定位与当前状态

本计划是以下三条主线之间的强约束连接层：

- `AUDIO_SEMANTIC_RECONSTRUCTION_DIAGNOSTIC_PLAN_ZH.md`：R2 音频文本目标重构；
- `AS_M4_improvement_plan_zh.md`：冻结教师、可训练学生及音频语义先验边界；
- `DYNAMIC_WINDOW_SELECTOR_DIAGNOSTIC_TRAINING_PLAN_V2_ZH.md`：Oracle selector 下游效用与 learned selector 授权。

当前状态为：

```text
R0 数据契约：PASS
R1 原始 BEATs 表征审计：PASS
旧 R2 Qwen token 文本目标：FAIL
CLAP 官方资产与 checkpoint lock：PASS
旧 R2-T0v1：已运行，但因错误复用仅覆盖 20/28 类的 pilot300 而撤销授权
新 R2-T0v2：PASS；使用全部 524 条、28/28 类、无抽样
R2-Q/T1：PASS
R2-Q/T2C：FAIL（Banjo/Mandolin/Shofar 的可用样本数低于预注册每类 3 条门禁）
expanded validation：唯一一次正式事务已消费
test_locked：未访问
R3 及其后续：未获授权
```

2026-07-27 数据覆盖事故复核：

- 旧 `pilot300` 只加载了 11 个 parquet 中的前 6 个，只覆盖 20/28 类；
- 其 300 条媒体全部可解码，但类别分布严重不平衡，不能作为正式教师开发集；
- 旧 T0v1 的教师资产 SHA、环境 smoke 和代码审计仍可继承，但 prompt、padding、增强、
  置信度及其选择结论全部撤销；
- validation/test_locked 尚未访问，因此允许在 train 内版本化重做 T0v2；
- T0v2 禁止抽样、限量或回退到 pilot，必须使用排除 `teacher_confirm` 后的全部 524 条 train。

2026-07-27 最终只读 readiness review：

| 检查项 | 结果 |
| --- | --- |
| 旧 R2 `per_sample.jsonl` | 已存在，784 行；其中 `old_answer_token_mean` 恰好 196 个唯一 physical IDs，无重复 |
| 旧 R2 per-sample SHA256 | `ae1698784d96e67c6b3bc3e9c6e3a13cafbd698952c5111cef64010df6b65398` |
| `old_r2_baseline_lock.json` | 已生成并通过逐样本 golden 重算，T0v2 SHA256 `fc38b3d20ba9f360b6b999cc18025a7aca181671a7d432830ed5342b09650ffc` |
| teacher_confirm manifest | 已生成 84 条，每类 3 条，SHA256 `a80927bf91303791e54b15959562e9e21731761052094de64f34358742fc9f5d` |
| 排除 confirm 后 student train manifest | 已生成 524 条，SHA256 `60ae099d61bb8a7b7365c0a88817573e7f89662cd4ec89afb138f349b9d61f4f` |
| 完整 teacher-development train | 已生成 524 条、28/28 类、无抽样，SHA256 `7848bea54c3cc0ac0af181f14d3e43585e49d27efe8444a5b0dbb7f89fe0a916` |
| teacher_confirm split lock | overlap 审计 PASS，T0v2 SHA256 `8ef5c17c57958ceb328025f4166e2915cec8a18de68fa84566746a6c99c03b26` |
| T0v2 教师视图 | 608 条（524 development + 84 confirm），SHA256 `459a0546890cf84a63e763d48211302d3403b3f55edc7aee8d417ede83a480ce` |
| T0v2 teacher qualification lock | PASS，SHA256 `d5d7523e7170a368d203d2aad3d3a78711f97972475511b2feb3300e47490ce9` |
| R2-Q transaction lock | 已冻结并完成唯一一次执行，SHA256 `44b4f8b440c4b667943ed6ee8f9c1640613245088feb1e50ff1cc7d5930927ce` |
| R2-Q decision | `FAIL / clip_semantic_only_not_authorized`，SHA256 `4414fb69b110e5cf85bf140d6276cacda533a05178baf11122d95049035317fc` |

R2-Q 正式结果只作追加证据，不反向修改预注册配置或门禁：

| 门禁 | 结果 |
| --- | ---: |
| T1 rank-first | `0.668367`，PASS |
| T1 margin bootstrap 95% CI 下界 | `0.065613`，PASS |
| 相对旧 R2 绝对提升 | `+0.571429`，PASS |
| clean 双次确定性 | 最大差 `0.0`，PASS |
| T2C 增强一致率 | `0.948554`，PASS |
| T2C 高/中置信覆盖率 | `0.744898`，PASS |
| T2C 可用子集 rank-first | `0.753425`，PASS |
| T2C 每类至少 3 条可用样本 | Banjo=`1`、Mandolin=`2`、Shofar=`2`，FAIL |

因此当前强制停止：不得训练 projector，不得修改 fusion/gate，不得训练 selector，不得复用同一
validation 调低置信度阈值或每类门禁。
| 项目 `intersuit/assets/hf_cache/` | `hub/` 与 `transformers/` 已建立，并由 `.gitignore` 项目规则命中 |
| M4 CLAP import | PASS，Transformers `4.44.0` |
| M4 可用性检测 | `librosa=False`，`soundfile=False` |
| raw NumPy CPU processor smoke | PASS，输出 `[1,4,1001,64]`、finite；固定 seed 双次 hash 均为 `656c4b231525b34db87db0ef3a85b5c0b451a8f56057a0655c3f3c559f689104` |
| CLAP 官方资产 | `laion/clap-htsat-unfused@8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a` 已下载并逐文件锁定 |
| 当前 feature extractor 默认值 | `sampling_rate=48000`、`max_length_s=10`、`padding=repeatpad`、`truncation=fusion`；正式配置不得隐式继承 |

上述 smoke 只证明 raw NumPy 输入在当前 M4 环境可运行，不证明默认 `repeatpad/fusion` 已获正式
资格。教师资产、baseline lock、confirm manifest 和 cache ignore 已解决；旧 pilot 双视图与
T0v1 选择结论不得继承。必须先完成全部 524 条 train 的教师视图、T0v2 配置比较和新
pre-validation ledger snapshot，才能运行 T1。

版本与证据继承关系：

- 旧 R0/R1 的冻结数据契约和已通过证据继续有效，不重复改写结论；
- 旧 R2 Qwen token 目标作为失败实验永久留档，不删除、不追认；
- 本文是旧 R2 之后的版本化替代路线，记为 `R2+`，不静默覆盖旧计划；
- 当前只授权设计和执行 `R2-T0/T1/T2`；
- `R3-C` 必须在教师门禁通过后由独立 `decision.json` 授权；
- `R4-E` 是核心模型结构变更，必须独立 review、独立 Git commit；
- `R4-B`、`R5`、`R6` 分别由上一阶段单独授权，不得打包授权。

因此，本计划不是把一个 CLAP checkpoint 直接接进训练，而是建立一条可证伪、可回退、
可复现的完整链路：

```text
冻结教师资产
→ 教师自身音频—文本资格审查
→ 教师置信度资格审查
→ BEATs 学生 clip 级语义资格审查
→ window 迁移资格审查
→ scene-audio token 工程实现
→ Qwen 桥接目标训练
→ 下游因果效用验证
→ 返回 Dynamic Window Selector Oracle
```

任何前置门禁失败，后续阶段均不得通过增加训练步数、解冻基座或修改 selector 来绕过。

## 2. 不可违反的职责边界

### 2.1 教师可以做什么

教师是“冻结、独立、经过资格审查的音频—文本语义教师”，只提供：

```python
teacher_audio_embedding = teacher.audio_encoder(audio)
teacher_text_embedding = teacher.text_encoder(canonical_event_prompt)
```

两者必须位于同一个共享语义空间。教师文本目标只来自预先冻结的 canonical label、
同义词和描述表。

### 2.2 教师不能做什么

教师不得：

- 替代 M4/Qwen2、视觉塔或 BEATs；
- 直接决定最终答案；
- 替代 question relevance；
- 开放生成事件名称并把生成结果当真值；
- 使用当前 M4 的预测作为第一阶段监督；
- 仅凭 argmax 生成高置信伪标签；
- 因自身资格通过而直接授权 fusion、gate、selector 或联合训练；
- 进入正式推理 graph、正式 checkpoint 或在线服务依赖。

教师语义表示只是“这个窗口可能包含什么声音”的先验。问题是否需要该声音、需要哪个窗口，
仍属于 question encoder、selector 和下游 Qwen 的职责。

### 2.3 全程冻结对象

教师资格审查期间禁止训练：

- teacher audio encoder；
- teacher text encoder；
- teacher projection head；
- teacher temperature；
- tokenizer、feature extractor；
- prompt 参数或可学习 prompt。

R2 阶段同时冻结：

- BEATs；
- 当前 scene-audio projector；
- M4/Qwen2；
- 视觉塔与视频 projector；
- fusion、gate、selector；
- 所有旧 AS-M4 新增模块。

R2 只验证目标，不训练任何模型。

## 3. 教师候选结论与冻结原则

### 3.1 预注册主候选

```text
model_id: laion/clap-htsat-unfused
role: frozen offline audio-text teacher
shared_embedding_dim: 512
teacher_sample_rate: 48000
formal_inference_dependency: false
status: provisional，未下载、未锁定、未通过 R2
```

选择它作为预注册主候选的原因：

- 面向一般音频与环境声事件，语义域与 AVE/UnAV 更接近；
- 音频和文本位于统一 512 维对比空间；
- 当前 M4 环境中的 Transformers 已具备 CLAP 导入能力；
- unfused 路径比多裁剪 fusion 更容易锁定预处理与解释输出；
- 教师可完全离线，不改变正式推理依赖。

“预注册主候选”不等于“已选定教师”。最终教师只有在官方来源、许可证、revision、
文件 SHA、预处理和本地资格指标全部锁定后才成立。

### 3.2 候选敏感性对照

| 候选 | 用途 | 主要风险 |
| --- | --- | --- |
| `laion/clap-htsat-fused` | pilot 敏感性对照 | 多裁剪/fusion 增加短窗归因与复现复杂度 |
| Microsoft CLAP 2023 | 独立实现复核 | 额外依赖、短音频重复、长音频随机裁剪 |
| `LanguageBind_Audio_FT` | 后续跨模态对照 | 依赖栈较旧，且现有视觉塔并非 LanguageBind |
| ImageBind Huge | 研究性对照 | 模型重、依赖复杂、许可证边界更严格 |

每个真正运行的候选都必须先拥有独立的 candidate lock。模型选择只能使用完整的
`teacher_development_full_train` 524 条，不得使用旧 pilot300，不得根据 expanded validation
选择候选。

所有候选尝试还必须追加写入 `candidate_selection_ledger.json`，记录：

- candidate ID、官方来源、revision 和 checkpoint SHA；
- prompt、同义词、padding、truncation、max length；
- threshold、augmentation、seed 和代码 SHA；
- 使用过的数据角色；
- 开始时间、结果、淘汰原因；
- 是否在任何正式 validation 上暴露过结果。

账本只追加、不覆盖。任何在正式 validation 上暴露过的配置及其派生配置，不得返回候选池
继续调参。

正式 validation 前生成 ledger snapshot，并把其 SHA 写入 teacher lock。正式结果完成后只向
ledger 追加 result 记录，同时在 `decision.json` 保存 `ledger_before_sha256` 和
`ledger_after_sha256`；不得回写 teacher lock 伪造“运行前已经知道结果”。

## 4. 冻结数据契约与双音频视图

### 4.1 R0 身份与划分保持不变

正式实验沿用：

```text
dataset: AVE_HF_EXPANDED
train: 608
validation: 196
test_locked: 196
physical video overlap: 0
supervision_level: clip
audio_required: true
student_sample_rate: 16000
window_sec: 1.0
hop_sec: 1.0
tail: right zero padding
tail metadata: valid_sample_count
```

禁止：

- 访问 `test_locked`；
- 把 clip label 复制到每个 1 秒窗口并伪装为 window supervision；
- 混入纯视觉样本；
- 使用单字母 A–D 作为文本目标；
- 把未知标签音频作为“语义负样本”；
- 在 validation 上选择模型、prompt、阈值或增强配置。

### 4.2 数据角色与一次性评估协议

四类数据角色必须物理隔离并写入 split lock：

```text
teacher_development_full_train 524
  → 排除 teacher_confirm 后的全部合法 train，不再二次抽样
  → 教师候选、prompt、repeatpad/pad、裁剪、阈值和增强选择
  → 必须覆盖 28/28 类，并报告每类实际数量

teacher_confirm 84
  → 候选确认、代码调试、确定性复现和 golden fixture
  → 不得与 expanded validation / test_locked 物理重叠
  → 不得用于选择 prompt、padding、增强或阈值

expanded validation 196
  → 单一冻结候选的一次性正式评估
  → 不再选择候选、prompt、padding 或阈值

test_locked 196
  → 继续不可见；本文不授权访问
```

旧 `pilot300` 只保留为历史下载与媒体 smoke 证据，不再参与任何教师候选、配置、阈值、
门禁或指标计算。禁止为了降低运行成本从 524 条中再构造 pilot、Top-N、每类子集或提前停止；
任一条合法 train 缺失、解码失败或未参与配置比较时，T0v2 fail closed。

`teacher_confirm` 必须从 train 侧预注册划出，不能在看到 expanded validation 后补建。
T0 固定按下列规则生成实际资产：

```text
source:
  AVE_HF_EXPANDED train 608

selection:
  按 canonical label 排序
  每类用固定 seed 20260727 选择 3 个 physical clips
  28 类共 84 个 teacher_confirm clips

manifest:
  intersuit/datasets/AVE_HF_EXPANDED/teacher_confirm_manifest.jsonl

student-train exclusion manifest:
  intersuit/datasets/AVE_HF_EXPANDED/teacher_confirm_excluded_from_student_train.jsonl

teacher-development full-train manifest:
  intersuit/datasets/AVE_HF_EXPANDED/teacher_development_full_train_manifest.jsonl
  内容必须与排除 confirm 后的 524 个 physical IDs 完全相同
  不允许 limit、subsample 或 class cap
```

必须报告：

- 84 个 physical media ID；
- 28/28 类覆盖和每类恰好 3 个；
- 与旧 pilot300、expanded validation、test_locked 的物理重叠均为 0；
- 与原 train 的重叠为 84（因为它由 train 划出）；
- 从原 train 排除 confirm 后的剩余清单固定为 524 条，28/28 类覆盖；
- teacher-development 524 与 student-train 524 的 physical ID 集合必须完全相同；
- 三个 manifest 的绝对路径、SHA256 和生成代码 SHA。

teacher_confirm 一经划出，不得再进入 teacher-development、R3-C、R3-W 或 R4-B 的学生训练。
若任一类别无法提供 3 个 confirm 或在剩余 524 条中完全缺失，T0 fail closed，不得临时降低
类别覆盖、缩小训练集或从 validation 补样本。

正式 validation 一旦运行，无论 PASS/FAIL，该候选路线的正式确认即结束：

- PASS：只按 `decision.json` 进入下一阶段；
- FAIL：不得更换教师、prompt、padding、阈值或增强后重新使用同一 validation 宣称通过；
- 若要研究新的候选，只能生成新版本计划和新 confirm/validation 资产，或把结果明确标记为
  exploratory，不能恢复正式授权资格。

### 4.3 T1/T2 一次正式评估事务

expanded validation 只能由一个不可拆分的正式事务访问一次。在访问前同时冻结：

- T1 clean clip 推理、文本原型、evaluator 和全部门禁；
- T2 clip augmentation、prompt agreement、置信度阈值、拒绝规则和覆盖率门禁；
- 三个 seed、样本顺序、增强参数和 bootstrap 索引；
- 输出 schema 与 decision 计算代码 SHA。

一次运行生成不可变的：

```text
r2_teacher_validation_transaction/
├── transaction_lock.json
├── clean_clip_results.jsonl
├── augmented_clip_results.jsonl
├── prompt_agreement_results.jsonl
├── raw_embeddings_manifest.json
├── transaction_outputs_sha256.json
└── evaluator_log.txt
```

`transaction_lock.json` 必须引用 teacher lock、old R2 baseline lock、expanded validation
manifest、candidate ledger pre-validation snapshot 和 evaluator code 的 SHA；这些引用是单向的，
禁止让 teacher lock 反向引用 transaction 输出形成循环哈希。

判定顺序只读取同一批冻结结果：

```text
先计算 T1 decision
├── T1 FAIL → T2 结果只存档，不授权任何后续阶段
└── T1 PASS → 从同一 transaction 输出计算 T2 clip-confidence decision
```

不得为 T2 再次访问 expanded validation。短窗口能力使用另一套预注册的 window validation，
拥有独立 manifest/SHA/访问账本，不计入本次 clip validation 事务，也不得用 AVE clip label
代替 window label。

### 4.4 教师与学生使用同一物理媒体的不同派生视图

当前学生音频是由原视频派生的 16 kHz mono WAV；CLAP 教师需要确定性的 48 kHz 输入。
不得把 16 kHz WAV 再上采样后假装成原始高频教师输入。计划采用：

```text
同一 physical_media_id / 同一时间轴
├── student_view：现有 16 kHz mono WAV，供 BEATs 使用
└── teacher_view：从官方原始 MP4 音轨确定性解码为 48 kHz mono PCM
```

双视图必须记录同一媒体 ID、原始时长、解码区间和时间戳映射。教师视图是派生缓存，
不改变 R0 split，也不得提交 Git。

### 4.5 教师 CLAP 预处理必须逐字段锁定

CLAP 官方预处理可能对超过最大长度的音频随机截断，对短窗执行 repeat/pad。正式资格实验
不得把内部随机裁剪留给 processor。必须先在外部按冻结规则构造精确波形区间，再送入
processor：

- 完整 clip：冻结起点、终点和最大时长；
- 1 秒窗：冻结 `[start_sec, end_sec)`；
- 尾窗：保留 `valid_sample_count`，只对缺失尾部右侧补零；
- augmentation：每个样本的种类、参数和 seed 可重放；
- processor 内部若仍存在随机分支，必须关闭或固定并纳入锁文件。

当前 Transformers CLAP feature extractor 的相关默认行为不能被隐式继承，必须读取实际
实例配置并逐字段记录：

```text
sampling_rate
max_length_s / max_length_samples
truncation（包括 fusion/random crop 分支）
padding（包括 repeatpad）
frequency range / mel configuration
return_attention_mask
numpy RNG state
torch CPU/CUDA RNG state
```

正式策略必须满足：

1. 在全部 524 条 `teacher_development_full_train` 上比较 `repeatpad` 与纯右侧 zero pad；
2. 在查看 validation 前冻结其中一种；
3. 输入先按锁定区间精确裁到 `max_length` 以内；
4. 正式 unfused 路径不得触发随机 mel crop；
5. 若库仍随机标记 batch 中某个样本为 `longer`，必须关闭该行为或固定并验证其不改变输出；
6. 对固定 golden waveform 保存原始 waveform SHA256、processor 输出 shape/dtype 和
   `input_features` 内容 hash；
7. 同一 lock 下重复运行，`input_features` hash 必须完全一致。

教师完整 clip 资格通过不等于教师具备 1 秒 window 定位能力；两者必须分阶段审查。
未经短窗口资格审查，不得把重复到 10 秒的 1 秒窗口 embedding 当作教师 window 真值。

## 5. Prompt 与文本目标冻结

教师文本目标只能来自 canonical label 表。预注册模板可以包括：

```text
"{label}"
"the sound of {label}"
"an audio recording of {label}"
```

冻结规则：

- prompt 只能在全部 524 条 `teacher_development_full_train` 上选择；
- 选择指标必须同时报告 28/28 类；任何类别为零样本时 fail closed；
- 每个标签使用完全相同的模板集合；
- prompt ensemble 的 pooling、归一化和权重提前固定；
- 同义词表提前冻结并记录来源；
- label-description prototype 只能使用 train split 定义；
- validation 不参与 prompt、描述、阈值和教师选择；
- 看到 validation 结果后不得追加有利描述。

## 6. 目标模型逻辑架构

### 6.1 因子化 scene-audio 表征

不能把现有 `768→3584` projector 直接替换成 `768→512`，否则会破坏 fusion 和
streaming 模块的既有张量契约。新增路径必须使用显式的因子化表征：

```text
SceneAudioRepresentation
├── raw_features:      [B, W, 768]   # 冻结 BEATs 输出
├── semantic_features: [B, W, 512]   # 新语义 projector 输出
├── llm_features:      [B, K, 3584]  # 新 Qwen bridge 输出
├── window_mask:       [B, W]        # 真实窗口，包括合法尾窗
├── selected_mask:     [B, K]
├── timestamps:        [B, W, 2]
├── valid_sample_count:[B, W]
└── selected_indices:  [B, K]
```

语义 projector 不得成为唯一信息瓶颈。Qwen bridge 允许同时读取：

```text
selected raw BEATs 768
+ selected semantic 512
+ normalized timestamp/valid_fraction
→ 3584
```

这样既保留教师对齐的类别语义，也保留同类不同实例的声学差异。

### 6.2 最终训练与推理路径

```text
16 kHz scene audio
→ 1 秒窗 / 1 秒 hop / 尾窗元数据
→ frozen BEATs
→ raw_features 768
→ selector（仍只基于合格输入执行）
→ selected raw features
├── semantic projector 768→512
└── raw + semantic + time/validity
    → audio-to-Qwen bridge → 3584
→ 独立 scene-audio tokens
→ Qwen2
→ answer
```

正式推理不加载教师。教师只在离线资格审查和学生监督中存在。

### 6.3 question relevance 仍是独立链路

当前 question-conditioned selector 若仅使用未上下文化的 Qwen input embedding 均值，
可能重复旧 R2 的文本表征失败。该编码器不因 CLAP 教师通过而自动合格。返回 learned
selector 前，必须单独审查 question encoder 对事件文本的排序能力。

## 7. 阶段与授权状态机

```text
R2-T0 资产与双视图锁定
  ↓ PASS
R2-Q  一次 clip validation transaction
  ├── R2-T1：先用 clean results 判教师 clip 资格
  └── R2-T2C：T1 PASS 后用同一 results 判 clip 置信度
  ↓ T1 PASS 且 T2C PASS
R2-T2W 独立 window validation（可得到 PASS/FAIL/not_qualified）
  ↓ 记录 capability；FAIL/not_qualified 仅禁用 window teacher
R3-C  BEATs→教师空间 clip 级 projector 资格审查
  ↓ PASS（仍不能部署到 window）
R3-W  window 迁移与弱监督/MIL 资格审查
  ↓ PASS
R4-E  scene-audio token 工程链路实现与回退测试
  ↓ PASS
R4-B  audio-to-Qwen bridge 显式目标训练
  ↓ PASS
R5    下游因果效用与融合位置门禁
  ↓ PASS
R6    返回 Selector V2 Oracle；Oracle 通过后才讨论 learned selector
```

每个 `decision.json` 只能授权紧邻的下一阶段，不能跨级授权。

当前授权矩阵：

| 阶段 | 当前授权 | 解锁条件 |
| --- | --- | --- |
| R2-T0 | 已授权 | 本文与 R0/R1 冻结证据 |
| R2-Q/T1/T2C | 条件授权 | R2-T0 PASS；只能执行一次 clip validation transaction |
| R2-T2W | 条件授权 | 独立 window manifest/lock 完成；不得再次访问 clip validation |
| R3-C | 未授权 | T1/T2C PASS，T2W 产生 `clip_only_qualified` 或 `clip_and_window_qualified`，且独立 decision 授权 |
| R3-W | 未授权 | R3-C PASS |
| R4-E | 未授权 | R3-W PASS；结构变更独立 review/commit |
| R4-B | 未授权 | R4-E contract/tests PASS |
| R5 | 未授权 | R4-B PASS |
| R6 | 未授权 | R5 PASS |

`clip_only_qualified` 是 T1/T2C PASS 且 T2W FAIL/not_qualified 后的受限状态，只授权不含
teacher window embedding 的
R3-C/R3-W 配置；不能被训练脚本解释为完整 window teacher PASS。

## 8. R2-T0：教师资产与实验锁定

### 8.1 必须生成的锁文件

新增 `teacher_qualification_lock.json`，至少包含：

```json
{
  "schema_version": 1,
  "teacher": {
    "model_id": null,
    "official_source": null,
    "revision": null,
    "architecture": null,
    "license": null,
    "checkpoint_files": [],
    "checkpoint_sha256": {},
    "model_code_sha": null,
    "config_sha256": null,
    "tokenizer_sha256": null,
    "feature_extractor_sha256": null
  },
  "preprocessing": {
    "teacher_sample_rate": 48000,
    "student_sample_rate": 16000,
    "clip_interval_rule": null,
    "max_length_seconds": null,
    "max_length_samples": null,
    "truncation": null,
    "padding": null,
    "disable_random_mel_crop": null,
    "longer_flag_policy": null,
    "window_sec": 1.0,
    "hop_sec": 1.0,
    "tail_policy": "right_zero_pad_with_valid_sample_count",
    "pooling": null,
    "normalization": null,
    "golden_waveform_sha256": null,
    "golden_input_features_sha256": null
  },
  "embedding": {
    "audio_dim": null,
    "text_dim": null
  },
  "prompts": {
    "templates": [],
    "synonym_table_sha256": null,
    "description_table_sha256": null,
    "ensemble_rule": null
  },
  "runtime": {
    "conda_env": "M4",
    "inference_dtype": null,
    "device": null,
    "seeds": [],
    "numpy_rng_state_sha256": null,
    "torch_rng_state_sha256": null,
    "transformers_version": null,
    "torch_version": null,
    "ffmpeg_version": null
  },
  "data": {
    "manifest_sha256": null,
    "split_lock_sha256": null,
    "teacher_view_manifest_sha256": null,
    "teacher_confirm_manifest_path": null,
    "teacher_confirm_manifest_sha256": null,
    "student_train_exclusion_manifest_path": null,
    "student_train_exclusion_manifest_sha256": null,
    "old_r2_baseline_lock_sha256": null,
    "candidate_selection_ledger_prevalidation_sha256": null
  },
  "capabilities": {
    "clip_teacher": false,
    "window_teacher": false,
    "allowed_student_targets": []
  }
}
```

任一必填字段为 `null` 时，R2-T1 必须 fail closed。

### 8.2 旧 R2 可比基线锁

“相对旧 Qwen token target 提升至少 10pp”只能引用一个冻结的
`old_r2_baseline_lock.json`。T0 必须锁定：

```text
source directory:
  intersuit/harness/artifacts/audio_semantic_reconstruction_r2_targets/

required source:
  config.json
  manifest_lock.json
  target_lock.json
  metrics.json
  decision.json
  per_sample.jsonl
```

baseline lock 至少包含：

- 上述每个文件的 SHA256；
- 完全相同的 196 个 validation physical IDs 及顺序 SHA；
- evaluator 代码 SHA；
- rank-first 的精确定义；
- hard-negative 构造规则；
- prompt aggregation、normalization 和 tie-breaking；
- bootstrap seed/索引；
- 旧 Qwen target 的逐样本 score、rank、margin；
- 聚合结果能够从 `per_sample.jsonl` 精确重算的 golden test。

若历史目录缺少 `per_sample.jsonl`，只允许在正式教师 validation 前，使用旧 R2 已冻结缓存、
旧 target lock 和旧 evaluator 重建；重建聚合指标必须与历史 `metrics.json` 在预注册容差内
一致。不得重新选择旧 target，不得借新教师结果修改 evaluator。若无法可靠重建，则
R2-T0 FAIL，“+10pp”不可比较，不能在看过新 validation 后删除该门禁。

新教师 T1 必须使用同一 196 IDs、同一 rank-first/hard-negative/tie-breaking 定义；只有目标
encoder 本身允许不同。`old_r2_baseline_lock.json` 的 SHA 写入 validation transaction lock。

### 8.3 下载和环境边界

当前本地没有已确认的 CLAP checkpoint。下载前必须：

1. 确认官方 Hugging Face 来源和许可证；
2. 固定精确 revision/commit；
3. 获得联网下载权限；
4. 只使用既有 `M4` 环境；
5. 不创建新环境，不覆盖安装候选模型的整套 requirements；
6. 下载后对每个模型文件计算 SHA256；
7. 模型权重、缓存和派生特征不加入 Git。

所有 Hugging Face 资产必须使用项目专属缓存，不读取或修改用户全局 HF cache：

```text
HF_HOME=/home/yjm/M4-main/intersuit/assets/hf_cache
HF_HUB_CACHE=/home/yjm/M4-main/intersuit/assets/hf_cache/hub
TRANSFORMERS_CACHE=/home/yjm/M4-main/intersuit/assets/hf_cache/transformers
```

要求：

- 每条下载/加载命令显式传入这些变量，不写入全局 shell profile；
- `intersuit/assets/hf_cache/` 必须先加入项目 `.gitignore` 并验证 `git check-ignore`；
- 不复用其他 Conda 环境的 cache、site-packages 或环境变量；
- 不安装候选仓库的整套 requirements；
- 下载与 smoke 的 `environment.json` 记录三个 cache 路径。

当前 M4 中 CLAP 类可以导入，但不得假设 `librosa/soundfile` 可用。T0 的第一条 processor
资格测试必须是 CPU + raw NumPy waveform：

```text
conda run -n M4 python <processor_cpu_smoke.py>

input:
  np.ndarray(dtype=float32, shape=[num_samples])
  sampling_rate=48000

must verify:
  不经过 librosa/soundfile 文件加载
  processor 输出 finite
  shape/dtype 与 lock 一致
  固定 waveform 的 input_features hash 双次一致
```

若 raw NumPy smoke 因依赖缺失而失败，立即停止并记录原始 import/availability 结果。只有完成
依赖影响 review 并获得用户明确同意后，才可评估在 `M4` 环境单独安装最小依赖；不得修改其他
环境或全局 Python。

### 8.4 R2-T0 门禁

```text
官方来源可验证
AND 许可证边界明确
AND revision 与全部 SHA 已锁定
AND 双音频视图可由 manifest 一一追溯
AND 全部 524 条 teacher-development train 均有合格教师视图并实际参与配置比较
AND teacher-development 覆盖 28/28 类且与 84 条 teacher_confirm 零重叠
AND 预处理不存在未锁定随机性
AND 固定 waveform 的 input_features hash 可重复
AND prompt/label/split/seed/code 已锁定
AND candidate selection ledger 的 pre-validation snapshot 已冻结
AND old R2 baseline lock 可逐样本重算且 evaluator 可比
AND 项目专属 HF cache 已被 Git 忽略
AND M4 环境 raw NumPy processor CPU smoke 通过
```

否则停止，不执行教师推理。

## 9. R2-T1：冻结教师自身资格审查

本阶段只比较：

```text
teacher.audio_encoder(teacher_clip_view)
vs
teacher.text_encoder(frozen_canonical_prompts)
```

不得训练教师、BEATs、projector、M4 或任何 AS-M4 模块。

### 9.1 评测流程

1. 在全部 524 条 `teacher_development_full_train` 上完成 evaluator、prompt、预处理和候选选择；
2. 在 `teacher_confirm` 完成代码调试、golden fixture 和双次确定性复现；
3. 同时冻结 T1 与 T2C 的全部配置、ledger、lock 和 transaction；
4. 在 expanded validation 196 上执行唯一一次正式 transaction，同时生成 clean/augmentation/
   prompt-agreement 原始结果；
5. `test_locked` 保持不可见；
6. 三个固定 seed 用于增强、bootstrap 和任何允许的数值随机性；
7. 无增强确定性推理重复运行应在预注册容差内一致。

### 9.2 必报指标

- audio→text rank-first；
- macro Recall@1；
- balanced accuracy；
- hard-cross-label rank-first；
- 正确标签与 hard negative 的 similarity margin；
- margin bootstrap 95% CI；
- per-class Recall；
- confusion matrix；
- random baseline 与 majority baseline；
- 3 seeds 的 mean/std/worst；
- 相对旧 Qwen token target 的绝对提升。

### 9.3 R2-T1 主门禁

```text
validation rank-first >= 0.65
AND margin bootstrap 95% CI lower bound > 0
AND 相对旧 Qwen token target 提升 >= 10pp
AND 所有输出 finite
AND 无增强重复推理满足确定性容差
```

若失败：

```text
不训练 projector
不修改 fusion/gate
不训练 selector
不运行 LongVALE/UnAV Oracle 下游效用
```

失败后允许在完整 teacher-development train 上做探索性根因分析，但不得更换候选、prompt、
padding、阈值或增强
后重新使用同一 expanded validation 宣称正式通过。新的教师路线必须进入新版本计划，并拥有
新的 confirm/validation 资产和独立 ledger；若没有新的未见正式评估集，只能报告探索结果，
不能授权 R3-C。

## 10. R2-T2：教师置信度与短窗鲁棒性

T2 clip-confidence（T2C）不得重新运行 expanded validation，只能读取 R2-Q transaction
已经冻结的 augmentation/prompt-agreement 结果。T1 FAIL 时这些结果只留档；只有 T1 PASS
才计算 T2C 授权 decision。

教师 argmax 不能被视为精确真值。每个监督项的置信度必须同时考虑：

- top-1 与 hard negative 的 similarity margin；
- 多 prompt 预测一致性；
- 不同音频增强下的类别与排序稳定性；
- 是否属于预定义 canonical event 集合；
- 可选的独立教师一致性。

预注册轻量增强：

- 小幅 gain；
- 轻噪声；
- 轻度混响；
- 小范围时间抖动。

置信度策略必须在全部 524 条 `teacher_development_full_train` 上冻结：

```text
high confidence → 正常权重
medium confidence → 降权
low confidence / out-of-set → 拒绝语义伪标签
```

默认正式门禁为：

```text
完整 clip 增强前后 top-1 一致率 >= 0.90
AND 高/中置信可用覆盖率 >= 0.70
AND 可用子集仍满足 R2-T1 的 rank-first 与 margin 方向
AND 低置信拒绝后 per-class 有效样本数全部达到预注册下限
```

若某类有效样本数不足，只能在查看 validation 前基于完整 524 条 train 将该类预注册为
“只用文本原型，不用 teacher audio 伪标签”；不得通过丢弃其他 train 样本、缩小分母或在
validation 后降低阈值。

还需分别报告完整 clip 与 1 秒短窗。短窗失败不否定教师的 clip 级资格，但会明确禁止把
teacher audio embedding 当作 window 真值；此时 R3-W 只能依赖文本原型 MIL 或已资格审查
的外部时间标注。

短窗口能力不得使用 AVE clip label 判定。它只能在已经通过媒体与时间戳资格审查、
`supervision_level=window/segment` 的 train/confirm 资产上开发，并在独立 window validation
上确认。默认短窗门禁为：

```text
1 秒 window→text rank-first >= 0.65
AND margin 95% CI lower bound > 0
AND repeatpad/pure-pad 冻结策略下增强一致率 >= 0.85
AND tail/short-event 分桶不存在预注册的灾难性退化
```

若没有合格的独立 window validation，状态必须是 `window_not_qualified`，工程权限等价于
short-window FAIL，但不得把“未评测”写成“模型失败”。

### 10.1 能力状态机

R2-Q/T2W 的输出不是一个模糊 PASS，而是以下互斥状态：

```text
T1 clip FAIL
  → teacher_status = rejected
  → 教师整体淘汰
  → 不授权 R3-C

T1 clip PASS + T2C clip-confidence FAIL
  → teacher_status = clip_semantic_only_not_authorized
  → 结果只作教师能力报告
  → 不授权任何学生训练

T1 clip PASS + T2C PASS + T2W short-window PASS
  → teacher_status = clip_and_window_qualified
  → 允许目标：text prototype + teacher clip + teacher window

T1 clip PASS + T2C PASS + T2W short-window FAIL / window_not_qualified
  → teacher_status = clip_only_qualified
  → 禁用 teacher window embedding
  → 只允许：text prototype + teacher clip relational + MIL/真实时间标注
```

`teacher_status`、`capabilities.clip_teacher`、`capabilities.window_teacher` 和
`allowed_student_targets` 必须同时写入 `teacher_qualification_lock.json`、
`decision.json` 和学生 checkpoint lock。训练脚本只能读取这些字段并 fail closed，
不得根据是否存在缓存文件、命令行默认值或运行时探测隐式选择监督分支。

## 11. R3-C：clip 级 semantic projector 资格审查

前提：R2-T1 与 R2-T2C 均通过；R2-T2W 已记录为
`clip_and_window_qualified` 或 `clip_only_qualified`。

### 11.1 冻结与可训练边界

继续冻结教师、M4/Qwen2、视觉塔、BEATs、旧 projector、fusion、gate 和 selector。
只训练新编号的：

```text
audio_semantic_projector_vN: BEATs 768 → teacher shared space 512
```

使用新 run name，不覆盖任何基线或失败 run。

### 11.2 clip 级数据粒度

AVE 只有 clip label，因此本阶段每个视频每个 epoch 只贡献一个预注册 pooling 得到的 clip
表示。不得把一个视频的 10 个窗口当作 10 个独立有标签样本。

### 11.3 双目标，避免类别原型塌缩

单独对齐类别文本原型会压平同类不同实例的声学内容，因此采用两个分开的目标：

1. 文本原型目标：学习 canonical event 语义；
2. 教师音频/关系蒸馏：保留同类不同实例和跨样本相对结构。

教师音频目标按 R2-T2C 置信度加权，低置信样本拒绝或降权。不得把同类不同视频作为
InfoNCE 负样本。

负样本必须：

- 不同 physical media；
- 不同 canonical label；
- 来自同一 split；
- batch 类别均衡。

### 11.4 执行阶梯与指标

```text
forward contract
→ 2-step overfit
→ 20-step canary
→ 1 epoch
→ 3 seeds
```

必须分开报告：

- text-prototype cross-modal alignment；
- audio-centroid preservation；
- teacher-audio relational preservation；
- rank-first、margin CI、macro Recall、per-class Recall；
- mean/std/worst seed 和 train-validation gap。

R3-C 必须通过两组互不替代的 evaluator。

声学信息保持门禁使用同一个 audio-centroid evaluator、同一批样本和同一分类协议分别评估
raw BEATs 与 projector 输出：

```text
projected audio-centroid macro Recall@1
  >= raw BEATs audio-centroid macro Recall@1 - 1pp
AND projected audio-centroid rank-first
  >= raw BEATs audio-centroid rank-first - 1pp
```

跨模态语义门禁只使用冻结的 CLAP 文本原型 evaluator：

```text
validation audio→text rank-first >= 0.70
AND validation audio→text margin 95% CI lower bound >= 0.02
AND worst-seed audio→text rank-first >= 0.65
AND train-validation gap <= 20pp
```

`raw BEATs audio centroid` 与 `CLAP text prototype` 不属于同一检索空间，禁止跨 evaluator
计算“+5pp”或其他差值。两组门禁分别回答“有没有破坏原声学信息”和“有没有学到跨模态语义”，
必须同时 PASS。

R3-C 通过只证明 clip 表征可学，不授权将 projector 直接应用于每个 1 秒窗口。

## 12. R3-W：window 迁移资格审查

本阶段专门修复“clip 训练、window 部署”的逻辑断层。

### 12.1 AVE 弱监督路径

每个视频作为一个 window bag：

```text
BEATs windows
→ shared semantic projector
→ per-window class similarities
→ 预注册 MIL 聚合
→ clip-level loss
```

可预注册 `logsumexp` 或 top-M pooling，但只能在完整 train 上选择。一个视频仍是一个训练
样本；clip label 只监督 bag，不复制给所有窗口。

### 12.2 外部时间监督路径

若使用 UnAV/LongVALE 等时间标注：

- 先锁定物理媒体、时间区间、标签映射和 split；
- 与 AVE validation/test 物理隔离；
- 确认音频确实存在且时间戳可对齐；
- 明确 `supervision_level=window/segment`；
- 未知标签不作为语义负样本。

只有 R2-T2 已证明教师 1 秒短窗可靠时，才允许使用 teacher window audio embedding；
否则教师只提供文本原型，window 位置由 MIL 或真实时间标注约束。

### 12.3 R3-W 门禁

必须预注册并报告：

- window/segment 定位 Recall、mAP 或 IoU；
- window→text rank-first 和 margin CI；
- hard-cross-label 与 same-class-different-instance；
- tail window 与 `valid_fraction` 分桶结果；
- clip 聚合性能不低于 R3-C 的容忍界限；
- 三个 seed 的 worst case。

若使用已经冻结的 Selector V1 UnAV-100 资产，R3-W 的定位授权不得新造宽松门禁，必须复用
V1 的 Event Coverage@K、Positive-window Recall@K、mean best tIoU、物理视频 cluster
bootstrap 和双次复现规则。新的 semantic projector 至少要满足：

```text
相对 Random 的定位/语义指标 CI 下界 > 0
AND 相对 raw BEATs 输入不发生超过预注册容忍值的退化
AND 1 秒窗语义 rank-first >= 0.65
AND margin 95% CI lower bound > 0
AND 至少 2/3 seed 方向一致
```

具体容忍值必须在完整 train 上冻结到 `config.json`；validation 后不可修改。

R3-W 不通过时，不允许把 semantic projector 接到 selector 或 Qwen。

## 13. R4-E：scene-audio token 工程链路

该阶段先实现和验证接口，不训练 Qwen。

### 13.1 token 插入策略

为了保证关闭 scene audio 时输入文本和视觉 token 与原 M4 完全一致，不新增用户可见
`<scene_audio>` 文本占位符。scene-audio tokens 在内部紧跟视觉 feature block 插入：

```text
system/user prefix
→ 原有 image/video feature block
→ optional scene-audio feature block
→ 原有 query-speech placeholder 对应的 feature block（若存在）
→ 其余 question text tokens
```

当 `enable_scene_audio=0` 或 `rollback_mode=behavior` 时，必须插入零个 scene-audio token，
而不是插入全零向量。这样才能实现 token 长度、position 和 KV cache 级别的真实回退。
`gate0_probe` 与静音属于不同实验条件，不共享这条 token 等价声明。

第一版结构必须冻结为：

```text
每个选中窗口 → 1 个 scene-audio token
primary K = 8（每个样本总计，不是每个视觉块各 8 个）
bridge 训练 selection = uniform_index_k8_v1，确定性且不训练 selector
selected windows = 按 (media_order, start_sec) 升序
后续所有可学习 selector scorer 只读取 raw BEATs 768
bridge = LayerNorm + 2-layer MLP
MODEL_MAX_LENGTH=32000 新增预算 = 最多 8 个 scene-audio token / sample
```

有效窗口少于 8 时只插入实际有效 token，不把 batch padding slot 写入 Qwen 序列。32k preflight
必须证明新增最多 8 token 不会通过截断挤掉原视频 token、query speech 或答案 labels。

`uniform_index_k8_v1` 不是 scorer，定义为固定索引选择算法：

```text
1. 过滤 batch padding，只保留 window_mask=true 且 valid_sample_count>0 的窗口；
2. 按 (media_order, start_sec, end_sec, original_index) 稳定排序，得到 N 个有效窗口；
3. N <= 8：选择全部 N 个；
4. N > 8：
   对 j=0..7 计算 q_j = j * (N - 1) / 7；
   使用 round-half-up：raw_i_j = floor(q_j + 0.5)；
   若与已选索引重复，向右选择第一个未使用索引；
   若右侧不存在未使用索引，直接 fail closed，不允许向左产生另一实现；
5. 对 selected indices 做唯一性断言，并按 start_sec 升序输出。
```

当 `N>8` 时端点 `0` 和 `N-1` 必选，因此合法 tail window 总被保留；当 `N<=8` 时全部窗口
都被保留。算法名、版本、实现代码 SHA、边界 golden cases 和选中索引 hash 必须进入
bridge checkpoint lock。

bridge 的精确第一版为：

```text
u = concat(
  raw_beats_768,
  semantic_512,
  normalized_start_sec,
  normalized_end_sec,
  valid_fraction
)                                      # 1283

token = Linear_2(
          GELU(
            Linear_1(
              LayerNorm_1283(u)
            )
          )
        )                              # 3584
      + scene_audio_modality_embedding

Linear_1: 1283 → 3584
Linear_2: 3584 → 3584
```

第一版时间信息只使用输入中的三个归一化标量，不新增独立 time embedding/time adapter，
也不保存任何 time embedding 参数。唯一新增的可学习类型 embedding 是
`scene_audio_modality_embedding`。

第一版禁止同时引入 Q-Former、Perceiver、跨窗 self-attention、可学习聚合器或一个窗口多 token。
这些只能在第一版通过后作为独立消融。

插入位置固定为：

```text
有一个或多个 image/video block：
  system/user prefix
  → 保持原顺序的所有 visual block，直到最后一个 visual block
  → scene-audio block
  → 原有 query-speech placeholder 对应 block
  → 其余 question text

无视觉 block：
  system/user prefix → scene-audio block → 原有 query-speech block → question text
```

query speech 继续使用原有 speech encoder/projector 和占位位置语义；统一 packer 只保证 canonical
模板中 scene audio 在 query speech 前。若输入模板给出冲突顺序，数据预处理必须 fail closed，
不得在 packer 中静默重排文本 token。

R4-E 必须为“单视觉、多视觉/多视频、无视觉”和“有/无 query speech”保存 token-level golden
fixture，至少锁定原始 placeholder 序列、展开后的 block 顺序、每段长度、attention mask、
position IDs 和 labels/IGNORE_INDEX hash。

一个样本可以有多个视觉 block，但第一版只有一个 scene-audio stream。若未来支持多个
scene-audio stream，必须另立 contract；当前遇到多个 stream 直接 fail closed，不把每路
`K=8` 拼成不可控 token 数。

### 13.2 必须实现的工程接口

- loader 显式使用 `window=1.0s`、`hop=1.0s`，禁止 0.5s 静默默认；
- `valid_sample_count` 和 `valid_fraction` 从 loader 贯穿 collator、encoder、selector、
  bridge 和 token packer；
- 合法尾窗保留，batch padding slot 才是 invalid；
- typed `SceneAudioRepresentation`；
- 图像 block 后可选 audio token 插入；
- audio token 的 `labels=IGNORE_INDEX`；
- attention mask、position ids、KV cache 长度同步更新；
- scene audio 与用户 query speech 同时存在；
- batch 内不同 window/token 数正确 padding；
- save/reload 保留 projector、bridge、scene-audio modality embedding 和配置；
- teacher 权重不被保存进学生 checkpoint；
- rollback 配置可恢复原 M4 行为。

当前代码审计对应的根改造点为：

| 现有位置 | 当前断点 | 计划中的根修复 |
| --- | --- | --- |
| `intersuit/intersuit/streaming/audio_stream.py` | `stack_audio_windows` 丢失 `valid_sample_count` | 返回 typed window batch，不再只返回 samples/timestamps |
| `intersuit/intersuit/train/train.py` | 运行参数可落到 0.5 秒 hop | 数据 contract fail-closed，正式 R0 必须显式 1.0/1.0 |
| `intersuit/intersuit/model/scene_audio_encoder/scene_audio_encoder.py` | encoder/projector 直接面向旧 3584 路径 | 保留旧输出并新增 factorized raw/semantic 接口 |
| `intersuit/intersuit/model/llava_arch.py` | scene audio 仍走视觉 residual；与 query speech 共存直接报错 | 建立统一 multimodal packer，在图像 block 后插入独立音频 token |
| collator 与相关 tests | 只有 mask/timestamp，缺少尾窗有效长度 | 加入 `valid_sample_count/valid_fraction` 并覆盖 mixed batch |

禁止只删除 `NotImplementedError` 或给缺失字段补默认值。上述改造必须以同一个 typed contract
贯穿数据、模型和生成路径。

### 13.3 必须新增的测试

- teacher cache 与 SHA 命中/失配 fail-closed；
- teacher/student 双视图媒体与时间戳同步；
- 768/512/3584 因子化张量 contract；
- `valid_sample_count` 贯穿与尾窗测试；
- MIL 不复制 clip label 到窗口；
- scene-audio token packing；
- scene audio + query speech 共存；
- attention/position/cache 增量生成；
- disabled/behavior 与原 M4 token 级等价；
- gate0_probe 保留 audio token slot/attention/位置，并把进入 Qwen 前的 audio input embeddings
  （含 modality embedding）置零；
- silence 保留正常 audio token 数和位置；
- `K=8`、时间排序和最多新增 8 token 的预算测试；
- 单图、多图/多视频、无图四类插入位置测试；
- 多 scene-audio stream fail-closed 测试；
- checkpoint save/reload；
- teacher 不进入 inference graph。

只有这些测试通过，才允许训练 bridge。

## 14. R4-B：audio-to-Qwen bridge 显式目标训练

随机初始化的 `512→3584` 层做无训练 logit probe 没有解释意义。bridge 必须拥有明确目标。

### 14.1 可训练模块

冻结 M4/Qwen2、视觉塔、BEATs、semantic projector、teacher、selector 和旧 fusion。只训练：

```text
audio_to_qwen_bridge_vN(
  raw_768,
  semantic_512,
  normalized_start_end,
  valid_fraction
) → 3584

scene_audio_modality_embedding
```

selector 尚未获得 learned 路线授权，因此 bridge 训练只能使用预注册的 `all valid windows`
或冻结的确定性索引选择，并保持时间顺序。第一版固定使用 `uniform_index_k8_v1`；不得在
R4-B 联合学习选窗。所用策略和 bridge 结构写入 checkpoint lock，R5 各条件保持一致。

### 14.2 训练目标

使用音频必需且标签合法的训练样本，让冻结 Qwen 在 scene-audio tokens 条件下预测：

- canonical event label；
- train-only 冻结的事件描述；
- 预注册、答案确实依赖声音的 QA 文本。

为避免只记住 AVE 28 类，R4-B 在启动前必须冻结：

- bridge train/dev 的 physical media ID 清单和零重叠证明；
- canonical label、自然语言描述、audio-required QA 三类目标的采样比例；
- correct、hard-cross-label mismatch、temporal-shift、silence 四类音频条件比例；
- label、同义词和描述的 train-only/heldout-description 划分；
- 未见描述、未见释义和组合式问题的泛化评测集；
- 每个目标族的 loss 权重和 batch 构造规则。

第一版建议比例必须在完整 bridge train 上确认后、查看 bridge dev 前冻结：

```text
canonical label target: 25%
frozen natural-language description: 25%
audio-required QA: 50%

其中至少 25% 训练实例使用 mismatch / temporal-shift / silence 负条件，
但负条件不得伪造错误答案监督；其目标应是拒绝、无事件或对应的合法 QA 答案。
```

不得使用 M4 当前预测作监督，不得使用开放生成教师标签，不得访问 test_locked。

训练前后必须比较：

- canonical label likelihood/rank；
- correct audio vs hard cross-label mismatch；
- same-class-different-instance；
- behavior/disabled 精确回退；
- gate0_probe 非劣性；
- silence 安全性；
- video-only 保真；
- 音频 token 数与上下文成本。
- seen label / unseen paraphrase / unseen description 的分组结果；
- train/dev physical media overlap audit；
- 只看类别名、去掉类别名和组合式 QA 三组泛化结果。

若 bridge 只在 seen 28 类标签名上通过、未见释义明显失败，或对 mismatch/错位音频同样
敏感，则判定为标签记忆，失败，不进入 R5。

## 15. R5：融合位置与下游因果效用

同一合格 projector/bridge checkpoint 下比较：

| 编号 | 方案 | 定位 |
| --- | --- | --- |
| F0 | video-only | 原始基线 |
| F1 | current residual | 历史诊断对照 |
| F2 | dedicated scene-audio tokens | 主方案 |
| F3 | late gated summary tokens | 低 token 成本备选 |
| F4 | hard mismatch / temporal shift | 语义与时序负对照 |
| F5 | silence | 开启音频路径、输入静音的安全性对照 |
| F6 | gate0_probe | 保留 audio token slot/attention/位置，只把输入 embedding 置零 |
| F7 | behavior / disabled | 完全不插入 audio token 的精确回退 |

三类回退/安全条件必须分开解释：

```text
behavior / enable_scene_audio=0
  → 插入 0 个 audio token
  → token、position、mask、cache 必须与原 M4 100% 一致

gate0_probe
  → 保留音频编码和 audio token slot
  → 保留 attention mask=true 和位置变化
  → 仅将进入 Qwen 前的 audio input embeddings（含 modality embedding）置零
  → 零 token 经 Transformer 后不保证保持零
  → 用于测量 slot/position/层变换副作用
  → 不宣称 token 序列等价

silence
  → scene audio 正常开启并产生 K 个 audio token
  → 只要求相对 video-only 无显著性能伤害
  → 不要求 token 或输出逐项一致
```

主门禁：

```text
correct audio accuracy >= video-only
AND correct audio > hard mismatch
AND correct-only > mismatch-only
AND correct net gain >= 0
AND correct net gain > mismatch net gain
AND audio-required 子集同样全部通过
AND behavior/disabled 的 token、position、mask、cache 与原 M4 100% 等价
AND gate0_probe 相对 behavior 的性能差异处于预注册非劣界限内
AND silence 相对 video-only 无显著性能伤害
```

还必须在与旧 gate 搜索物理媒体零重叠的 `reserve_new` 上选一次配置，再在 `dev_new` 一次性
确认。旧 `dev760` 不得重新用于调参。

主 scorer 必须预注册；严格 label、同义词 canonicalization 和选择题 exact match 中只有一个
用于主判定，其余只作敏感性分析。

若只有放大 residual 才改变输出，或 correct 与 mismatch 无差异，判定音频没有可归因语义
效用，停止 fusion/gate/selector。

## 16. R6：返回 Dynamic Window Selector 主线

只有 R2-T0 至 R5 全部通过，才允许：

1. 冻结合格的 semantic projector 与 bridge；
2. 返回既有 Dynamic Selector V2；
3. 先运行 All windows、Energy/Eventness Top-K、Temporal scorer Top-K、Oracle Top-K、
   Random Top-K；
4. 验证 Oracle Top-K 是否产生明确下游收益；
5. Oracle 有收益后，单独资格审查 question encoder；
6. question relevance 通过后才推进 learned question-conditioned selector；
7. Oracle 无收益则停止 selector，回到答案表示与融合位置。

教师资格通过、projector 通过或 bridge 通过，任何一个都不能单独授权 learned selector。

## 17. 运行、资源与产物契约

### 17.1 环境

- 所有 Python 命令使用 `conda run -n M4 ...`；
- 不创建新环境，不修改其他 Conda 环境；
- R2 优先 CPU/离线特征；
- 需要 GPU 时先运行项目 preflight；
- 单卡任务显式选择物理 GPU；
- 不默认使用全部 GPU；
- 保存 stdout、stderr、配置和开始时间；
- GPU 运行结束检查内核中的 NVRM/Xid/AER/PCIe/hung-task 异常；
- checkpoint、教师权重、特征、缓存和日志不提交 Git。

### 17.2 每阶段标准产物

```text
config.json
manifest_lock.json
teacher_qualification_lock.json        # R2 及后续引用
candidate_selection_ledger.json        # 只追加的候选/参数试验账本
old_r2_baseline_lock.json              # 旧 R2 逐样本可比基线
teacher_confirm_manifest.jsonl         # T0 固定 84 条
teacher_development_full_train_manifest.jsonl  # T0v2 固定全部 524 条
r2_validation_transaction_lock.json    # 唯一 clip validation 事务
metrics.json
per_sample.jsonl
confusion_matrix.*
review.md
decision.json
environment.json
checkpoint_sha256.json                 # 有新学生权重时
code_sha.txt
```

`decision.json` 至少包含：

```json
{
  "stage": "R2-Q",
  "status": "PASS_OR_FAIL",
  "t1_gate_results": {},
  "t2c_gate_results": {},
  "t2w_status": "PASS_OR_FAIL_OR_NOT_QUALIFIED",
  "transaction_outputs_sha256": {},
  "authorized_next_stage": null,
  "blocking_reasons": []
}
```

每次重大设计、模块新增/删除或核心模块改动完成后，应按项目规则提交独立 Git commit；
提交前确认未加入运行资产。尤其 `R4-E` 不得与 projector 训练、bridge 训练或实验结果混在
同一个 commit；它必须先完成接口与测试 commit，再由新 checkpoint lock 启动 R4-B。

## 18. 全局停止条件与回退

| 失败位置 | 必须停止 | 允许返回 |
| --- | --- | --- |
| R2-T0 资产不完整 | 全部教师推理 | 官方来源、许可证、锁文件 |
| R2-T1 教师不合格 | projector/fusion/gate/selector | 完整 teacher-development train 探索根因；不得复用同一正式 validation 宣称新候选通过 |
| R2-T2C clip 置信度不稳 | 全部学生训练 | 不得二次访问 validation；保留事务结果并停止 |
| R2-T2W FAIL/not_qualified | teacher window embedding | 只允许 clip teacher + 文本原型/MIL |
| R3-C clip 学生失败 | window、Qwen bridge | projector 目标与表征设计 |
| R3-W window 失败 | selector 与 Qwen 接入 | MIL/真实时间监督，不复制标签 |
| R4-E 工程 contract 失败 | bridge 训练 | 修正根接口和数据契约 |
| R4-B bridge 失败 | 下游 fusion | 显式目标、token 表示与训练数据 |
| R5 因果效用失败 | selector | 答案表示或融合位置 |
| R6 Oracle 无收益 | learned selector | 音频目标、答案表示、融合 |

任何阶段出现数据泄漏、test 污染、许可证不清、SHA 不可复现、NaN/Inf、环境依赖冲突或
需要破坏 M4 环境时，立即停止并保留原始错误。

回退分两层：

- 行为回退：不插入任何 scene-audio token，原 M4 token、position、mask 和 cache 保持不变；
- 权重回退：显式加载保留的 12k/32k no-freeze 基线，不用“冻结”冒充恢复旧权重。

## 19. 完整性验收定义

本计划只有同时满足下列条件才算完成，而不是“成功训练出一个 projector”：

### 19.1 逻辑链路完整

- 教师职责、学生职责、question relevance 和答案生成边界互不混淆；
- clip 资格审查与 window 部署之间有独立 R3-W；
- 类别语义与同类实例内容由双目标共同保留；
- Qwen bridge 有明确监督目标；
- selector 只有在下游 Oracle 有收益后才获得 learned 路线授权。

### 19.2 工程链路完整

- 官方教师资产、revision、SHA、预处理和双视图可复现；
- `valid_sample_count`、mask、timestamp 从解码贯穿到 token；
- 768/512/3584 三层表征接口明确且不破坏旧路径；
- scene audio 与 query speech 可共存；
- attention、position、cache、save/reload 全部有测试；
- 关闭 scene audio 时真正插入零个 token；
- 正式推理不依赖教师；
- 失败时可行为回退和权重回退。

### 19.3 最终授权链

```text
教师 R2 合格
≠ projector 自动合格
≠ window 自动合格
≠ Qwen bridge 自动合格
≠ fusion 自动有效
≠ selector 自动有用

只有：
R2-T0 PASS
→ R2-Q 一次事务完成
→ R2-T1 PASS
→ R2-T2C PASS
→ R2-T2W capability 已锁定
→ R3-C PASS
→ R3-W PASS
→ R4-E PASS
→ R4-B PASS
→ R5 PASS
→ R6 Oracle PASS

才允许讨论 learned dynamic window selector。
```
