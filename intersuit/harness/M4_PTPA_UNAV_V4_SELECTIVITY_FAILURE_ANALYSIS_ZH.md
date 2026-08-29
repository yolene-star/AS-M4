# UnAV v4 正确/错配音频选择性失败分析

## 结论

正确音频选择性并不是在 raw BEATs 或 v4 semantic projector 中消失的。主要瓶颈是复用的旧`streaming_av_module.fusion.audio_projector`：它把仍然明显不同的correct/mismatch表示投影到几乎相同的方向；随后固定gate=`0.005`又把差异缩小到相对视频特征约十万分之几。

## 逐层证据

对3条纯音频任务的correct/mismatch配对，比较各层时间均值向量：

| 阶段 | 平均余弦相似度 | 平均相对差异 |
|---|---:|---:|
| raw BEATs | 0.7525 | 0.6956 |
| v4 semantic | 0.7093 | 0.7468 |
| 旧 M4 adapter | 0.8609 | 0.5134 |
| 旧 fusion projector 输出（gate前） | 0.9987 | 0.0618 |

v4使余弦相似度下降、相对差异上升，因此没有抹平语义。旧adapter造成部分压缩，但仍保留约0.51的相对差异。经过旧fusion projector后余弦升至0.9987、差异降至0.0618，选择性基本被压成公共方向。

adapter bias norm为0.1987，weight-only窗口信号norm均值为1.6268，二者比值仅0.122，因此不是bias主导所有输出相似。

## 为什么最终几乎没有决策差异

旧fusion projector来自早期BEATs semantic-contrast音频分支，训练时接收的是旧raw-BEATs adapter分布，而当前输入是归一化后的v4表示。虽然复用旧adapter能保证接口形状，但不能保证旧fusion的判别子空间仍与新表示对齐。

fusion输出的correct/mismatch方向已经几乎相同；乘以0.005 gate后，最终残差只有视频norm的约`5.1e-5–6.6e-5`。因此语言模型看到的是极小且方向近似一致的扰动。3条纯音频任务完全没有预测变化，30条诊断中的变化只出现在视觉描述措辞，也说明当前残差更像非选择性扰动，而不是有效音频证据。

## 排除项

- 不是v4本身没有事件语义：UnAV统一评估和物理视频级CI均已通过margin提升门禁；
- 不是adapter bias淹没内容：bias/weight信号比仅0.122；
- 暂无证据把语言模型列为首要原因：差异在进入语言模型前的fusion层已基本消失；
- 不能靠直接放大gate解决：放大会同时放大correct和mismatch的近同向扰动。

## 建议

下一步只建立隔离的fusion重构合同：冻结BEATs、v4、M4和旧基线，先在CPU/小张量上比较identity fusion、旁路旧fusion、以及受约束的新轻量fusion是否保持correct/mismatch差异。通过表示保持门禁后，才允许新的单样本gate0和极小非零gate诊断；不直接训练全模型，不访问test。
