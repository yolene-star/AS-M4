# M4/PTPA UnAV：MUG 风格伪标签增强音视频对齐实体计划书

## 1. 目标

在不训练 BEATs、不修改 M4/Qwen2 和任何视觉模块的前提下，利用 UnAV 官方 onset/offset 强时间事件标注，训练纯音频 semantic projector，重点解决 projector 相比 raw BEATs 的类别召回下降问题。本阶段不引入视觉伪标签，也不执行跨模态随机组合。

本计划只承担 UnAV 事件语义与音视频对齐验证，不承担最终 M4 问答效果证明。

## 2. 已冻结基线

- BEATs checkpoint：`BEATs_iter3_plus_AS2M.pt`，SHA256=`d43cbfad...18cd8d34`。
- UnAV 冻结特征 manifest SHA256=`57b0bc55...eb935f0`，7941 条 train/validation，334769 个 1 秒窗口。
- 当前 temporal projector v2：SHA256=`0bf779db...f92a610`；validation Macro Recall@1=`0.62795`，margin=`0.02133`。
- raw BEATs 参考：validation Macro Recall@1=`0.68056`，margin=`0.01755`。

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
