# P3 Relevance 特征可分性分析

## 结论

当前 relevance 失败的主要原因是问题特征与场景音频/视频特征不在可直接比较的语义空间。冻结特征
预计算和 `llava_arch.py` 都将问题表示定义为原始 token embedding 的平均池化；它不是经过 Qwen2
上下文层或跨模态对齐后的句子表示。Gate 却直接使用 audio-question cosine 作为 relevance logit 的
显式项，因此 correct/mismatch 排序接近随机。

## 300 个严格配对的冻结特征统计

audio 和 audio-visual 各 150 对，共 300 对：

| 指标 | 平均值 | 结论 |
|---|---:|---|
| correct 与 mismatch audio 余弦 | 0.8263 | 音频条件确实不同，但仍相近 |
| correct 与问题余弦 | -0.0130 | 接近零 |
| mismatch 与问题余弦 | -0.0139 | 接近零 |
| correct 问题余弦更高比例 | 0.5233 | 接近随机 |
| correct 视频余弦更高比例 | 0.5167 | 接近随机 |

因此，数据和音频扰动本身不是恒等的；问题在于当前输入表示未提供可用的 audio-question 语义关系。

## 代码证据

- `precompute_audio_confidence_p3_features.py` 的 `encode_question()` 对 `embed_tokens` 输出做平均池化；
- `llava_arch.py::_pool_question_text_features()` 使用同一策略；
- `FirstStageAudioConfidenceGate` 显式将 `aq_cos` 加入 relevance logit。

## 后续修复方向

不要继续提高 relevance loss 权重或训练步数。应改用冻结但语义化的问题表示，例如将问题 token
通过冻结 Qwen2 的若干上下文层得到 pooled hidden state，或使用已验证与 scene-audio projector 处于
同一空间的 query encoder。该修改会改变冻结特征 schema/source SHA，必须重新生成特征资产、运行
独立审计，再从 2-step 重新开始；不得复用当前 v5/v7 的 relevance 结论作为通过依据。
