# UnAV v4 新 fusion projector 真实 7B gate0 结果

2026 年 8 月 29 日在物理 GPU2、Conda `M4` 环境完成单样本三条件 smoke：`behavior-off`、`bypass gate0`、`lightweight gate0`。

三条件预测和生成 token 精确一致，均输出不完整答案 `The sound event audible in this video is`，真实答案为 `Clock`。bypass/lightweight 模式均实际生效，音频已解码，gate 与残差比例精确为 0，冻结模块 SHA 未变化，峰值显存约 24.38 GiB，运行后 GPU 正常释放。

结论：新 fusion projector 路径已通过真实 7B 接口和精确回退门禁，但本结果不证明任务收益。下一步若要验证效果，必须另建并评审极小非零 gate correct/mismatch 合同。
