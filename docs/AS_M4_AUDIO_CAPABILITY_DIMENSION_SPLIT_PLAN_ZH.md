# AS-M4 音频能力多基准分维度测评计划

日期：2026-09-19（Asia/Shanghai）

状态：`MAINLINE_FROZEN_DIMENSION_SPLIT_V1`

本计划覆盖并更新 `docs/AS_M4_UNAV_OMNIMMI_MAIN_EXPERIMENT_PLAN_ZH.md` 中“LLP 只保留为历史诊断”的旧决定。UnAV-100、LLP/AVVP 和 OmniMMI 不合并样本、不共享指标分母，也不汇总为单一总分；每个测评维度只能使用一个冻结测试集。

## 1. 研究目标

对同一个冻结 AS-M4 checkpoint 建立三层证据链：

1. `UnAV-100` 回答“模型能否在长视频里找到应该听的时间窗口”；
2. `LLP/AVVP` 回答“模型能否判断应该依赖声音、画面还是二者”；
3. `OmniMMI` 只回答“相对原始 M4，视频理解与原任务能力是否下降”。

三层证据相互补充但不能相互替代。机制指标不得外推为最终问答收益，最终问答指标也不得反推定位或模态选择能力。

## 2. 单维度单测试集合同

| 测评维度 | 唯一测试集 | 固定 split | 允许的核心指标 |
| --- | --- | --- | --- |
| 时间事件定位 | UnAV-100 | validation | Event Coverage、Window Hit、Positive-window Recall、mean best tIoU |
| 固定预算选窗 | UnAV-100 | validation | Coverage@K、first-hit rank、headroom recovery、selector latency |
| 模态依赖识别 | LLP/AVVP | validation | audio-only / visual-only / audio-visual macro Recall、balanced accuracy |
| 音频选择性与抗干扰 | LLP/AVVP | validation | correct-mismatch margin、correct-silence margin、visual-only flip rate |
| 视频理解回归 | OmniMMI | PT/PA full | PT Accuracy delta、PA Accuracy/Precision/IoU delta、空输出率 delta |

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

## 5. OmniMMI 视频能力回归线

OmniMMI 保持独立，只承担相对原始 M4 的视频理解能力回归检查，不承担音频收益、音频依赖或模态选择性证明。使用 PT/PA 原始完整评测集和官方 evaluator，不需要新增 `audio_required/visual_only/audio_visual_joint` 人工标签；此前准备的 89 条人工复核工作包退出当前主线，只保留为历史候选资产。

主比较必须固定为：

1. 原始 M4 checkpoint 的原始视频理解结果；
2. 待评 AS-M4 checkpoint 在 `behavior_off/video_only` 下的结果；
3. 两者使用相同视频、问题、prompt、帧采样、解码参数和官方评分入口；
4. 报告 PT Accuracy 与 PA Accuracy/Precision/IoU 的绝对差值、配对置信区间、空输出率和逐样本翻转；
5. 非劣容差必须在读取新的 AS-M4 预测前冻结，不能根据结果回调。

`correct_audio/mismatch_audio/silence` 不属于这条回归线的必需条件；它们如需评测，归入 LLP 音频选择性维度或另立诊断，不得改变 OmniMMI 的视频回归结论。OmniMMI 不进入 UnAV 或 LLP 的机制指标，也不使用其样本补齐类别或分母。

## 6. 执行阶段与门禁

### A. 合同与 CPU 审计

- 冻结本计划和机器可读合同；
- 审计每个维度只有一个数据集、一个 split 和唯一指标所有权；
- 统计 LLP validation 的模态分布，但不把旧 S1 结果视为通过；
- 核对 UnAV 冻结资产和 OmniMMI 原始 M4 基线状态；
- 本阶段只允许 CPU/mock，不加载 7B，不运行 GPU。

### B. evaluator smoke

- UnAV：冻结小子集验证六策略输入输出、K=4/K=8 和 Random 可复现性；
- LLP：冻结小子集验证四条件、模态标签、mismatch 冲突、silence 回退和视频聚类统计；
- OmniMMI：物理 GPU 2 上 PT limit=2 的原始 M4 与 AS-M4 behavior-off 配对 smoke 已通过，2/2 预测与 2/2 全词表末位 logits 精确一致；无需人工模态复核。smoke 授权已重新关闭，完整 PT/PA 仍关闭。

### C. 完整冻结评测

- 每条线单独生成结果、SHA、置信区间和失败原因；
- 只做横向并列报告，不生成跨数据集综合分；
- 任一 evaluator 的数据、权限或合同失败，只阻断该维度，不用另一数据集替代。

### D. 模型训练决策

只有 UnAV 定位/选窗、LLP 模态选择性和 OmniMMI 视频能力非劣的证据链均完整后，才重新评审 150-step 或正式训练。当前 `gpu_smoke=false`、`gpu_150step=false`、`formal_training=false`。

## 7. 当前状态

- UnAV：`CPU_FIVE_STRATEGIES_PASS_LEARNED_MISSING`，Random/Energy/Eventness/Temporal/Oracle 已完成同口径 K=4/K=8 评测；仅缺 learned selector 与冻结 M4 下游传递验证；
- LLP：32 组/224 条 manifest、mock evaluator、真实 M4 runner CPU dry-run 和 audio-only/visual-only 各 1 组 GPU teacher-forced smoke 已完成；管线通过但音频选择性未证明，完整 validation 媒体仍未准备；
- OmniMMI：`PT_LIMIT2_EXACT_ROLLBACK_PASS_FULL_CLOSED`，原始 M4 与 AS-M4 behavior-off 两条配对 smoke 的预测及 logits 精确一致；无需新增人工标签；
- 全局：`OMNIMMI_PT_LIMIT2_SMOKE_PASS_FULL_CLOSED`；150-step、训练和完整 PT/PA 继续关闭。

## 8. 人话结论

UnAV 考“该听哪一段”，LLP 考“该不该听、声音是否真的有用”，OmniMMI 只做原能力体检，检查 AS-M4 是否把视频理解弄坏。三套卷子考不同能力，每门课只用一套卷子评分，不把分数混成一个看似漂亮但无法解释的总分。
