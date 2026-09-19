# AS-M4 音频能力多基准分维度测评计划

日期：2026-09-19（Asia/Shanghai）

状态：`MAINLINE_FROZEN_DIMENSION_SPLIT_V1`

本计划覆盖并更新 `docs/AS_M4_UNAV_OMNIMMI_MAIN_EXPERIMENT_PLAN_ZH.md` 中“LLP 只保留为历史诊断”的旧决定。UnAV-100、LLP/AVVP 和 OmniMMI 不合并样本、不共享指标分母，也不汇总为单一总分；每个测评维度只能使用一个冻结测试集。

## 1. 研究目标

对同一个冻结 AS-M4 checkpoint 建立三层证据链：

1. `UnAV-100` 回答“模型能否在长视频里找到应该听的时间窗口”；
2. `LLP/AVVP` 回答“模型能否判断应该依赖声音、画面还是二者”；
3. `OmniMMI` 回答“这些机制是否改善 M4 原任务的最终输出”。

三层证据相互补充但不能相互替代。机制指标不得外推为最终问答收益，最终问答指标也不得反推定位或模态选择能力。

## 2. 单维度单测试集合同

| 测评维度 | 唯一测试集 | 固定 split | 允许的核心指标 |
| --- | --- | --- | --- |
| 时间事件定位 | UnAV-100 | validation | Event Coverage、Window Hit、Positive-window Recall、mean best tIoU |
| 固定预算选窗 | UnAV-100 | validation | Coverage@K、first-hit rank、headroom recovery、selector latency |
| 模态依赖识别 | LLP/AVVP | validation | audio-only / visual-only / audio-visual macro Recall、balanced accuracy |
| 音频选择性与抗干扰 | LLP/AVVP | validation | correct-mismatch margin、correct-silence margin、visual-only flip rate |
| 最终问答效用 | OmniMMI | PT/PA reviewed subset | PT Accuracy、PA Accuracy/Precision/IoU、四条件配对差值 |

强制规则：

- 一个维度的 manifest 中只能出现一个 `dataset` 和一个冻结 split；
- 不允许将 UnAV 与 LLP 的样本放入同一个 Accuracy、Recall、F1 或置信区间分母；
- 不允许计算跨数据集平均总分、加权总分或用样本数加权的“总体音频能力”；
- 同名指标只能归属一个维度，避免同一数值名称承载不同数据语义；
- 三套评测必须记录相同 checkpoint SHA；评测期间禁止更新 BEATs、projector、selector、融合层、视觉塔或语言模型；
- 可以并列展示三个维度的结论，但不得用某一维度的通过掩盖另一维度的失败。

## 3. UnAV-100 职责与执行矩阵

UnAV-100 唯一承担时间定位、固定 K 预算选窗和 selector 策略比较。沿用官方 validation、1 秒不重叠 micro-window、物理视频 cluster bootstrap 和固定 Random seeds。

主矩阵为 Random、Energy、Eventness、Temporal、Oracle、learned selector，K 固定为 4 和 8。现有 Random/Energy/Eventness/Oracle 资产保持冻结；Temporal 必须生成同 schema 分数；learned selector 只能在冻结特征上训练自身，并冻结 train/validation、参数白名单、早停规则和 checkpoint SHA。

UnAV 不承担 audio-only / visual-only 模态判断，也不承担最终问答准确率证明。

## 4. LLP/AVVP 职责与重建要求

LLP/AVVP 唯一承担模态依赖和音频选择性。统一窗口 manifest 已包含 `audio_only`、`visual_only`、`audio_visual` 和 `background`，以及事件标签和窗口边界；正式评测只允许使用官方 validation。合同中的规范名称为 `validation`，现有统一 manifest 的源字段编码为 `val`，审计器必须显式映射，禁止把 `test` 或 `unknown` 混入。

旧 S1 prototype evaluator 的 semantic margin 已失败，不能直接改名为新的模态能力成绩。新 evaluator 必须重建为窗口级、按物理视频聚类统计的四条件合同：

1. `correct_audio`：原视频正确音频；
2. `mismatch_audio`：不同物理视频、不同目标标签的音频；
3. `video_only`：不注入 scene-audio；
4. `silence`：注入全零音频并验证回退。

正式运行前必须解决：媒体/效果使用权限、16 kHz mono waveform 与 SHA、validation 物理媒体隔离、visual-only 可见性复核、类别不平衡分层、Speech 共现污染，以及 mismatch 标签冲突审计。

LLP 不承担长视频稀疏定位、K 预算 selector 排名或 UnAV 六策略矩阵。

## 5. OmniMMI 最终任务线

OmniMMI 保持独立，只承担最终问答效用。继续完成 89 条候选的人工听看复核，冻结 Audio-required、Visual-only、Audio-Visual Joint、ambiguous/excluded 标签以及 correct/mismatch/video-only/silence 四条件 manifest。

OmniMMI 不进入 UnAV 或 LLP 的机制指标，也不使用其样本补齐类别或分母。

## 6. 执行阶段与门禁

### A. 合同与 CPU 审计

- 冻结本计划和机器可读合同；
- 审计每个维度只有一个数据集、一个 split 和唯一指标所有权；
- 统计 LLP validation 的模态分布，但不把旧 S1 结果视为通过；
- 核对 UnAV 冻结资产和 OmniMMI 人工 review 状态；
- 本阶段只允许 CPU/mock，不加载 7B，不运行 GPU。

### B. evaluator smoke

- UnAV：冻结小子集验证六策略输入输出、K=4/K=8 和 Random 可复现性；
- LLP：冻结小子集验证四条件、模态标签、mismatch 冲突、silence 回退和视频聚类统计；
- OmniMMI：只有人工 review 与正式 manifest 冻结后才允许 smoke。

### C. 完整冻结评测

- 每条线单独生成结果、SHA、置信区间和失败原因；
- 只做横向并列报告，不生成跨数据集综合分；
- 任一 evaluator 的数据、权限或合同失败，只阻断该维度，不用另一数据集替代。

### D. 模型训练决策

只有 UnAV 定位/选窗、LLP 模态选择性和 OmniMMI 最终效用的证据链均完整后，才重新评审 150-step 或正式训练。当前 `gpu_smoke=false`、`gpu_150step=false`、`formal_training=false`。

## 7. 当前状态

- UnAV：`READY_PARTIAL_MATRIX`，缺 Temporal 与 learned selector 同口径结果；
- LLP：`BLOCKED_EVALUATOR_RECONSTRUCTION`，数据标注可用，但权限、waveform 和新 evaluator 门禁未完成；
- OmniMMI：`PENDING_HUMAN_MODALITY_REVIEW`，89 条人工复核尚未回填；
- 全局：`CPU_CONTRACT_ONLY`。

## 8. 人话结论

UnAV 考“该听哪一段”，LLP 考“该不该听、声音是否真的有用”，OmniMMI 考“听完以后回答是否更好”。三套卷子考不同能力，每门课只用一套卷子评分，不把分数混成一个看似漂亮但无法解释的总分。
