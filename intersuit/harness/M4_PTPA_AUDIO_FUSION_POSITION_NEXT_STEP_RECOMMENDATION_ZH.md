# S3 融合位置下一步建议

## 资产判断

- UnAV S1-R temporal projector v2 已有独立评估：macro Recall@1 `0.62795`，positive-minus-hard-negative margin `0.02133`，correct>mismatch 三组通过，未访问 test；
- AVE v4 active projector 也通过纯音频表示 CI 与 gate0 接口，但进入 M4 后旧 fusion 会压平差异；
- CLAP/teacher 目前只有资格审查与候选锁，不应直接当作已授权学生目标或接入正式 M4。

## 决策

下一步不再开发 contextual Qwen2 query encoder，而是固定一个已通过纯音频门禁的表示（优先 temporal projector v2 或 AVE v4，二者分开），只比较融合位置。

## 最小实验矩阵

使用同一 M4 checkpoint、同一音频表示、同一 10 条 dev 配对：

1. `visual_token_residual`；
2. `llm_input_audio_tokens`；
3. `gate0`/behavior-off 回退对照。

每种位置只改一个变量，记录 correct/mismatch 答对率、输出变化率、首 token、空输出/EOS、重复文本、输入长度、残差比例和冻结模块 SHA。

## 晋级条件

- audio-token 或 residual 至少一个位置满足 correct > mismatch；
- correct 不低于 video-only；
- 空输出/EOS/重复文本不增加；
- gate0 与 behavior-off 精确一致；
- 物理视频级配对 bootstrap 方向为正。

任一位置失败都停止该位置，不通过放大 gate、解冻 Qwen2 或增加训练步数挽救。只有位置上界通过后，才讨论小规模 adapter 训练。
