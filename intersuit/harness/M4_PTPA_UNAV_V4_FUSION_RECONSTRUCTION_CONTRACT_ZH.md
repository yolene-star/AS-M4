# UnAV v4 fusion projector 重构 CPU 合同

## 目的

验证 v4 semantic projector 和冻结 M4 基座之间的最后一层 fusion 是否压平 correct/mismatch 差异。此阶段只做 CPU/小张量合同，不运行 7B、不训练、不提高 gate、不访问 test。

## 允许比较的模式

1. `identity_fusion`：直接保留 3584 维 adapter 表示；
2. `bypass_old_fusion`：绕过旧 `streaming_av_module.fusion.audio_projector`；
3. `lightweight_norm_preserving`：只允许受约束的轻量映射，必须保持有限值和输入尺度。

## 通过标准

- correct/mismatch 的相对差异至少保持 adapter 阶段的有效水平（当前参考下限 `0.5`）；
- 两者余弦相似度不得升至塌缩阈值以上（当前上限 `0.95`）；
- v4、BEATs、M4 基座和旧 checkpoint 全部冻结；
- gate0 仍精确输出零残差；
- 不允许扩大 GPU 评测、访问 formal test 或开始训练。

只有 CPU 合同通过后，才允许在物理 GPU2 做新的单样本 gate0/极小非零 gate smoke。
