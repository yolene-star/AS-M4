# Query-conditioned 音频计划书开源架构 Review

日期：2026-08-30（Asia/Shanghai）

## 总体结论

判定：**结构思路值得参考，但当前计划不宜直接作为 P3 主线实施**。

可以借鉴视频 selector 的工程形式：音频窗口和 query 分别投影、归一化后计算窗口级分数，并保持
因果 Top-K、mask 和显式状态。但“冻结 Qwen2 contextual hidden state 就能成为可靠音频 query”
这一核心假设没有被现有开源架构或本地实验支持，甚至与已有 R2 失败证据相冲突。

## 开源架构核查

### 1. M4 视频 query-conditioned selector

本地 M4 的 `QuestionConditionedWindowSelector` 是一个**通用的可训练双投影打分器**：

```text
audio_features -> audio_projection -> L2 normalize
question_features -> question_projection -> L2 normalize
dot product -> causal Top-K
```

它本身不提供预训练的音频-文本语义对齐；query 的语义质量完全由输入 query embedding 和
selector 训练监督决定。

### 2. M4 实际 question 输入

`llava_arch.py` 在 `question_conditioned` 路径仍使用
`_pool_question_text_features(embed_tokens, ...)`，即 Qwen2 输入 embedding 的 mask mean pool。
这与当前 P3 raw-question 路径相同，不是经过 Qwen2 transformer 的 contextual sentence embedding。
因此视频 selector 的代码形式可复用，但不能把它当成“已经验证的问题-音频共同空间”。

### 3. 既有 selector 研究边界

仓库中的 `DYNAMIC_WINDOW_SELECTOR_DIAGNOSTIC_TRAINING_PLAN_V2_ZH.md` 明确限制：

- learned selector 的 query 只来自注册的 `event_label` 模板；
- 结论只能称为 `event-label query-conditioned ranking`，不能伪装成开放域 QA；
- selector-only 训练使用冻结逐窗口音频缓存和 ranking loss，不使用 LM loss；
- 必须通过 Oracle headroom、Audio+Question 对照、shuffled/empty query 和 cluster bootstrap 门禁。

这说明开源/本地可验证路线是“受限事件 query selector”，而不是直接把开放域 Qwen2 问题表示接到
音频特征上。

### 4. 既有音频语义诊断证据

`AUDIO_SEMANTIC_RECONSTRUCTION_R0_R2_EXECUTION_REVIEW_ZH.md` 已在 R2 证明：raw BEATs/当前
scene-audio projector 保有事件类别结构，但与 Qwen 文本目标空间不对齐；合法文本目标的 rank-first
仅约 5.1%–8.7%，全部未过门禁。该证据否定“换成 Qwen2 contextual pooling 即自然获得音频语义对齐”，
除非先做独立 A/B 证明。

## 对当前计划书的判断

### 值得保留

- audio/query 独立 projection + cosine 的 scorer 形式；
- selector 与最终 residual Gate 分离；
- causal/future-invariance、Top-K、mask、pair-group 和 versioned manifest；
- 2-step -> 20-step 的 fail-closed 门禁；
- 不解冻 BEATs、Qwen2 主体和视觉塔。

### 必须降级或删除的假设

- 不得宣称 Qwen2 contextual hidden state 已是音频共同语义空间；
- 不得把开放域 QA relevance 作为第一验证目标；
- 不得在没有 Oracle/事件标签上界的情况下重算 5,400 条特征；
- 不得把 projection 训练参数混入冻结输入特征 SHA；
- 不得直接复用旧 P3 relevance 门槛来证明新 selector 有效。

## 建议的可行路线

### 路线 A：低成本、最值得先做

先做 event-label query selector 诊断：

1. 使用 UnAV/AVE 已有 event label 模板，不使用开放域问题；
2. 冻结 raw BEATs 逐窗口特征；
3. 只训练 selector 内部 audio/query projection；
4. 采用同视频 positive/negative ranking loss；
5. 对照 Random、Audio-only、Question-only、shuffled-query、empty-query 和 Oracle；
6. 只有 Audio+Question 明显优于对照，才考虑把 selector 接回 P3 Gate。

这条路线与仓库已有 selector 计划同构，且不依赖 Qwen2 文本空间已经对齐。

### 路线 B：若坚持开放域 QA relevance

必须先做小规模 query encoder A/B，而不是直接全量：

- raw token mean；
- Qwen2 contextual pooling；
- 明确的音频-文本预训练 encoder（如已有合法、可审计的 shared-space teacher）。

在 300 个 correct/mismatch 对上比较可分性、shuffled-query 差异和 bootstrap CI。任何 query encoder
都必须先证明相对 raw 基线有稳定增益，才允许生成 5,400 条新特征。

## 最终建议

当前计划书应改为“**受限 event-label selector 先行，开放域 contextual query 作为候选 A/B**”。
不建议马上实现并重新生成 P3 全量特征，更不建议在现有 Qwen2 contextual 假设下继续训练 Gate。

通过条件应是：

```text
event-label Oracle headroom 通过
-> Audio+Question selector 显著优于对照
-> query encoder A/B 有独立正证据
-> 再决定是否接入开放域 P3 relevance Gate
```

在这些条件满足前，当前计划保持设计评审状态，不授权 GPU、特征重算或训练。
