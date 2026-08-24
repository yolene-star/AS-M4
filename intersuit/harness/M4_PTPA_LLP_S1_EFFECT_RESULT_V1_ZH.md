# LLP S1 冻结 BEATs 音频语义上界结果

## 结论

预注册 S1 总门禁为 **FAIL**，没有表示入选。最强但未通过的表示是
`dense_target_window_mean`。它在 25 类 macro Recall@1 上明显高于随机，且 correct
明显优于随机 mismatch；但三个固定 seed 的正确类分数均未超过最难错误类，semantic
margin 均值和物理视频 bootstrap CI 门禁全部失败。因此按实验顺序停止进入 S2、S3、
S4 和任何下游训练，只允许做 S1 失败分析。

本轮使用 100 个 official train 物理视频、50 个 official validation 物理视频、25 类和
99 个“validation 物理视频–dense audio event label”查询。固定 seed 为 `17/29/43`，
每项每 seed 进行 10,000 次物理视频 cluster bootstrap。LLP formal test 访问为 0，训练
参数为 0。

## 表示汇总

随机类别基线为 `0.04`，macro Recall@1/balanced accuracy 门槛为 `0.24`。

| 表示 | 状态 | macro R@1 mean/worst | rank-first | margin mean / 最差 CI 下界 | correct-mismatch R@1 | correct-mismatch score |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `clip_mean` | FAIL | 0.335051 / 0.304202 | 0.370667 | -0.012095 / -0.021715 | 0.360667 | 0.062208 |
| `rms_top5` | FAIL | 0.334532 / 0.317378 | 0.349333 | -0.016654 / -0.027124 | 0.299333 | 0.064855 |
| `dense_event_union_mean` | FAIL | 0.333875 / 0.303025 | 0.350667 | -0.013093 / -0.022967 | 0.341778 | 0.065614 |
| `dense_target_window_mean` | FAIL | 0.422629 / 0.381445 | 0.407333 | -0.005296 / -0.013350 | 0.390667 | 0.057122 |

四种表示的 macro Recall@1、balanced accuracy、correct>mismatch score 和
correct>mismatch Recall@1 均在三个 seed 上通过。四种表示的 semantic margin mean>0
和 margin CI95 lower>0 均失败。

## 逐 seed 主指标

| 表示 | seed | macro R@1 | 视频等权 rank-first | MRR | semantic margin | margin CI95 | mismatch R@1 |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| clip_mean | 17 | 0.304202 | 0.324000 | 0.559038 | -0.013604 | [-0.021715, -0.006533] | 0.000000 |
| clip_mean | 29 | 0.334398 | 0.384000 | 0.591606 | -0.011538 | [-0.019622, -0.004316] | 0.020000 |
| clip_mean | 43 | 0.366555 | 0.404000 | 0.606516 | -0.011143 | [-0.019463, -0.003276] | 0.010000 |
| rms_top5 | 17 | 0.333221 | 0.364000 | 0.569422 | -0.017984 | [-0.027124, -0.010435] | 0.063333 |
| rms_top5 | 29 | 0.352997 | 0.350000 | 0.563601 | -0.016196 | [-0.027101, -0.005752] | 0.010000 |
| rms_top5 | 43 | 0.317378 | 0.334000 | 0.561932 | -0.015783 | [-0.025435, -0.006462] | 0.076667 |
| dense_event_union_mean | 17 | 0.303025 | 0.304000 | 0.546199 | -0.014773 | [-0.022967, -0.007667] | 0.020000 |
| dense_event_union_mean | 29 | 0.333221 | 0.364000 | 0.581911 | -0.012525 | [-0.020611, -0.005250] | 0.000000 |
| dense_event_union_mean | 43 | 0.365378 | 0.384000 | 0.597800 | -0.011980 | [-0.020190, -0.004149] | 0.006667 |
| dense_target_window_mean | 17 | 0.381445 | 0.390667 | 0.601067 | -0.007639 | [-0.013350, -0.001936] | 0.040000 |
| dense_target_window_mean | 29 | 0.434398 | 0.414000 | 0.626748 | -0.004674 | [-0.011027, 0.001762] | 0.010000 |
| dense_target_window_mean | 43 | 0.452045 | 0.417333 | 0.632167 | -0.003574 | [-0.010559, 0.003681] | 0.000000 |

## 负样本诊断

| 表示 | correct-mismatch score / CI下界 | correct-acoustic score / CI下界 | same-video margin / CI下界 | same-class cosine |
| --- | ---: | ---: | ---: | ---: |
| clip_mean | 0.062208 / 0.036834 | -0.003147 / -0.019042 | 0.112275 / 0.093974 | 0.778685 |
| rms_top5 | 0.064855 / 0.042684 | -0.004721 / -0.020591 | 0.088143 / 0.067989 | 0.730128 |
| dense_event_union_mean | 0.065614 / 0.045141 | 0.001099 / -0.014669 | 0.109427 / 0.089429 | 0.771413 |
| dense_target_window_mean | 0.057122 / 0.032403 | -0.014866 / -0.029906 | 0.093600 / 0.069505 | 0.760057 |

随机 mismatch 很容易区分，但声学最相近且标签不同的跨视频 hard negative 更难：四种表示
中只有 `dense_event_union_mean` 的点估计略正，其 CI 下界仍为负。same-video 高 RMS 非目标
窗口在 60/99 个查询上可构造，四种表示的 margin 和 CI 均为正；其余 39 个查询的目标事件
覆盖全部 10 秒，没有合法 target-inactive 窗口。

## 最强失败表示逐类 Recall@1

| 类别 | query | seed17 | seed29 | seed43 | mean | worst |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Accordion | 2 | 0.500000 | 0.500000 | 1.000000 | 0.666667 | 0.500000 |
| Acoustic_guitar | 2 | 0 | 0 | 0 | 0 | 0 |
| Baby_cry_infant_cry | 2 | 1 | 1 | 1 | 1 | 1 |
| Baby_laughter | 2 | 0 | 0 | 0 | 0 | 0 |
| Banjo | 2 | 0.500000 | 0.500000 | 0.500000 | 0.500000 | 0.500000 |
| Basketball_bounce | 5 | 0.400000 | 0.400000 | 0.200000 | 0.333333 | 0.200000 |
| Blender | 2 | 0 | 0 | 0 | 0 | 0 |
| Car | 1 | 0 | 0 | 0 | 0 | 0 |
| Cat | 1 | 0 | 0 | 0 | 0 | 0 |
| Cello | 5 | 0.200000 | 0 | 0.400000 | 0.200000 | 0 |
| Chainsaw | 3 | 1 | 1 | 1 | 1 | 1 |
| Cheering | 2 | 1 | 0.500000 | 0.500000 | 0.666667 | 0.500000 |
| Chicken_rooster | 2 | 0 | 0.500000 | 0.500000 | 0.333333 | 0 |
| Clapping | 3 | 0 | 0.333333 | 0.333333 | 0.222222 | 0 |
| Dog | 1 | 0 | 1 | 1 | 0.666667 | 0 |
| Fire_alarm | 2 | 0.500000 | 1 | 0.500000 | 0.666667 | 0.500000 |
| Frying_(food) | 2 | 0.500000 | 0.500000 | 1 | 0.666667 | 0.500000 |
| Helicopter | 2 | 0 | 0 | 0 | 0 | 0 |
| Lawn_mower | 2 | 0.500000 | 0.500000 | 0 | 0.333333 | 0 |
| Motorcycle | 2 | 1 | 0.500000 | 1 | 0.833333 | 0.500000 |
| Singing | 6 | 0.500000 | 0.833333 | 0.833333 | 0.722222 | 0.500000 |
| Speech | 34 | 0.264706 | 0.264706 | 0.205882 | 0.245098 | 0.205882 |
| Telephone_bell_ringing | 2 | 0.500000 | 0.500000 | 0.500000 | 0.500000 | 0.500000 |
| Vacuum_cleaner | 7 | 0.571429 | 0.428571 | 0.428571 | 0.476190 | 0.428571 |
| Violin_fiddle | 5 | 0.600000 | 0.600000 | 0.400000 | 0.533333 | 0.400000 |

六类在三个 seed 上 Recall@1 恒为 0：`Acoustic_guitar`、`Baby_laughter`、`Blender`、
`Car`、`Cat`、`Helicopter`。稳定较好的类别是 `Chainsaw`、`Baby_cry_infant_cry`、
`Motorcycle` 和 `Singing`。单样本类别 `Car/Cat/Dog` 的估计方差很高，不能作强结论。

主要混淆包括：Speech→Blender 14、Speech→Baby_cry_infant_cry 13、
Speech→Chicken_rooster 12、Cello→Violin_fiddle 9、Vacuum_cleaner→Car 8、
Basketball_bounce→Speech 7、Speech→Motorcycle 7、Baby_laughter→Baby_cry_infant_cry 6。

## 数据与审计

- validation 50 个视频全部有 dense audio 标注；共 99 个 video-label query。
- 7 个 validation 视频的 weak audio-visual union 与 dense audio union 不完全一致；评测以
  `AVVP_eval_audio.csv` 为准，没有人工改标签。
- 官方 dense audio 全文件有 6 条非正区间，现有官方 parser 按 `offset<=onset` 跳过；与本次
  50 个 validation 视频交集为 0。
- 独立审计复算 12 个 representation×seed 组合的 macro Recall@1、视频等权 rank-first、
  semantic margin 和 correct-mismatch score，结果一致。
- 1188 条 per-query 记录中 mismatch 目标标签冲突 0、acoustic hard-negative 目标标签冲突 0、
  same-class 标签失败 0、prototype 选择失败 0。
- 完整测试：`545 passed, 2 warnings`；警告为既有 SWIG 弃用提示。

## 限制与下一步

LLP train 只有 weak clip 标签；本轮没有把它复制成逐秒监督。dense window 两种表示使用
validation 官方逐秒 audio 标注，属于 oracle 上界，不是可部署 selector。balanced accuracy
按项目既有多类合同等于每类 Recall@1 的宏平均，因此数值与 macro Recall@1 相同。

本轮不允许通过训练 projector、fusion、selector 或语言模型掩盖 S1 margin 失败。下一步只可
审查弱标签原型污染、Speech 多标签占比、类别不平衡和冻结 BEATs 表示空间；若要提出新的 S1
表示，必须先形成新的预注册合同和独立数据边界。正式 test 仍不得访问。
