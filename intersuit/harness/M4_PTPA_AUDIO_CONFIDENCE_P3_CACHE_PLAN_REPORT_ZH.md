# P3 冻结特征去重缓存与容量计划报告

日期：2026-08-30（Asia/Shanghai）

## 结论

状态为 `PASS_P3_FEATURE_CACHE_PLAN_AUDIT`。基于 SHA 锁定的 P2 首阶段 manifest，已在
Conda `M4` 中完成不加载模型、不访问 GPU 的全量缓存计划构建和审计。该结果只证明缓存
引用、去重键和容量估算一致，不代表已经生成 5,400 条冻结特征，也不开放 Gate 训练。

## 范围与复用规则

- 输入记录：5,400 条 `first_stage_training=true` 的 P2 记录；
- 音频缓存键包含音频来源媒体 SHA、corruption recipe、seed、采样率、窗口参数和 BEATs
  encoder source SHA；
- 视频缓存键包含物理视频 SHA、窗口参数和视觉 encoder source SHA；
- 问题缓存键包含 question id、文本和 question encoder source SHA；
- 每条训练记录只保存三类 content-addressed key 和监督标签，不复制特征张量。

## 审计结果

```text
record_count: 5400
unique_audio: 4577
unique_video: 150
unique_question: 450
deduplicated_tensor_bytes: 607190304  (~579 MiB)
uncached_tensor_bytes: 1449963072     (~1.35 GiB)
saving_ratio: 0.5812374013343148       (~58.1%)
plan_sha256: 2ce86a505b5d0038266aff2e9ccacd1bd99c773d94abcde06b2667dc2c305b65
```

计划文件：

```text
intersuit/harness/artifacts/audio_confidence_p3_cache_plan_v1/cache_plan.json
```

## 验证

- `test_audio_confidence_cache_plan.py`：`2 passed`；
- `git diff --check`：通过；
- 入口脚本 `build_audio_confidence_p3_cache_plan.py`：实际生成计划并返回
  `PASS_P3_FEATURE_CACHE_PLAN_AUDIT`；
- 未运行 GPU、7B、特征编码、反向传播、推理或 formal test。

## 下一步与边界

缓存计划通过后，可以进入完整特征预计算的授权评审。正式执行仍需保持：物理 GPU2、唯一
输出目录、`overwrite_existing=false`、保存 stdout/stderr、合同快照、开始时间、GPU 前后
状态和独立特征审计；`two_step_training` 必须继续为 false，直到全部特征资产审计通过。
