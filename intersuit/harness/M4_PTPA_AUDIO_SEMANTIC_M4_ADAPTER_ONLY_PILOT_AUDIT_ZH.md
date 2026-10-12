# M4 adapter-only 小试训练前审计

日期：2026-08-31（Asia/Shanghai）

## 结果

- Manifest 审计：`PASS_ADAPTER_PILOT_MANIFEST_AUDIT`；
- 记录数：120；pair groups：30；唯一音频文件：33；
- correct/mismatch 音频来源 SHA 均匹配且不相同；问答字段严格一致；video-only 无音频；
- silence 物化规则：复制目标波形形状后置零，尚未执行物化；
- adapter-only 参数白名单 CPU 回归：`4 passed, 2 warnings`。

## 关键边界

当前 M4 正式训练入口面向 M4-IT 对话数据，不能直接读取本 pilot 的四条件反事实 manifest，
也不会自动构造 `scene_audio`、silence waveform、correct/mismatch 对照和 adapter-only LM loss。
因此本审计不授权 2-step GPU 训练。

## 下一步

实现专用 pilot loader/training entry：

1. 读取 120 条 manifest 并物化四条件音频；
2. 加载并冻结 UnAV v2 projector 与 M4/Qwen2/视觉塔；
3. 只挂载 768→3584 adapter；
4. 显式计算 correct/mismatch LM 对比和 visual-only 抑制；
5. 先做 CPU 单步有限性与冻结参数审计，再单独评审物理 GPU2 的 2-step。
