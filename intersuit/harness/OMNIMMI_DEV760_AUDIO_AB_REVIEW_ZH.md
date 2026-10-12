# OmniMMI / dev760 场景音频接入前后 A/B 测试 Review

日期：2026-08-09（Asia/Shanghai）

## 1. Review 结论

本次测试可以执行，但不能直接把当前最新音频 checkpoint 称为“已验证最优音频模型”。当前状态必须区分：

- 最新代码：HEAD `cb0af7b`，已把场景音频整理为可开关旁路，并包含最新 micro-window / selector v2 代码；
- 最新可加载音频权重：`AS-M4-BEATs-semantic-contrast-canary150-v1/checkpoint-150`；
- 正式资格：该 checkpoint 的 dev760 音频选择性门禁为 FAIL，selector v2 oracle pilot 也已触发 stop gate，只能做诊断性 A/B。

因此推荐采用“双层对比”：

1. 绝对参考：保留用户最近确认的官方原始 M4 PT/PA 最优结果；
2. 纯音频增量：在同一个 AS-M4 checkpoint 上比较 `behavior/gate0` 与正确场景音频开启，隔离音频分支影响。

不能只比较“官方原始 M4 checkpoint”与“AS-M4 音频 checkpoint”，因为两者语言模型/视觉基座来源不同，无法把全部差异归因给音频。

## 2. 冻结的原模型基础配置

### OmniMMI PT

- checkpoint：官方公开 `M4-LongVA-7B-Qwen2`；
- 固定等距 32 帧；
- `gen=False`；
- 检测 token：`151644 = <|im_start|>`；
- softmax temperature：`1.0`；
- beta：`0.2`；
- 现有诊断结果：`126/200 = 63.00%`。

该 63% 是同一批完整 logits 的离线重判，不是一次独立 GPU 重跑。音频 A/B 必须分别采集自己的完整 logits，再使用相同公式判分。

### OmniMMI PA

- checkpoint：官方公开 `M4-LongVA-7B-Qwen2`；
- 完整视频 1 FPS；
- 分辨率 336；
- query 从视频开始阶段注入；
- grounding：最后一层 attention、多头平均、Qwen query 区域倒数第三项；
- 阈值：`mean + 2 * std`；
- 触发：`frame_count > 4` 且 `cnt > 4`；
- 生成：greedy，最多 64 token；
- 现有结果：Accuracy `21.50%`、Precision `31.82%`、IoU `14.02%`。

## 3. 冻结的音频分支诊断配置

- checkpoint：`intersuit/checkpoints/AS-M4-BEATs-semantic-contrast-canary150-v1/checkpoint-150`；
- BEATs：`intersuit/checkpoints/BEATs_iter3_plus_AS2M.pt`；
- BEATs SHA256：`d43cbfad4d7b56381c061d7a24774f908d4d94c72961f6eb1d9090ff18cd8d34`；
- window mode：`micro_window`；
- window / hop：`1.0s / 1.0s`；
- top-k：`8`；
- fusion：`beats_simple_residual`；
- inference gate：`0.005`；
- residual scale：`1.0`；
- delta ratio cap：`0.0`；
- selector learned training：禁止；
- 测试性质：诊断性，不是正式主线资格测试。

已知 dev760 结果：video-only `254/760`、correct `251/760`、mismatched `259/760`。因此本轮目标是准确测量当前最新接线对 OmniMMI/dev760 的影响，不预设音频一定带来提升。

## 4. 代码 Review

### dev760

`run_predictions_from_plan.py` 已能：

- 加载视频特征和场景音频；
- 切换 correct / mismatched / silence / gate0；
- 覆盖场景音频 encoder、window mode 和 inference gate；
- 保存预测与音频融合诊断。

dev760 可以直接构建同 checkpoint 的严格 A/B。

### OmniMMI PT

现有 `diagnose_omnimmi_pt.py` 已支持固定 32 帧、完整 logits、temperature/beta 离线判分，但尚未：

- 从视频解码场景音轨；
- 构建 1 秒音频窗口和时间戳；
- 向模型传入 `scene_audios` / `scene_audio_mask` / `scene_audio_timestamps` / `frame_timestamps`；
- 记录音频分支诊断和缺音轨回退。

需要扩展该隔离 runner，不能直接使用 OmniMMI 当前 M4 adapter。

### OmniMMI PA

当前 `intersuitonline_modeling.py` 逐帧构造视觉 embedding 并直接维护 KV cache，会绕过 `prepare_inputs_labels_for_streaming_av()`。仅设置 `AS_M4_ENABLE_SCENE_AUDIO=1` 不会让音频生效。

需要新增 run-scoped AS-M4Online adapter：每个 1 FPS 时刻读取对应 1 秒场景音频窗口，复用模型的 scene-audio encoder 和 fusion，将音频残差加到当帧视觉 feature 后，再进入原 PA KV-cache/attention/grounding 算法。原 PA 阈值、触发与 greedy 生成逻辑保持不变。

现有 `M4-Audio` / `M4-AudioOnline` 是用户问题语音路径，不是视频场景音轨，不能用于本测试。

## 5. 媒体与环境 Review

- PT：200 个视频全部存在；184 个含音轨，16 个无音轨；
- PA：200 个视频全部存在且全部含音轨；
- dev760 frozen manifest SHA256：`6d8a29e53532593eb5b66f007e0cc774631f52afa070b82a3e3e1718fd4a2ea7`；
- Conda：使用 `M4`；
- Python：`/home/yjm/miniconda3/envs/M4/bin/python`；
- PyTorch：`2.5.1+cu121`；
- Transformers：`4.44.0`；
- 磁盘：约 1003G 可用；
- 当前 Codex 沙箱没有 `/dev/nvidia*`，`torch.cuda.is_available=False`，不能判断宿主 GPU 空闲与功率。GPU smoke 前必须重新执行有设备权限的 preflight。

PT 的 16 个无音轨样本必须精确回退 video-only，并分别报告：

- 全量 200 条结果；
- 有音轨 184 条子集结果；
- 无音轨 16 条回退一致率。

## 6. 推荐 A/B 矩阵

### 绝对参考层

| ID | checkpoint | 音频 | 用途 |
| --- | --- | --- | --- |
| `REF_OFFICIAL_M4` | 官方公开 M4 | 关闭 | 对齐最近 PT/PA 最优绝对参考 |

### 同权重纯音频增量层

| ID | checkpoint | 音频条件 | 用途 |
| --- | --- | --- | --- |
| `PAIR_BEHAVIOR_OFF` | 最新诊断 AS-M4 checkpoint | behavior off | 同权重原路径 |
| `PAIR_GATE0` | 同上 | 正确音频、gate=0 | 验证执行链精确回退 |
| `PAIR_AUDIO_CORRECT` | 同上 | 正确音频、gate=0.005 | 音频接入后结果 |
| `PAIR_AUDIO_MISMATCHED` | 同上 | 错配音频 | 语义选择性负对照，dev760 必跑；PT/PA 可先 pilot |

## 7. 执行门禁

1. CPU contract、配置哈希和 dry-run 全部通过；
2. 宿主 GPU preflight 通过，记录 GPU、功率、显存和进程；
3. PT、PA、dev760 每个条件先跑 1–2 条 smoke；
4. gate0 必须与 behavior off 精确一致；
5. PT 无音轨样本必须精确回退；
6. PA 必须证明音频 feature 确实进入逐帧视觉 embedding，禁止只看环境变量；
7. 先运行 dev760 和 PT pilot；若 correct 明显退化或与 gate0 完全无差别，暂停耗时更长的 PA 全量；
8. 全量输出必须保存配置、开始时间、stdout/stderr、逐样本预测、指标和 postflight。

## 8. 推荐下一步

推荐先按当前最新 checkpoint 做“诊断性 A/B”，完成 runner 接线、CPU 测试和三条路径 smoke；只有 smoke 与小规模 pilot 证明音频确实生效且没有灾难性退化后，再运行 PA 与 dev760/OmniMMI 全量。

如果要求“正式证明最新音频优于原模型”，则当前 checkpoint 不满足前提，应先训练并通过新的 reserve/dev 选择性门禁，再做全量 OmniMMI 对比。
