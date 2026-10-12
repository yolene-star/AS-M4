# S3 融合位置非零 Gate 单样本结果

日期：2026-08-30（Asia/Shanghai）

## 结论

独立 audio-token 位置在非零 gate 下已成功改变 LLM 输入序列并影响生成，但单样本结果不足以证明
正确音频收益。该 smoke 仅用于确认位置接线和可观察性，不授权训练或扩大到 formal test。

## 对照结果

固定同一 M4 基座、同一 v4 音频来源和同一问题：

| 条件 | 融合位置 | 输入长度 | 输出 |
|---|---|---:|---|
| behavior-off | visual residual | 4637 | `The sound event audible in this video is` |
| correct | visual residual | 4637 | `The sound event audible in this video is` |
| mismatch | visual residual | 4637 | `The sound event audible in this video is` |
| correct | independent audio tokens | 4657 | `The sound event audible in this video is is the keyboard being pressed.` |
| mismatch | independent audio tokens | 4657 | `The sound event audible in this video is` |

Audio-token correct/mismatch 均无空输出或首 token EOS；correct 与 mismatch 产生不同生成，说明
该位置确实将音频信息暴露给了 Qwen2。该样本真实答案为 `Clock`，correct 输出仍不正确，不能宣称
任务收益。

## 边界与下一步

- gate0 回退已在前一 smoke 中与 behavior-off 精确一致；
- 本轮未训练、未修改 checkpoint、未访问 formal test；
- 需要扩展到预注册的 10 条 dev 配对，统计 correct/mismatch 的答对率、首 token、空输出/EOS、
  重复文本和输入长度；
- 若 10 条仍无选择性，停止该位置；若出现稳定 correct 优势，再评审 late fusion。
