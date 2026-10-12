# M4/PTPA 音频置信度融合 P3 训练前门禁报告

## 结论

P3 训练前门禁状态为 `PASS_P3_PREFLIGHT_TRAINING_CLOSED`。P2 虚拟波形执行器、真实 MP4
解码 smoke、Gate-only 精确参数白名单、optimizer 审计和 `2 -> 20 -> 150` 停止合同已经建立；
但冻结特征预计算、Gate loss/collator 和 gate-only checkpoint 保存尚未实现，因此训练权限继续为
false，本阶段没有启动 GPU 或模型训练。

## 虚拟扰动加载器

新增 `valor32k_confidence.py`，只消费 SHA 锁定的 P2 manifest：

- 使用系统 `ffmpeg` 解码 MP4 为 float32；当前 Conda `M4` 的 `torchaudio` 没有可处理 MP4 的
  backend，因此不依赖它的隐式解码选择；
- clean、mismatch、background noise 统一转换为目标采样率、声道、时长和窗口数；
- white/pink noise 使用 manifest seed 确定性生成；
- same-dataset-background 使用锁定的跨视频媒体及 SHA；
- 按目标 RMS 精确施加 20/10/0 dB SNR；
- silence 与目标波形逐元素同形且全零；
- shifted 默认硬拒绝，只有显式 `allow_second_stage=True` 才能读取；
- 相对路径必须位于媒体根目录内，源 SHA 不匹配时 fail closed；
- dataset 初始化时强制核对 P2 manifest SHA，并只保留 5,400 条首轮记录。

真实 MP4 smoke 结果：clean 输出形状 `(2, 443073)`，white 10 dB 实测 `10.0 dB`，silence
非零元素数为 0，同一 noisy 条件重复生成逐元素一致。

P2 manifest 为支持实际执行器新增目标采样率、声道、时长和窗口数字段，重新生成并再次通过独立
审计；新 SHA-256 为
`007d5b1d936323d28adee252ce6f407d458d24b3042b1caf1bb8a0086f301e26`。

## Gate-only 参数边界

`StreamingAVModule` 新增 dormant `first_stage_confidence_gate`。它放在全部既有模块初始化之后，
默认 forward 不读取它，因此不改变旧路径；旧 state dict 使用 `strict=False` 时只缺新 Gate 参数，
无 unexpected key，既有 fusion 初始化顺序测试保持一致。

新增严格 `MM_TUNABLE_PARTS=audio_confidence_gate` 别名。它只允许：

- QuestionAudioRouter；
- audio/video/question 三个低维投影；
- Quality MLP；
- Relevance MLP。

在 hidden size 3584、projection dim 128 下，白名单为 24 个参数 tensor、1,910,671 个参数，
fp32 原始参数约 7.29 MiB。BEATs、scene audio encoder/projector、M4/Qwen2、视觉塔、旧 Gate、
fusion、aligner、event detector 和 SyncGate 全部冻结。optimizer 必须与白名单逐名字精确一致；
任何多余、缺失或重复参数都立即报错。

## Canary 合同

合同固定物理 GPU2 单卡、Conda `M4`、普通单卡训练、micro batch 1、gradient accumulation 16，
按 `2 -> 20 -> 150` 推进：

- 2-step：loss/gradient/gate finite，只允许 Gate 参数变化，Sync loss 必须不存在；
- 20-step：correct/mismatch 排序准确率至少 0.55，Router balanced accuracy 至少 0.60，Gate
  不得全零、全一或常数；
- 150-step：排序准确率至少 0.60，Router balanced accuracy 至少 0.70，clean/noisy 单调准确率
  至少 0.60，visual-question 平均 Gate 必须低于 audio-question。

任一 nonfinite、冻结权重漂移、optimizer 越界、shift 混入、Sync loss 非零或 Gate 塌缩都停止。

## 尚未授权的部分

当前合同只允许 CPU loader 和白名单测试。以下均为 false：

- 冻结 BEATs/视频/问题特征预计算；
- 2-step、20-step、150-step 训练；
- P4 冻结 7B 验证；
- formal test。

下一步实现冻结特征包 schema、Gate 三类 loss 与 collator、gate-only checkpoint 保存和恢复测试。
这些 CPU/离线合同通过后，再单独评审是否把 `two_step_training` 改为 true。
