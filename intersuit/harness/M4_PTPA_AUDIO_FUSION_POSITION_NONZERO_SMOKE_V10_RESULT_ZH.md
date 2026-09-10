# S3 融合位置非零 Gate 十条样本结果

日期：2026-08-30（Asia/Shanghai）

## 结果

固定同一 M4 基座、v4 音频表示、gate 预算和 10 条 dev 样本：

| 路径 | correct 答对 | mismatch 答对 | 输出变化 | 空输出/EOS |
|---|---:|---:|---:|---:|
| visual token residual | 3/10 | 3/10 | 与 behavior 基线无新增变化 | 0/0 |
| independent audio tokens | 2/10 | 2/10 | 5/10 对 correct/mismatch 产生差异 | 0/0 |

audio-token 输入长度随样本增加约 20 个 token（音频窗口数），未出现非有限值、空输出或首 token EOS。

## 判定

独立 audio-token 位置比视觉 residual 更能让音频影响生成，但目前没有 correct > mismatch 的选择性，
也没有总体答对率提升。该结果不授权训练、late cross-attention 或 formal test；当前只证明位置可见性，
不证明位置收益。

## 下一步

暂停扩大 audio-token 样本。优先分析音频 token 的语义承载方式（token 数、归一化、位置和模态标识）
以及 M4 对连续音频 token 的训练覆盖；如需继续，应另立小规模 token 设计合同，保持 video-only 和
旧 residual 作为回退基线。
