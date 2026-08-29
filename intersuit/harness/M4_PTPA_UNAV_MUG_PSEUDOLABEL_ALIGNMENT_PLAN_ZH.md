# M4/PTPA UnAV：MUG 风格伪标签增强音视频对齐实体计划书

## 1. 目标

在不训练 BEATs、不修改 M4/Qwen2 和任何视觉模块的前提下，利用 UnAV 官方 onset/offset 强时间事件标注，训练纯音频 semantic projector，重点解决 projector 相比 raw BEATs 的类别召回下降问题。本阶段不引入视觉伪标签，也不执行跨模态随机组合。

本计划只承担 UnAV 事件语义与音视频对齐验证，不承担最终 M4 问答效果证明。

## 2. 已冻结基线

- BEATs checkpoint：`BEATs_iter3_plus_AS2M.pt`，SHA256=`d43cbfad...18cd8d34`。
- UnAV 冻结特征 manifest SHA256=`57b0bc55...eb935f0`，7941 条 train/validation，334769 个 1 秒窗口。
- 当前 temporal projector v2：SHA256=`0bf779db...f92a610`；validation Macro Recall@1=`0.62795`，margin=`0.02133`。
- raw BEATs 参考：validation Macro Recall@1=`0.68056`，margin=`0.01755`。
- v4 active hard-negative 候选：SHA256=`3c514457...7bc866`。在统一的 2413 个 validation query 上，raw BEATs Macro Recall@1=`0.637547`、v4=`0.637742`，差值=`+0.000195`；margin 从`0.015302`提升到`0.018008`，raw 正确样本保持率=`99.67%`。这里的统一口径替代早期不可直接横比的 2413/4070 query 数值。

## 3. 数据与伪标签

### 3.1 正确配对

音频和视频来自同一物理视频，事件标签只由官方 `event_label` 与 `onset/offset` 产生。窗口标签按时间重叠秒数加权，禁止把 clip label 复制到全部窗口。

### 3.2 纯音频强时间事件原型

只使用 UnAV 官方 `event_label`、`onset/offset` 和冻结 BEATs 1 秒窗口特征。按事件与窗口的重叠秒数加权池化；重叠事件保留多标签。禁止把视频级标签复制到全部窗口。

### 3.3 本阶段不做 CMRC

MUG 的跨模态随机组合属于后续音视频对齐阶段，本阶段不使用视觉输入、不生成视觉伪标签、不构造音视频混合样本。当前只研究纯音频 projector 为什么损失类别召回，以及如何保持 raw BEATs 几何。

## 4. 冻结与可训练范围

冻结：BEATs、M4/Qwen2、视觉主干、原有语言模型。

首轮只训练：audio semantic projector。

首轮禁止：fusion、dynamic selector、语言模型联合训练、BEATs 微调。

## 5. 损失函数

`L = L_event_semantic + L_temporal_overlap + L_hard_negative + λ L_identity`。

- `L_event_semantic`：多标签事件语义对齐；
- `L_temporal_overlap`：约束 onset/offset 与窗口激活一致；
- `L_hard_negative`：正确配对优于错配、inactive same-video 和跨视频 hard negative；
- `L_identity`：保持 raw BEATs 几何，避免 projector 退化。

## 6. 训练阶梯

依次执行 `2-step smoke → 20-step canary → 150-step → 300-step`。每阶段保存配置、伪标签 SHA、数据来源、参数冻结审计、loss、显存和退出状态。

## 7. 独立评估

validation 只允许一次确认，比较 raw BEATs、旧 projector v1、temporal projector v2 和新的纯音频 projector。指标包括 Macro Recall@1、balanced accuracy、positive-minus-hard-negative margin、correct-minus-mismatch、每类 Recall、零召回类别数和物理视频 bootstrap CI。

## 8. 通过门槛

新模型必须同时满足：margin 严格大于 0、margin CI 下界大于 0、三个 mismatch seed 均为 `correct > mismatch`、不能出现大量零召回类别；Macro Recall@1 不得较 raw BEATs 显著恶化。

## 9. 回退与停止

任一事件区间审计、泄漏检查、负样本规则或 validation 门禁失败，立即停止，不接入 M4。回退到 raw BEATs 或已冻结 projector v2；不得用冻结/删模块宣称恢复旧权重。

## 10. 当前执行入口

第 1 步：冻结基线与 SHA 锁。
第 2 步：构建并独立审计纯音频强时间事件原型与 hard-negative manifest。
第 3 步：训练召回保持型纯音频 projector。
第 4 步：运行 `2-step → 20-step → 150-step → 300-step` 训练阶梯。
第 5 步：validation 一次性独立评估并与 raw BEATs/projector v2 对比。
第 6 步：只有纯音频门禁通过后，才讨论是否进入 M4 gate0 诊断。

## 11. 2026-08-29 执行状态

- 第 1–4 步已完成，v4 active hard-negative 版本已冻结保存；BEATs、M4、fusion 和 test 均未训练或访问。
- 第 5 步的统一评估和物理视频级 bootstrap CI 已完成。三个 seed 各5000次重采样均显示Macro Recall差值CI跨0、v4绝对margin及margin增量CI下界严格为正，因此只允许宣称“召回与raw统计上不可区分、margin稳定提升”，不允许宣称召回显著提升。
- v4 已实现隔离的`768→768 semantic projector + 768→3584 M4 adapter`接口并通过 CPU shape、SHA、冻结和 gate0 回退测试。
- 当前进入第 6 步前置门禁，只允许只读 behavior-off/gate0 smoke；不允许非零 gate correct 推理、训练、BEATs 解冻或 test 访问。

## 12. M4 gate0 前置合同

首轮只使用`M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1`诊断基座和 dev760 的首个非 test 样本。v4 semantic projector 按 SHA 锁定；`768→3584` adapter 必须复用并冻结该基座已有的`audio_projector`，禁止使用随机初始化 adapter 解释语义效果。

只运行两种条件：

1. `V4_BEHAVIOR_OFF`：完全关闭 scene audio；
2. `V4_GATE0`：真实同步音频经过冻结 BEATs 和 v4 projector，但强制残差 gate 为 0。

通过标准为两条件预测精确一致、gate0 音频残差精确为 0、所有冻结运行模块推理前后 SHA 不变且无 NaN/Inf。该 smoke 只证明接口和行为回退，不证明 v4 能提升 M4；只有 gate0 通过并补齐 adapter 来源审计后，才另行授权非零 gate correct/mismatch 诊断。

## 13. gate0 执行结果

2026-08-29在物理GPU2完成真实7B单样本smoke，独立审计状态为`PASS_V4_M4_GATE0_EXACT_ROLLBACK`。behavior-off与gate0预测及9个生成token精确一致；gate0解码20个音频窗口且audio norm为`9.93586`，但所有gate/delta均精确为0，冻结模块SHA不变，运行后无Xid/AER/hung task。

该样本两条件均回答不完整且错误，因此只能关闭接口回退门禁，不能证明M4效果。非零gate仍需新合同，不自动授权。
