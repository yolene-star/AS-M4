# AS-M4 音频语义重构计划整体 Review

日期：2026-07-25（Asia/Shanghai）

## 1. Review 结论

`docs/AUDIO_SEMANTIC_RECONSTRUCTION_DIAGNOSTIC_PLAN_ZH.md` 已按完整工作流重新设计，
不是在 Dynamic Window Selector V2 上追加补丁。Review 结论为：

```text
plan_status: CONDITIONALLY_APPROVED_AFTER_SELECTOR_V1
training_status: NOT_AUTHORIZED
selector_localization_status: ACTIVE_MEDIA_GATED
selector_downstream_status: BLOCKED_BY_AUDIO_PATH
formal_test_status: NOT_AUTHORIZED
```

当前计划能够把冻结表示、projector、答案目标、fusion 和下游评分五类问题分开证伪；但它
只在 Selector V2 的 V1 Oracle 定位 headroom 通过后用于修复下游 blocker，不再作为主入口。
在自身 R1 前不会引入新的可训练模块，也不会继续使用 dev760 调参。

## 2. 代码链路 Review

### 2.1 Semantic ranking

实现位置：`intersuit/intersuit/model/language_model/llava_qwen.py`。

核验结果：

- `semantic_labels` 在 multimodal packing 前保存；有效 label 对应答案 token；
- target 是 Qwen input token embedding 的无上下文均值并 `detach`；
- 正/负音频 window feature 分别做 mask mean pooling；
- cosine margin ranking 数值公式正确，CPU 单元测试覆盖正负方向与反传；
- loss 对 scene-audio projector 有直接梯度；
- loss 不经过 fusion，因此没有直接训练 fusion 的 correct-vs-mismatch 选择性。

结论：没有发现公式实现层面的明显反号 bug；主要问题是目标定义和模块职责错配。

### 2.2 Negative builder

实现位置：`intersuit/scripts/build_stage2_semantic_negative_manifest.py`。

核验结果：不同物理媒体/路径约束有效；但当正样本或候选缺少 `event_label` 时，代码允许
配对继续。2430 条中 1230 条因此无法证明跨语义负样本。旧命名“semantic negative”超出了
实际 contract。

### 2.3 Fusion

实现位置：`intersuit/intersuit/model/streaming_av/fusion.py` 与
`intersuit/intersuit/model/llava_arch.py`。

核验结果：

- gate=0、silence 与 residual cap 数值回退有测试；
- fixed BEATs 路径把 window feature 线性插值到帧；
- projected audio delta 在空间维广播到该帧全部视觉 token；
- dev760 correct/mismatch 残差比几乎相同；
- 当前 token 没有显式 audio modality identity。

结论：数值安全 contract 通过，语义接口 contract 未被证明。

### 2.4 Prediction scorer

实现位置：`intersuit/intersuit/harness/runners/run_predictions_from_plan.py`。

核验结果：

- A–D 选择题 exact match 行为明确；
- `label_terms` 对普通标签使用词边界，对括号限定做移除，对逗号标签拆分后任一命中；
- scorer 有单元测试；
- scorer 适合事件标签包含判断，但不能作为开放文本语义质量的唯一指标；
- R5 必须预注册主 scorer 并把其他 normalization 作为敏感性分析。

## 3. 数据集 Review

### 3.1 AVE_HF_EXPANDED

本地实际检查：1000 个视频、1000 个音频；报告显示 1000/1000 可解码，28 类均衡抽样。
已有 300 样本窗口特征 manifest，音频 768 维、视频 1024 维，294 个样本 20 窗、6 个样本
19 窗，finite check 通过。

本地 `start_seconds` 是源视频片段偏移，不是可靠事件边界。既有 projector 把全部窗口复制为
视频级事件标签，属于弱标签扩增并可能包含背景窗口。判定：可用于每视频一个表示的 clip-level
R1 pilot 和 R2/R3 开发；不能用于 window-level 监督，也不能生成新的 AVE official test 结论。

既有 300 样本 BEATs metadata 已核验为锁定 checkpoint SHA，但窗口 contract 是 1.0s/0.5s，
不是正式计划的 1.0s/1.0s。因此将其资格降为 evaluator 开发与数值预检；正式 R1 pilot 前
必须重新提取。这避免了复用资产时静默改变窗口分布。

### 3.2 AS_M4_STAGE2

train 2430、dev 780，物理媒体零重叠。其来源混合 AVE_HF、AVUT、MUSIC-AVQA；旧训练中
存在 753 纯视觉、664 字母答案、1230 缺标签。判定：适合作为历史失败证据，不适合作为
新的统一音频语义训练集。

### 3.3 UnAV-100 与 LongVALE

两者格式和官方指标已核验；UnAV 去污染 manifest 已冻结。两者当前均 `media_verified=false`。
计划已将其设为条件候选而非立即可执行依赖，避免再次把“仓库格式可用”误写为“本地实验可跑”。

### 3.4 删除/排除项

MUSIC-AVQA、QVHighlights、AVQA 不参与本路线主门禁；AVUT 仅可用于后续人类 QA 观察，
不能用 A–D 字母作为语义目标。该边界与既有数据污染审计一致。

## 4. 实验设计 Bug Review

已排除的设计 bug：

1. 不再用 dev760 调 gate 或选结构；
2. 不把 raw BEATs、projector、fusion 和 selector 联合训练；
3. 不把 visual-only 混入音频语义主门禁；
4. 不把 A–D 字母当语义文本；
5. 不把同类不同视频当负样本；
6. 不把媒体未验证的数据集写成可执行；
7. 不用 test 结果回头选择 checkpoint/阈值；
8. 不因 Oracle/selector 代码已存在就绕过音频路径 selectivity；
9. 不用单次 seed 或训练集过拟合判定泛化；
10. 不把 scorer 修改当作模型收益。
11. 不把 clip-level 标签复制到全部窗口后声称获得 window-level 监督；

仍需在 R0 实现后验证的风险：

- AVE_HF 不具备可靠本地事件区间，R1–R3 必须保持 clip-level；
- canonical label 同义词表是否会合并不同类别；
- 300 样本 pilot 的长尾类别过少，主门禁应只在合格类别上执行并单报长尾；
- raw BEATs 特征是否为当前锁定 BEATs checkpoint 生成；
- 新提取的 R1 特征是否严格为 1.0s/1.0s，并记录 tail contract；
- 既有 feature manifest 路径是否全部存在且 SHA 可冻结；
- 新 `reserve_new/dev_new` 是否能找到足够的未暴露物理媒体。

这些风险全部被安排在训练之前 fail-closed，不构成现在启动训练的理由。

## 5. 指标与门禁 Review

R1–R3 使用 balanced/class-aware 指标，避免类别数量主导总准确率；同时要求 margin CI、
worst seed、per-class 和 train-validation gap，能区分过拟合与真实泛化。

R4–R5 使用成对条件：video-only、correct、hard mismatch、same-class mismatch、silence、gate0。
这比旧 correct/mismatch 二元比较更能区分：事件类别语义、实例噪声、纯幅度扰动和回退行为。

所有晋级条件均为合取；不得以某个局部指标通过覆盖关键条件失败。

## 6. 环境与执行基础 Review

- Python 解释器已核验为 `/home/yjm/miniconda3/envs/M4/bin/python`；
- 基础锁脚本在 `M4` 环境只读运行通过；
- 当前 R0/R1 可优先使用已有离线特征，不需要 GPU；
- 后续若进入 R3，单卡显式选择，不默认使用全部 GPU；
- 正式多卡仍遵守 `0,2,3,4`、NCCL `lo` 与 preflight 规则；
- 不提交数据、特征、checkpoint、缓存或日志。

## 7. 最终批准范围

条件批准（仅在 Selector V2 V1 通过后执行）：

- R0 manifest/label/feature contract；
- R1 不训练的 raw BEATs 可分性 pilot；
- evaluator golden tests、静态测试和 review。

暂不批准：

- projector 训练；
- fusion 改动；
- 新 gate sweep；
- Dynamic Window Selector；
- dev/test 正式评测；
- LongVALE/UnAV 媒体下载与 GPU 实验。

当前更早的决策点是 Selector V2 V1 的定位上界 `decision.json`。只有该门禁通过，才轮到本
计划的 R1 pilot 与独立 review，而不是训练 loss 是否下降。
