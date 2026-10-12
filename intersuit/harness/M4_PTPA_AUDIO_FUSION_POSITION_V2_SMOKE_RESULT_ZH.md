# S3 使用 UnAV S1-R temporal projector v2 的十条融合位置结果

日期：2026-08-30（Asia/Shanghai）

## 资产

- temporal projector v2：`summary.pt`，SHA `0bf779dbc1678328d935089f8d41c16e216266b694dd86e51415bfb96f92a610`；
- 独立评估：Macro Recall@1 `0.62795`，positive-minus-hard-negative margin `0.02133`；
- M4 adapter：复用并冻结基座已有 768→3584 `audio_projector`；
- 未训练、未访问 formal test。

## 结果

| 路径 | correct 答对 | mismatch 答对 | 输出变化 | 空输出/EOS |
|---|---:|---:|---:|---:|
| visual token residual | 3/10 | 3/10 | 相对 behavior 分别 3/10、1/10 | 0/0 |
| independent audio tokens | 2/10 | 2/10 | 相对 behavior 分别 6/10、5/10 | 0/0 |

## 判定

v2 表示在 residual 路径上比此前 v4 更能让 correct 音频改变输出，但没有提高答对率或形成
correct>mismatch；audio-token 位置同样只有可见性而无选择性。当前不能选择最终融合位置，
不授权训练或扩大评测。

## 下一步

优先停止位置堆叠，转向音频到答案的监督/桥接问题：检查 M4 原训练是否覆盖独立音频 token，
以及 768→3584 adapter 是否需要有监督的任务桥接。任何新 adapter 必须先在冻结 M4 上做
correct/mismatch 小样本和 gate0 回退合同。
