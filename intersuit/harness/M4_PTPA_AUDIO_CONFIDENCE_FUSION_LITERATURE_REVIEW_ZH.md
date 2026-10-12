# M4/PTPA 音频置信度融合论文与开源项目调研

日期：2026-08-29（Asia/Shanghai）

## 1. 调研目标

围绕当前 `M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1` 的第一阶段需求，筛选：

- 顶会或顶刊正式论文；
- 作者或实验室公开的官方 GitHub；
- 对低质量、噪声、缺失或不可靠模态有动态融合机制；
- 能提供问题条件下的音视频相关性建模；
- 能以小模块接入当前冻结 BEATs + M4 残差路径，而不要求重建整个模型。

本次只做在线只读调研，没有下载代码、安装依赖、修改模型或运行论文项目。

## 2. 核心结论

没有一篇论文同时完整覆盖“音频声学质量 + 问题相关性 + 流式 MLLM 残差融合”。
最适合当前项目的路线是组合两个研究方向：

```text
QMF / PDF
    -> 学习质量、置信度、相对可靠性与校准方法

QA-TIGER / MUSIC-AVQA
    -> 学习问题条件下的音视频相关性与任务路由

当前 M4/BEATs
    -> 保留生成式主干、逐窗口音频表示、残差融合与 gate0 回退
```

优先组合建议：

1. 以 QMF 的低质量模态动态加权思想构造 `QualityGate`；
2. 以 QA-TIGER 的 question-aware fusion 构造 `QuestionRouter/RelevanceGate`；
3. 以 PDF 的相对置信校准作为第二版训练正则；
4. 以 MUSIC-AVQA 的问题类型和音视频对照设计评测集；
5. 首轮不引入时间 Gaussian experts、证据理论或 test-time adaptation。

## 3. 第一优先级项目

### 3.1 QMF：低质量模态动态融合

- 论文：**Provable Dynamic Fusion for Low-Quality Multimodal Data**
- 会议：ICML 2023
- 官方论文：[PMLR](https://proceedings.mlr.press/v202/zhang23ar.html)
- 官方代码：[QingyangZhang/QMF](https://github.com/QingyangZhang/QMF)
- 仓库许可：MIT

论文直接研究低质量多模态数据，使用不确定性估计动态决定各模态权重，并报告分类准确率和
鲁棒性提升。官方仓库包含 text-image 与 RGB-D 两类任务、噪声等级实验、训练脚本和预训练模型。

与当前项目的对应关系：

```text
QMF 的 modality quality / uncertainty
    -> AS-M4 的逐窗口 g_quality

QMF 的噪声等级实验
    -> clean / noisy / silence / clipping 对照

QMF 的动态融合权重
    -> 当前固定 gate=0.005 的替代基线
```

最值得学习：

- 如何把“质量”变成可训练的动态权重；
- 如何构造逐级噪声实验；
- 如何报告 clean 与 corruption 条件下的共同收益；
- 如何防止低质量模态拖累联合预测。

不能直接照搬：

- 原项目是分类 late fusion，不是生成式 AVQA；
- 原权重主要是样本级/模态级，而当前需要逐音频窗口 gate；
- QMF 的分类置信度不能直接等价为 Qwen2 生成置信度；
- 不能把 QMF 的整个分类头接到 M4 后面替代语言模型目标。

适配评价：**5/5，最适合 QualityGate 的方法起点。**

### 3.2 QA-TIGER：问题条件下的音视频融合

- 论文：**Question-Aware Gaussian Experts for Audio-Visual Question Answering**
- 会议：CVPR 2025 Highlight
- 官方论文：[CVF Open Access](https://openaccess.thecvf.com/content/CVPR2025/html/Kim_Question-Aware_Gaussian_Experts_for_Audio-Visual_Question_Answering_CVPR_2025_paper.html)
- 官方代码：[AIM-SKKU/QA-TIGER](https://github.com/AIM-SKKU/QA-TIGER)
- 官方项目页：[QA-TIGER Project](https://aim-skku.github.io/QA-TIGER/)

QA-TIGER 显式把问题信息注入音频和视觉表示，并通过 question-aware fusion 和 Gaussian
experts 选择与问题相关的连续或非连续时间片段。论文与项目页报告在多个 AVQA benchmark
上取得一致的先进结果。官方仓库使用 Python 3.10、PyTorch 2.4，提供配置、训练/测试脚本和 checkpoint，
与本机 PyTorch 2.5.1 的技术代际接近。

与当前项目的对应关系：

```text
QA-TIGER 的 question-aware fusion
    -> AS-M4 的 QuestionRouter + RelevanceGate

问题显式注入 audio/video
    -> 解决当前 fixed residual 不读取问题特征的问题

逐时间片 question relevance
    -> AS-M4 的逐窗口 g_relevance(t)
```

最值得学习：

- 问题特征如何在最终答案头之前进入音视频融合；
- 如何同时使用句子级和词级问题特征；
- 如何可视化不同问题对音频、视频时间片的关注；
- 如何按 Audio-QA、Visual-QA、Audio-Visual-QA 分层评测。

首轮不建议照搬：

- Gaussian temporal experts 同时改变时间选择，会混入第二阶段“错位/对齐”变量；
- 完整 MoE 比当前小型 Gate 更重；
- 使用 VGGish/CLIP 的输入空间与当前 BEATs/M4 不同；
- 仓库页面未明确显示可复用许可证，复制代码前必须单独核对许可。

适配评价：**5/5，最适合 QuestionRouter/RelevanceGate 的结构参考。**

## 4. 第二优先级项目

### 4.1 PDF：相对置信度与动态校准

- 论文：**Predictive Dynamic Fusion**
- 会议：ICML 2024
- 官方论文：[PMLR](https://proceedings.mlr.press/v235/cao24c.html)
- 官方代码：[Yinan-Xia/PDF](https://github.com/Yinan-Xia/PDF)
- 仓库许可：Apache-2.0

PDF 使用 Mono-Confidence、Holo-Confidence 和 Collaborative Belief，并用相对校准策略处理
潜在不确定性。论文报告多个 benchmark 上优于对比方法。

对当前项目最有价值的是“置信度必须相对其他模态校准”，而不是孤立地预测一个音频分数。
例如，同一个中等质量音频在纯视觉问题中应低权重，在强音频问题且视觉证据不足时可以更高。

建议借鉴：

- correct/mismatch/noisy 成组样本上的相对排序正则；
- 音频相对于问题和视频的可靠性校准；
- 防止单个 confidence predictor 产生过度自信。

局限：官方环境是 PyTorch 1.11/CUDA 11.3，且仍是分类任务；README 还提示小 batch 下 confidence
预测可能导致 `log` 数值不稳定。因此更适合在 QMF 式第一版通过后引入训练思想，不建议首轮移植全部实现。

适配评价：**4/5，适合作为第二版校准损失。**

### 4.2 MUSIC-AVQA / AVST：直接的音视频问答基线

- 论文：**Learning to Answer Questions in Dynamic Audio-Visual Scenarios**
- 会议：CVPR 2022 Oral
- 官方论文：[CVF Open Access](https://openaccess.thecvf.com/content/CVPR2022/html/Li_Learning_To_Answer_Questions_in_Dynamic_Audio-Visual_Scenarios_CVPR_2022_paper.html)
- 官方代码：[GeWu-Lab/MUSIC-AVQA](https://github.com/GeWu-Lab/MUSIC-AVQA)
- 官方项目页：[MUSIC-AVQA](https://gewu-lab.github.io/MUSIC-AVQA/)

该项目提供约 4.5 万 QA、9288 个视频、33 种问题模板，以及音频、视觉、音视频三类问题场景。
论文的时空 grounding 网络优于其对比的音频、视觉和音视频 QA 方法，官方仓库提供训练模型、
特征提取与可视化。

对当前项目最有价值的不是旧网络本身，而是：

- Audio-QA / Visual-QA / AV-QA 的任务划分；
- 问题引导音频和视觉上下文的基本实现；
- sounding object 的时空 grounding 可视化；
- 用问题类型检验 Gate 是否真正具备 task routing。

局限：环境是 Python 3.6、PyTorch 1.6，并依赖旧 TensorFlow VGGish；不应安装到当前 `M4`
环境或直接替换 BEATs。仓库页面对许可证的展示存在不一致，复制实现前需再次核对。

适配评价：**4/5，最适合作为任务设计和评测参考。**

## 5. 第三优先级与理论参考

### 5.1 Multimodal Dynamics

- 论文：**Multimodal Dynamics: Dynamical Fusion for Trustworthy Multimodal Classification**
- 会议：CVPR 2022
- 官方论文：[CVF Open Access](https://openaccess.thecvf.com/content/CVPR2022/html/Han_Multimodal_Dynamics_Dynamical_Fusion_for_Trustworthy_Multimodal_Classification_CVPR_2022_paper.html)
- 官方代码：[TencentAILabHealthcare/mmdynamics](https://github.com/TencentAILabHealthcare/mmdynamics)

该方法同时估计 feature-level 与 modality-level informativeness，并用稀疏 gate 做动态融合。
论文报告在多模态医学分类任务中取得更好的性能与可信性。

可学习点：逐特征与逐模态分层 gate、稀疏性、可解释权重。局限是领域为多组学/医学分类，
官方代码仅 5 次提交、PyTorch 1.7，且声明 all rights reserved。因此只建议阅读结构和实验，
不要复制代码进入当前项目。

适配评价：**3/5，适合学习“窗口 gate + 全局 gate”的分层设计。**

### 5.2 TMC / ETMC：证据不确定性融合

- 论文：**Trusted Multi-View Classification**，ICLR 2021
- 扩展：**Trusted Multi-View Classification with Dynamic Evidential Fusion**，TPAMI 2022
- 官方代码：[Han-Zongbo/TMC](https://github.com/Han-Zongbo/TMC)
- ICLR 论文：[OpenReview](https://openreview.net/forum?id=OOsR8BzCnl5)

TMC 把各模态分类输出转为 Dirichlet evidence，计算 belief 与 uncertainty，再通过证据组合实现可信融合。
官方仓库提供完整公式、损失和 demo。

可学习点：不确定性监督、校准指标、冲突模态分析。局限是强依赖固定类别分类头和
Dempster-Shafer 组合，无法直接用于自由生成 Qwen2。若当前项目后续增加“该问题是否需要音频”
二分类辅助头，可把 TMC 作为校准对照，但不建议首版作为主 Gate。

适配评价：**2.5/5，适合作为 uncertainty baseline。**

## 6. 暂不作为首选的项目

### AVQACL（CVPR 2025）

[论文](https://openaccess.thecvf.com/content/CVPR2025/html/Wu_AVQACL_A_Novel_Benchmark_for_Audio-Visual_Question_Answering_Continual_Learning_CVPR_2025_paper.html)
和[官方代码](https://github.com/kx-wu/AVQACL)包含 question-guided cross-modal information fusion，
但主问题是持续学习与灾难性遗忘。可参考 QCIF，不值得为当前 Gate 引入完整 continual-learning 框架。

### READ（ICLR 2024）

[论文](https://proceedings.iclr.cc/paper_files/paper/2024/hash/f25d75fc760aec0a6174f9f5d9da59b8-Abstract-Conference.html)
和[官方代码](https://github.com/XLearning-SCU/2024-ICLR-READ)研究 test-time reliability bias，
通过自适应模态注意力和测试时更新提高噪声条件鲁棒性。方向相关，但当前项目强调冻结、可回退和
先验证 Gate；测试时修改模型会引入额外漂移，适合作为后续部署研究，不适合作为第一版。

### Missing-AVQA（ECCV 2024）

[官方代码](https://github.com/VisualAIKHU/Missing-AVQA)研究音频或视觉完全缺失时的 AVQA，
使用 relation-aware generator 和 diffusion 恢复缺失模态。对鲁棒性评测有价值，但结构较重，
而当前问题是低质量/无关音频的选择性融合，不是重建完全缺失模态。

## 7. 面向当前代码的落地映射

建议只提取思想和小模块，不把外部仓库直接合并到项目：

| 当前模块 | 首选论文来源 | 建议借鉴内容 |
|---|---|---|
| `QuestionRouter` | QA-TIGER | 问题显式注入、Audio/Visual/AV 问题分层 |
| `RelevanceGate` | QA-TIGER + MUSIC-AVQA | 逐窗口问题相关性、问题类型对照 |
| `QualityGate` | QMF | 低质量模态动态权重、噪声实验 |
| Gate 排序/校准 | PDF | 相对可靠性、Mono/Holo confidence 思想 |
| 分层 Gate | Multimodal Dynamics | feature/window 与 modality/global 两层 informativeness |
| 不确定性基线 | TMC | Dirichlet evidence、校准与冲突诊断 |

建议的第一版仍保持：

```text
冻结 BEATs + 冻结 M4/Qwen2 + 冻结视觉塔
-> 只训练 QuestionRouter、QualityGate、RelevanceGate
-> offset 不参与窗口移动
-> 保留 gate0 精确回退和 residual ratio cap
```

## 8. 推荐阅读顺序

1. **先读 QMF**：理解为何低质量模态需要动态加权，以及如何做噪声鲁棒性实验；
2. **再读 QA-TIGER**：理解问题特征应如何提前进入音频和视觉融合；
3. **运行级阅读 MUSIC-AVQA**：重点看问题类型、数据格式、AVST 和可视化；
4. **再读 PDF**：为第一版 Gate 增加相对校准与排序正则；
5. **最后读 Multimodal Dynamics/TMC**：补充分层 gate 和证据不确定性理论。

如果只选两个项目深入学习，应选择：

```text
QMF       -> “这段音频可靠吗？”
QA-TIGER  -> “这段音频与当前问题有关吗？”
```

二者组合最接近当前第一阶段目标，同时不要求提前解决音视频错位。

## 9. 工程与许可注意事项

- 不在当前 `M4` Conda 环境直接安装这些项目的 requirements；
- 不创建新 Conda 环境，除非用户明确授权；
- 先只读审计模块源码，再用当前项目风格独立重写小型 Gate；
- QMF 为 MIT、PDF 为 Apache-2.0；其余项目复制代码前必须再次核对许可证；
- 不下载外部 checkpoint 或数据，除非后续明确授权网络、目标目录和数据用途；
- 不把外部项目的 test 指标直接当作当前 M4 可获得的收益；
- 所有收益必须在当前 correct/mismatch/noisy/silence/visual-question 合同中重新验证。

## 10. 最终建议

当前无需继续扩大论文列表。最有效的下一步是对 QMF 和 QA-TIGER 做源码级定向阅读：

1. 从 QMF 提取 confidence predictor、动态权重和噪声训练逻辑；
2. 从 QA-TIGER 提取 question-aware audio fusion 的最小计算图；
3. 将两者压缩为适配当前 `3584` 隐空间或低维投影空间的小型 CPU Gate 合同；
4. 先用冻结特征验证排序关系，再决定是否接入真实 7B。

该组合比移植某个完整论文模型更符合当前“先解决融合、后处理错位”的推进顺序。
