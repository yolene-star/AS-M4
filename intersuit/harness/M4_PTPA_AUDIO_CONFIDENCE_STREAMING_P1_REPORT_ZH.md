# M4/PTPA 音频置信度流式融合 P0/P1 报告

日期：2026-08-29（Asia/Shanghai）

## 1. 本阶段范围

本阶段只完成置信度融合的 P0 合同冻结和 P1 CPU 原型，不接入正式 7B 推理路径，不训练，
不使用 GPU，也不处理音视频错位。

第一阶段固定：

```text
g_sync = 1
g_audio = g_quality * g_relevance
```

## 2. P0 合同

机器可读合同：

```text
intersuit/harness/configs/audio_confidence_streaming_p0_contract_v1.json
```

合同锁定：

- 只允许 `QuestionRouter + QualityGate + RelevanceGate`；
- BEATs、M4/Qwen2、视觉塔、现有 projector 和 fusion 保持冻结；
- 时间窗口校正关闭；
- 回答使用不可变 memory snapshot；
- 生成期间的新输入进入下一 memory version；
- 已输出 token 不可静默修改；
- 当前阶段不授权 GPU 或训练。

## 3. P1 新增模块

### 3.1 `QuestionAudioRouter`

输入池化问题特征，输出样本级 `task_audio_need`。最后一层零初始化并使用负 bias，初始状态
接近关闭音频，降低未训练模块破坏现有行为的风险。

### 3.2 `FirstStageAudioConfidenceGate`

逐窗口输出：

- `quality`；
- `task_audio_need`；
- `window_relevance`；
- `relevance`；
- 恒等 `sync=1`；
- `raw_gate`；
- 有上限的最终 `gate`。

模块只读取当前窗口音频、当前窗口视频、池化问题和当前窗口声学统计，不在内部保存跨窗口状态，
因此不会通过隐藏的双向时序层读取未来窗口。

静音窗口的 `quality` 和最终 gate 精确为零；`force_audio_gate=0` 也保持精确零回退。

### 3.3 `StreamingConfidenceStateManager`

按 `stream_id` 独立维护：

- `memory_version`；
- `last_timestamp`；
- causal EMA；
- hysteresis active 状态；
- rate-limited output gate。

它支持显式 reset、时间戳单调检查、强制关闭和冻结 snapshot。Snapshot 使用 frozen dataclass，
创建后不会随未来窗口更新。

## 4. 正确性合同

新增测试覆盖：

- 输出 shape、范围和 `sync=1`；
- 静音精确关闭；
- 相关问题高于相反问题；
- future invariance；
- mask 与强制 gate0；
- prefix/chunk boundary equivalence；
- 两条 stream 交错执行仍相互隔离；
- reset 后与全新 stream 一致；
- snapshot immutability；
- force-close 精确回退；
- 单 stream 时间戳倒退 fail-closed；
- 非法配置和空 stream ID fail-closed。

## 5. 验证结果

使用 Conda `M4`：

```text
解释器：/home/yjm/miniconda3/envs/M4/bin/python
定向测试：44 passed, 2 warnings
完整测试：644 passed, 2 warnings
完整测试耗时：101.57 秒
```

两个 warning 均为既有 SWIG 类型的 `DeprecationWarning`，没有新增失败。

## 6. 兼容性

- 未替换旧 `AudioConfidenceGate`；
- 未修改 `StreamingAVModule` builder 的实例化路径；
- 未修改 `llava_arch.py` 的正式融合行为；
- 未修改任何 checkpoint；
- 新类通过 `streaming_av.__init__` 导出；
- 流式状态通过 `streaming.__init__` 导出。

因此当前 checkpoint 行为不因本阶段代码自动改变。

## 7. 尚未证明的内容

本阶段不能证明：

- Gate 能改善最终回答；
- correct 音频任务收益高于 mismatch；
- 音频问题 gate 一定高于视觉问题；
- 首 token 延迟满足实时预算；
- 长流状态在真实服务中稳定；
- 音视频错位得到处理。

这些内容分别属于 P2 数据合同、P3 Gate 训练、P4 冻结 7B 小规模验证和第二阶段 SyncGate。

## 8. 下一步

进入 P2 前先冻结数据配对规则：

```text
correct
mismatch
silence
noisy
visual-question
audio-question
```

时间平移样本只作为第二阶段资产生成和审计，不进入第一阶段训练或模型选择。
