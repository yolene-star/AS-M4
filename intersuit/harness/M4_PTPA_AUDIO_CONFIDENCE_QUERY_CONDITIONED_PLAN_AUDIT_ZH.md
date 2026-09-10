# 音频问题条件化计划书审计结论

日期：2026-08-30（Asia/Shanghai）

## 总体判定

判定为“方向通过、带条件修改后才能实施”。query-conditioned selector 能借鉴视频模块的
投影与 cosine 机制，但当前计划存在三个接口/资产边界问题，必须先修正；在修正前不应开始
代码实现或重新生成 5,400 条特征。

## 已确认的优点

- 采用音频/问题独立 projection 后再做归一化相似度，结构上与现有视频 selector 一致；
- 保留 `g_sync=1`、Gate-only 冻结骨干、2-step -> 20-step 门禁和 versioned 资产隔离；
- 明确要求 contextual question feature，并禁止继续把 raw token mean 当正式 relevance 资产；
- 计划保留 selector 与最终 Gate 的职责分离，便于归因和回退。

## 必须修改的三点

### 1. 视频 selector 目前也使用 raw question feature

`llava_arch.py` 在 `window_mode == "question_conditioned"` 时调用
`_pool_question_text_features(embed_tokens, ...)`，与 P3 当前问题相同。因此不能把“仿照视频
selector”表述为已有语义空间已验证；只能复用其接口形式。计划应明确：先做 contextual query
feature 的离线 A/B，对视频 selector 和音频 Gate 分别记录 raw/contextual 两种结果。

### 2. 冻结特征与可训练 projection 的 SHA 边界冲突

计划第 6 节要求将 audio/video/query projection source SHA 写入冻结特征 manifest，但第 5 节又
把这些 projection 列为 P3 可训练参数。可训练权重不能作为冻结特征资产的固定来源，否则每次
Gate 更新都需要重新生成 5,400 条特征，破坏 Gate-only 训练效率和 checkpoint 复现。

修正为：manifest 只锁定冻结 encoder（BEATs、视觉塔、Qwen2 contextual query encoder）的
source SHA；projection 的结构版本、初始化 seed 和参数白名单写入训练合同/checkpoint metadata，
不写入冻结输入特征的 encoder SHA。

### 3. Selector 尚未接入现有 P3 训练入口

当前 `train_audio_confidence_gate_p3.py` 只实例化 `FirstStageAudioConfidenceGate`，并消费
`FrozenFeatureManifestDataset` 的 audio/video/question 特征；`QuestionConditionedWindowSelector`
尚未进入训练 forward、loss 或 checkpoint。计划必须先决定：

- selector 作为 Gate 内部新的 relevance 子模块；或
- selector 独立输出 Top-K/score，再由 Gate 消费其结果。

无论选择哪种，都要新增参数白名单、loss 输入、checkpoint schema 和 CPU 合同测试，不能只新增
一个离线 selector 类而宣称计划完成。

## 额外门禁建议

- contextual query encoder 必须定义 transformer 层范围、pooling、attention mask 和 dtype；
- 必须用同一批 300 个 correct/mismatch 对比较 raw/contextual 的可分性，contextual 版本至少
  不得低于 raw 基线；
- 新 manifest schema/version 必须与旧 `frozen_features_v1` 隔离；
- 先做单样本 CPU/单卡 query encoder smoke，再做全量特征生成；
- 不得把视频 selector 当前 raw-question 行为当作通过依据。

## 审计后的执行顺序

1. 修订计划书并锁定 contextual query encoder 与 projection 的资产边界；
2. 实现 query encoder/selector 的 CPU 合同和训练入口接线；
3. 用 300 对数据完成 raw/contextual 可分性 A/B；
4. 通过后再重算 5,400 条冻结特征；
5. 重新执行 2-step、20-step 指标门禁。

在上述条件完成前，当前计划不能直接授权 GPU 或训练。
