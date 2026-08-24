# LLP S1 失败分析：弱标签原型污染、Speech 与类别不平衡

## 结论

本轮诊断流程为 `PASS_DIAGNOSTIC_COMPLETE`，独立审计为 `PASS`，但原始 S1
结论保持 `FAIL_UNCHANGED`。没有训练参数、没有访问 LLP formal test，也没有解锁下游阶段。

三项假设的结论如下：

1. **弱标签 prototype 污染真实存在，但不能单独解释 S1 FAIL。** 63% 的 train 视频是
   多标签，非 Speech 类 prototype 来源条目中 43.33% 同时带 Speech；但污染率与 Recall 的
   置换相关性不显著。仅使用单标签来源后 macro Recall@1 方向性提高 3.97 个百分点，
   semantic margin 反而下降 0.00376。
2. **Speech 是数据结构中的共现中心，但主要问题不是 Speech 过度预测。** Speech 覆盖
   45% train 视频、68% validation 视频和 34.34% validation query；其自身 Recall 只有
   24.51%。非 Speech query 被误报为 Speech 的比例只有 7.69%，更明显的是 Speech 被错分成
   Blender、Baby cry、Chicken/rooster 等其他类别。
3. **类别不平衡很严重，但当前 25 类样本没有证明单调因果关系。** train 最大/最小频次比
   为 11.25，validation query 最大/最小比为 34；频次与 Recall/margin 的五组 Spearman
   置换检验均不显著。

最直接且跨 seed 稳定的结构性问题是 **prototype 表示空间过度纠缠**：25 类 prototype
两两 cosine 均值为 0.8365，95 分位约 0.9410，最大 0.9743；train 中共同出现过的类别对
比从未共现的类别对平均再高 0.0909。这与原 S1 的 negative semantic margin 一致。

## 数据范围

- official train：100 个物理视频；
- official validation：50 个物理视频；
- 类别：25；
- dense target video-label query：99；
- prototype seed：17、29、43；
- formal test：0；
- 训练：0。

## 弱标签污染

Train 视频弱标签基数分布：

| 每视频标签数 | 视频数 |
| ---: | ---: |
| 1 | 37 |
| 2 | 54 |
| 3 | 9 |

- 每视频弱标签均值：1.72；
- 单标签视频比例：37%；
- 多标签视频比例：63%；
- 三 seed、24 个非 Speech 类 prototype 来源条目：270；
- 其中同时带 Speech：43.33%。

代表性污染结构：

| 类别 | 单标签来源比例 | Speech 共标签比例 | Recall mean | Margin mean | 稳定最近 prototype |
| --- | ---: | ---: | ---: | ---: | --- |
| Acoustic_guitar | 0 | 0.3333 | 0 | -0.06165 | Banjo/Cello |
| Banjo | 0 | 0.2222 | 0.5000 | 0.00738 | Violin/Acoustic guitar/Singing |
| Cello | 0 | 0.3333 | 0.2000 | -0.01610 | Violin |
| Cat | 0 | 0.8889 | 0 | -0.02232 | Speech |
| Dog | 0 | 0.5556 | 0.6667 | 0.00110 | Speech |
| Basketball_bounce | 0.1111 | 0.8889 | 0.3333 | -0.01268 | Speech |
| Lawn_mower | 0.1111 | 0.6667 | 0.3333 | -0.02801 | Motorcycle |
| Motorcycle | 0.1667 | 0.6667 | 0.8333 | 0.01571 | Lawn mower |
| Singing | 0.2222 | 0.3889 | 0.7222 | 0.00907 | Speech/Violin |
| Speech | 0 | — | 0.2451 | -0.01461 | Dog |

“污染率高”并不自动等于“效果差”：Motorcycle 的 Speech 共标签率为 66.67%，但 Recall
仍为 83.33%；Baby cry 同样有较高 Speech 共现但 Recall 为 100%。因此污染需要与
prototype geometry 一起解释。

## Prototype geometry

| Seed | 300 类别对 cosine mean | median | p95 | max | 共现类对 mean | 非共现类对 mean | 差值 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 17 | 0.82646 | 0.85087 | 0.94013 | 0.97412 | 0.91155 | 0.81337 | 0.09818 |
| 29 | 0.84279 | 0.86230 | 0.94124 | 0.97433 | 0.91859 | 0.83112 | 0.08747 |
| 43 | 0.84021 | 0.85702 | 0.94164 | 0.97348 | 0.91577 | 0.82859 | 0.08718 |

三个 seed 的方向一致：整个 prototype 空间高度相似；40 个曾共现类别对比 260 个未共现
类别对更靠近。这会让正确类与最难错误类的分差接近或低于 0，即使 Recall@1 已高于随机。

典型近邻包括：Cello↔Violin cosine 约 0.968–0.974、Speech↔Dog 约
0.965–0.973、Lawn mower↔Motorcycle 约 0.942–0.958、Baby cry↔Baby laughter
最高约 0.960。

## Speech 分析

| 指标 | 数值 |
| --- | ---: |
| Train Speech 视频 | 45/100（45%） |
| Validation Speech 视频 | 34/50（68%） |
| Validation Speech query | 34/99（34.34%） |
| Speech Recall mean | 0.24510 |
| Speech Recall seeds | 0.26471 / 0.26471 / 0.20588 |
| 非 Speech query 误报 Speech | 0.07692 |
| 非 Speech prototype 来源带 Speech | 0.43333 |

Speech 最常被错分为：Blender 14、Baby cry 13、Chicken/rooster 12、Motorcycle 7、
Cheering/Vacuum cleaner/Singing 各 4。其他类别被错分为 Speech 的主要来源是
Basketball bounce 7，其他类别最多为 2。

因此当前更像是“Speech prototype 被大量共标签来源拉向其他事件”，而不是模型形成了简单的
Speech 预测偏置。

## 类别不平衡与置换相关性

| 关系 | Spearman rho | 双侧置换 p | n |
| --- | ---: | ---: | ---: |
| train weak count vs Recall | 0.18868 | 0.35506 | 25 |
| validation query count vs Recall | 0.15020 | 0.47465 | 25 |
| train weak count vs semantic margin | 0.09448 | 0.65153 | 25 |
| single-label fraction vs Recall | 0.03362 | 0.87111 | 25 |
| Speech 共标签率 vs Recall（非 Speech 类） | -0.05754 | 0.78322 | 24 |

五项均未达到常用显著性标准。这不能证明不平衡没有影响：25 类且多个类别只有 1–2 个
validation query，统计功效有限；它只说明当前样本不支持“频次越多就稳定越好/越坏”的简单
单调解释。

Train 频次分层：

| 分层 | 类别数 | Recall mean | Margin mean |
| --- | ---: | ---: | ---: |
| count=4 | 15 | 0.39101 | -0.00531 |
| count=5–9 | 8 | 0.49028 | -0.00803 |
| count≥10 | 2 | 0.38922 | -0.00879 |

Validation query 分层：

| 分层 | 类别数 | Recall mean | Margin mean |
| --- | ---: | ---: | ---: |
| count=1–2 | 17 | 0.40196 | -0.00816 |
| count=3–5 | 5 | 0.45778 | -0.00471 |
| count≥6 | 3 | 0.48117 | 0.00028 |

## 单标签 prototype 反事实

仅12类拥有至少2个纯单标签 train 来源，共覆盖35个 validation query。为避免类别集合变化
造成不公平比较，all-weak 和 single-label 两个条件都只在同一12类中分类。

| 条件 | Macro Recall@1 | 视频等权 semantic margin |
| --- | ---: | ---: |
| 同12类、全部 weak 来源 | 0.55873 | 0.02010 |
| 同12类、仅单标签来源 | 0.59841 | 0.01634 |
| Single-label − all-weak | +0.03968 | **-0.00376** |

改善类别：Blender、Frying(food)、Vacuum cleaner；退化类别：Baby laughter、Singing；
其余7类不变。Recall 有方向性提升，但 margin 下降，且只覆盖12/25类、35/99 query，不能
把它晋级为新候选，也不能推翻原 S1 FAIL。

## 独立审计

- 状态：`PASS`；
- 独立复核 train/validation/query：100/50/99；
- Speech query：34；
- 非 Speech prototype source entries：270；
- 三 seed geometry 全部重算一致；
- analysis SHA、contract SHA 与 manifest lock 一致；
- formal test：0；训练：0。

## 下一步限制与建议

现在不应直接训练 projector、fusion、selector 或语言模型。最合理的下一步仍属于新的 S1
数据/表示合同，而非下游阶段：

1. 获取与当前 validation 物理隔离的、更纯净音频事件 prototype 来源，避免 weak audio-visual
   union 直接充当音频类别原型；
2. 预注册冻结的去各向异性/中心化表示诊断，先确认能否降低 prototype pair cosine，同时
   保持 correct>mismatch；
3. 对 Speech、Cello/Violin、Lawn mower/Motorcycle、Baby cry/laughter 建立显式
   冲突组门禁；
4. 新候选必须使用新的开发/确认边界，不能继续在已经访问的50条 validation 上调参；
5. formal test 仍保持封存。

逐条 YouTube license 仍未核验；本分析仅限内部非商业研究，不构成法律或机构批准。
