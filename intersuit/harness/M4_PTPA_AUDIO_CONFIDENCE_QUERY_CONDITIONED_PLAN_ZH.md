# M4/PTPA 音频问题条件化选择与置信度融合计划书

日期：2026-08-30（Asia/Shanghai）
状态：设计评审阶段，尚未授权实现、特征重算或训练

## 1. 目标

解决当前 P3 relevance 门禁未通过的问题。现有 Gate 将原始 Qwen2 token embedding 的平均池化
直接与冻结 scene-audio/video hidden state 比较，问题特征与音频特征不在可用的语义比较空间，
导致 correct/mismatch 排序接近随机。

本计划借鉴视频模块的 query-conditioned window selector 思路：先把音频窗口和问题分别投影到
专用共同空间，再计算窗口级相关性；同时将问题表示升级为冻结 Qwen2 上下文化表示。

## 2. 范围与非目标

首阶段只改 P3 Gate-only relevance 输入和窗口选择接口：

- 保持 BEATs、视觉塔、M4/Qwen2 主体和旧 fusion 冻结；
- 保留 `g_sync=1`，不处理时间平移；
- 不修改视频帧采样预算；
- 不接入 query speech，不混淆 `query_speech` 与 `scene_audio`；
- 不进行端到端 7B 训练，不访问 formal test；
- 不复用旧 raw-question feature manifest 作为新模型资产。

## 3. 目标结构

```text
冻结 Qwen2 上下文化 question hidden state Q
                         |
                  question_projection
                         |
scene-audio window A_t -> audio_projection -> normalize -> cosine(A_t,Q)
                         |
                 QueryConditionedAudioSelector
                         |
             relevance score / optional Top-K mask
                         |
       QualityGate + RelevanceGate -> g_audio(t)
```

Selector 负责“哪些窗口与问题相关”，Gate 负责“在音频可靠性满足时允许多大影响”。二者不合并，
以便分别审计窗口选择和最终残差门控。

## 4. 问题表示

新增冻结 query encoder 路径：

1. tokenizer 生成问题 token；
2. 使用冻结 Qwen2 transformer context layers 得到 contextual token hidden states；
3. 按 attention mask 做 mean pooling；
4. 保存 context-layer 配置、模型 state SHA 和 pooling 规则。

不得继续使用仅 `embed_tokens` 的 raw mean pooling作为正式 relevance 输入。若显存或速度不允许
完整层数，先固定一个经过 CPU/单样本审计的层数，例如最后 4 层的输出均值，并将层数写入
manifest/source SHA。

## 5. 可训练模块与参数边界

P3 仍只训练新增音频置信度模块：

- audio projection；
- query projection；
- QueryConditionedAudioSelector 的轻量打分层；
- Quality MLP；
- Relevance MLP；
- QuestionAudioRouter。

冻结：BEATs、scene-audio encoder/projector、Qwen2 context encoder、视觉塔、旧 projector、旧
fusion、temporal aligner、event detector 和 SyncGate。optimizer 必须按参数名精确白名单审计。

## 6. 数据与特征资产

必须重新生成 P3 冻结特征：

- 新增 `question_context` 特征；
- audio/video/query projection source SHA 改变后重新锁定；
- 继续使用 5,400 条首阶段记录和原 correct/mismatch/silence/noisy/visual/audio 条件；
- 保持 pair-group 隔离，记录 modality、condition、question id 和 physical video id；
- 新 manifest 必须声明 query encoder 层数、pooling 规则、hidden size、projection dim 和全部来源 SHA。

旧 manifest 与旧 checkpoint 只能作为诊断基线，不能混入新训练。

## 7. 验证门禁

### 7.1 CPU/离线门禁

- query contextual feature shape、finite、mask 和确定性；
- future invariance 与 prefix/chunk equivalence；
- audio/query projection 输出 shape 和归一化；
- selector Top-K、valid mask 和 pair-group 隔离；
- correct/mismatch 特征可分性至少不低于 raw-question 基线；
- Gate-only optimizer 参数白名单和冻结参数 SHA。

### 7.2 GPU canary 顺序

```text
2-step -> 独立 checkpoint 恢复审计
       -> 20-step Router/relevance/Gate 指标
       -> 达标后才评审 150-step
```

20-step 最低门槛保持不变：

- correct/mismatch ranking accuracy >= 0.55；
- Router balanced accuracy >= 0.60；
- Gate 非全零、非全一、非恒定；
- 所有 loss/gradient/output finite；
- sync loss 必须为 0。

未达标时必须分析特征、标签、采样或优化动力学，不得降低阈值或直接进入 150-step。

## 8. 回退与版本隔离

- 行为回退：关闭 query-conditioned selector/relevance gate 时回到原 P3/原 M4 音频路径；
- 权重回退：加载保留的 12k/32k M4 基线或旧 P3 诊断 checkpoint；
- 新 query-conditioned 特征、checkpoint 和报告必须使用新的 versioned 目录；
- 不覆盖现有 `frozen_features_v1`、canary2、canary20-v5/v7 资产。

## 9. 执行顺序

1. 先确认视频 selector 使用的 question feature 类型，避免复制未验证的 raw embedding 缺陷；
2. 实现冻结 Qwen2 contextual query encoder 和 query-conditioned audio selector；
3. 完成 CPU 合同和特征可分性审计；
4. 在物理 GPU2 重新生成 5,400 条特征并独立审计；
5. 执行 2-step，再执行 20-step 指标门禁；
6. 只有 20-step 全部达标后，才评审 150-step 或 P4 7B。

## 10. 当前决策

本计划目前只完成设计，不授权代码实现、特征预计算或训练。授权前必须先完成第 1 项的
视频 selector question feature 来源核查，并确认上下文化 query encoder 的显存和时间预算。
