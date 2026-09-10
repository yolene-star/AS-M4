# S3 音频融合位置诊断阶段 Review

日期：2026-08-30（Asia/Shanghai）

## 当前结论

S3 的“融合位置优先”方向成立，但目前只完成了视觉 token residual 路径内部的 fusion projector
对照，尚未实现独立 audio token 或 late cross-attention，因此不能宣称三种融合位置已经比较。

## 已完成证据

- CPU fusion contract：`PASS_CPU_FUSION_MODE_COMPARISON`；
- bypass/identity/lightweight 均保持 correct/mismatch 表示差异（相对差异约 `0.632`）；
- 旧 fusion reference 将差异压为余弦 `1.0`、相对差异 `0`；
- 所有候选 gate0 均精确零回退；
- 真实 7B nonzero 小样本中，bypass 与 lightweight 都没有带来正确音频收益，lightweight 反而对 mismatch 产生更多扰动。

## 尚未完成

- `llm_input_audio_tokens` 尚未接入 `_pack_image_features_into_inputs`/generation path；
- `late_cross_attention` 尚未有模块、参数白名单或 KV-cache 兼容合同；
- 因此当前证据只能定位“旧视觉残差 fusion 是瓶颈之一”，不能选择最终融合位置。

## 建议顺序

1. 先实现无训练的独立 audio-token 注入，仅增加固定数量、带显式 mask 的音频 token；
2. 在单样本 CPU/7B gate0 smoke 验证序列长度、position ids、streaming KV-cache 和精确回退；
3. 再做最多 10 条的 behavior/correct/mismatch/silence 对照；
4. 只有独立 audio token 显示 correct 优于 mismatch，才设计 late cross-attention；
5. 任一位置失败都保留失败证据，不通过解冻大模型或放大 gate 挽救。

## 资源与边界

本阶段不训练、不访问 formal test、不重算 P3 冻结特征，不覆盖任何 M4/AS-M4 checkpoint。
