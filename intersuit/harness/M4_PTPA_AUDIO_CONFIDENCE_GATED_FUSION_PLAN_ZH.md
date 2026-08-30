# M4/PTPA 音频置信度感知融合计划书

日期：2026-08-29（Asia/Shanghai）

## 1. 计划定位

本计划以当前 `M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1` 为诊断基座，
将现有固定音频残差融合改造成问题条件下的、逐窗口的音频置信度门控融合。

核心目标不是让模型“更多地使用音频”，而是让模型学会判断：

1. 当前问题是否需要音频；
2. 当前音频窗口是否清晰、可靠；
3. 当前音频是否与问题和画面相关；
4. 当前音频与视频是否同步；
5. 只有上述条件满足时，才允许音频显著改变视频表示。

当前版本使用固定 inference gate `0.005`。已有 correct/mismatch 小规模诊断显示，
正确音频和错配音频尚未形成任务侧选择性。因此，本计划优先解决 query/task routing
与音频可靠性判断，不把“直接放大固定 gate”作为合法路线。

## 2. 当前基线与问题

### 2.1 当前正式音频路径

```text
1 秒 scene-audio 窗口
-> 冻结 BEATs
-> micro_window Top-8
-> audio projector / v4 semantic projector（按实验合同选择）
-> 固定顺序映射到视频帧
-> 固定 gate 残差融合
-> M4/Qwen2
```

当前路径已经具备音频窗口选择和精确 `gate=0` 行为回退，但存在以下问题：

- gate 不读取问题特征，无法区分音频问题和纯视觉问题；
- gate 基本不区分正确、错配、噪声和延迟音频；
- 固定残差可能干扰本来依靠视频即可回答的问题；
- 单纯增大 gate 可能同时放大有效音频和错误音频；
- 当前 offset scorer 主要提供诊断结果，尚未安全地参与正式融合校正。

### 2.2 本计划不解决的内容

首轮明确不做：

- 不解冻 BEATs；
- 不联合训练 M4/Qwen2、视觉塔或原有 projector；
- 不直接修改视频帧采样预算；
- 不在第一阶段执行硬时间窗口移动；
- 不同时引入 MUG、动态时间校正和全模型训练；
- 不使用 formal test 选择结构、阈值或 checkpoint；
- 不把 ASR 置信度伪装成声学质量置信度。

## 3. 总体结构

门控拆成三个可解释分量：

```text
g_quality(t)   = 当前音频窗口本身是否可靠
g_relevance(t) = 当前音频是否与问题、画面相关
g_sync(t)      = 当前音频与视频时间关系是否可信

g_audio(t) = clamp(
    g_quality(t) * g_relevance(t) * g_sync(t),
    0,
    gate_max
)
```

融合形式：

```text
delta_audio(t) = fusion_projector(audio_feature(t))
delta_safe(t)  = ratio_cap(delta_audio(t), video_feature(t))
video_fused(t) = video_feature(t)
                 + residual_scale * g_audio(t) * delta_safe(t)
```

首轮保留乘法分解，因为任意一个条件明显不可靠时，最终音频影响都应下降。
训练和诊断必须分别记录三个子 gate，不能只记录最终乘积。

阶段边界：第一阶段固定 `g_sync(t)=1`，实际训练和融合只使用
`g_quality(t) * g_relevance(t)`；`g_sync` 及时间窗口修正属于第二阶段，第一阶段不因其未实现而阻塞。

## 4. 三类 Gate 设计

### 4.1 Quality Gate：声学可靠性

建议输入：

- 窗口 RMS；
- loudness / dBFS；
- silence ratio；
- 波形 clipping ratio；
- 音频特征 norm 与相邻窗口稳定性；
- 可选的噪声或 SNR 估计；
- 可选的重叠说话概率。

第一版不要求引入大型音频质量模型。优先使用确定、可审计的波形统计和冻结特征统计，
由小型 MLP 输出 `g_quality(t)`。

预期行为：

- 静音窗口接近 0；
- 极低 SNR、严重削波或异常幅值窗口下降；
- 正常清晰的有效声音高于其加噪版本；
- 不能简单把“声音响”当成“质量好”。

### 4.2 Relevance Gate：问题与任务相关性

这是第一优先级，也是当前固定融合最缺失的能力。

建议分成两层：

```text
task_audio_need = P(当前问题需要音频 | question)
window_relevance(t) = P(当前窗口与问题相关 | audio(t), video(t), question)

g_relevance(t) = task_audio_need * window_relevance(t)
```

建议输入：

- 音频窗口特征 `A_t`；
- 对应视频帧摘要 `V_t`；
- 问题池化特征 `Q`；
- `cos(A_t, Q)`；
- `cos(A_t, V_t)`；
- 音频窗口事件性或变化分数；
- 可选的问题类型先验，但不能只依赖关键词。

预期行为：

- “听到了什么声音”等音频问题：正确相关音频 gate 上升；
- “画面中有几个人”等视觉问题：音频 gate 接近 0；
- 与问题无关或来自其他视频的音频：低于正确音频；
- 仅替换问题、不替换媒体时，gate 应随任务需求变化。

### 4.3 Sync Gate：时间可靠性（第二阶段）

第一阶段只把时间同步信息作为降权信号，不执行硬窗口移动。

建议输入：

- offset scorer 的最大概率；
- 第一与第二候选的 margin；
- 预测 `abs(offset)`；
- 连续窗口 offset 的稳定性；
- 音频 onset 与视频变化的一致性；
- 候选 offset 是否通过冻结门槛。

预期行为：

- 同步且高 margin 的窗口保持较高权重；
- 时间平移且低置信的窗口降权；
- offset 不确定时不强行移动窗口；
- 只有独立 offset 评测和下游选择性同时通过后，第二阶段才允许软重采样。

## 5. ASR 与场景音频的职责边界

当前 scene-audio 主路径使用 BEATs，不依赖 ASR。因此：

- 背景噪声、静音、削波、重叠声源属于 `g_quality`；
- 音频是否回答当前问题属于 `g_relevance`；
- 声音延迟属于 `g_sync`；
- ASR token confidence、no-speech probability、重复率只在显式接入 ASR 文本时使用；
- 未来若融合 ASR 文本，应增加独立 `g_asr`，不能把 ASR 错误混入 BEATs 声学质量标签。

## 6. 最小可行模型

首版只增加轻量模块：

```text
QuestionRouter:
    Q -> LayerNorm -> Linear -> GELU -> Linear -> sigmoid

QualityGate:
    [audio_stats, audio_feature_summary]
    -> LayerNorm -> Linear -> GELU -> Linear -> sigmoid

RelevanceGate:
    [A_t, V_t, Q, cos(A_t,V_t), cos(A_t,Q)]
    -> LayerNorm -> Linear -> GELU -> Linear -> sigmoid

SyncGate（第二阶段接口，第一阶段恒等返回 1）:
    [offset_confidence, margin, abs_offset, stability]
    -> small MLP or deterministic calibrated function
```

为控制显存，可先把 `A_t/V_t/Q` 投影到较低维度再拼接。Gate 最后一层使用负 bias
初始化，使新模型初始行为接近关闭音频；但要配合排序监督，防止永久塌缩为零。

## 7. 流式输入与流式输出合同

### 7.1 两类“流式”的边界

本计划区分：

1. **输入流式**：音频和视频窗口持续到达，模型持续更新多模态 memory；
2. **输出流式**：回答触发后，Qwen2 使用 KV Cache 逐 token 输出文字。

第一版目标锁定为：

```text
音视频持续流入
-> 因果更新 Gate 与多模态 memory
-> 用户提问或触发回答
-> 冻结当前 memory 快照
-> Qwen2 基于该快照逐 token 流式输出
```

第一版不允许在答案生成过程中用新到达的音视频修改已经冻结的上下文，也不承诺撤回已经输出的
token。生成期间到达的新窗口进入下一版 memory，只影响下一次回答。这样可以保持输出确定性、
KV Cache 一致性和可复现实验。

### 7.2 严格因果 Gate

流式 Gate 必须满足：

```text
gate_t = f(audio_<=t, video_<=t, question)
```

禁止：

- 使用 `t` 之后的音频或视频；
- 在整个序列上做需要未来窗口的全局 softmax；
- 用完整视频统计量归一化当前 gate；
- 在离线训练时无意引入双向 attention，再宣称可流式运行；
- 让同 batch 中其他 stream 的状态泄漏到当前 stream。

问题特征 `Q` 在一次提问中只编码一次，之后复用于所有窗口。`QualityGate` 和
`RelevanceGate` 按窗口计算；第一阶段令 `g_sync=1`，不处理错位，也不等待未来 offset 证据。

### 7.3 流式状态

每条独立 stream 至少维护：

```text
stream_id
memory_version
last_timestamp
audio_quality_ema
gate_ema
gate_active
last_audio_feature / short_audio_cache
short_video_summary_cache
question_feature（存在当前问题时）
M4/Qwen2 KV Cache（只属于一次冻结回答）
```

状态必须支持显式初始化、重置、序列结束和异常中止。不同视频、不同用户或不同并发请求不得共享
EMA、事件状态、问题特征或 KV Cache。

### 7.4 Gate 平滑与防抖

逐窗口原始 gate 容易随噪声快速跳变。第一版采用因果 EMA：

```text
gate_ema(t) = beta * gate_ema(t-1) + (1-beta) * gate_raw(t)
```

并支持：

- 开启阈值高于保持阈值的 hysteresis；
- 最大单窗口上升/下降幅度限制；
- 最短保持窗口；
- 静音或无效 mask 时快速关闭；
- 恢复音频时缓慢开启；
- stream reset 后不得继承上一段视频的 gate。

平滑只能使用历史状态，不能引入对未来窗口的对称滤波。

### 7.5 首 token 延迟与计算预算

小型 `QuestionRouter/QualityGate/RelevanceGate` 相比冻结 BEATs 和 7B Qwen2 应保持轻量，
但不能预先宣称“开销可忽略”。必须分别记录：

- 音频窗口编码耗时；
- Gate 前向耗时；
- memory 更新耗时；
- 回答触发到首 token 的 P50/P95 延迟；
- 后续 token/s；
- 输入实时因子、GPU 峰值显存和状态缓存大小；
- fixed-gate 与 confidence-gate 的延迟差值。

第一版不接入完整 QA-TIGER Gaussian MoE，不在每个生成 token 上重复运行 Gate，也不因新窗口到达
重算已有 KV Cache。

### 7.6 输出一致性与迟到证据

回答触发时生成不可变的 `memory_version`。预测、日志和评测记录必须绑定：

```text
stream_id + memory_version + last_input_timestamp + question_id
```

若关键声音在回答开始后才到达，第一版有三种合法行为：

1. 等待预注册的最小音频上下文后再触发回答；
2. 本次回答不使用迟到窗口，下一次提问使用新 memory；
3. 上层产品显式发起“修正回答”，作为新的 generation，不覆盖旧 token 记录。

不允许静默修改已经展示的答案，也不允许用未来窗口回填后把结果计作实时预测。

### 7.7 流式正确性测试

必须新增以下合同：

- **future invariance**：改变未来窗口不得改变当前及历史 gate；
- **prefix equivalence**：同一前缀按整段因果运行与逐 chunk 运行结果一致；
- **chunk boundary consistency**：改变合法 chunk 切分不应显著改变最终 memory；
- **state reset**：新 stream 首窗口与独立运行逐元素一致；
- **stream isolation**：交错处理两条 stream 与分别处理结果一致；
- **snapshot immutability**：回答开始后新输入不改变本次生成上下文；
- **gate0 streaming fallback**：任意 chunk 顺序下 `gate=0` 残差均精确为零；
- **empty/silence continuity**：空窗口、静音窗口和 mask 缺口不产生 NaN/Inf 或状态污染。

### 7.8 外部论文思想的流式化约束

- QMF 只借鉴质量置信度与噪声监督，将样本级权重改为因果逐窗口权重；
- QA-TIGER 只借鉴问题显式注入与相关性建模，首轮不移植依赖完整序列的 Gaussian experts；
- PDF 的相对置信度只能比较当前与历史可见证据；
- 任何外部实现若使用完整序列、双向 attention 或未来统计，必须重写并通过 future invariance，
  不能直接接入正式流式路径。

## 8. 训练样本与对照条件

每个训练或验证样本尽量构造以下配对条件：

| 条件 | 预期 Quality | 预期 Relevance | 预期 Sync |
|---|---:|---:|---:|
| 正确同步清晰音频 | 高 | 高或由问题决定 | 高 |
| 静音 | 低 | 不强制 | 不强制 |
| 正确音频加噪 | 低于清晰音频 | 保持语义趋势 | 高 |
| 跨视频错配音频 | 不一定低 | 低 | 不一定低 |
| 同视频无关事件窗口 | 不一定低 | 低 | 高 |
| 时间平移音频 | 不一定低 | 可能仍相关 | 低 |
| 纯视觉问题 + 正确音频 | 不一定低 | 接近零 | 不强制 |
| 音频问题 + 正确音频 | 高 | 高 | 高 |

关键原则：质量、相关性和同步性必须被独立扰动。不能把所有负样本都同时做成“静音、错配、
延迟”，否则模型无法学会三个 gate 的职责边界。

第一阶段训练只使用 correct、mismatch、silence、noisy 和 visual/audio-question 对照；
时间平移条件只冻结为第二阶段评测资产，不进入首轮 Gate 选择或训练。

## 9. 损失函数

总损失建议为：

```text
L = L_task
  + lambda_rank * L_gate_rank
  + lambda_quality * L_quality
  + lambda_relevance * L_relevance
  + lambda_sync * L_sync
  + lambda_sparse * L_visual_suppression
  + lambda_cal * L_calibration
```

第一阶段设置 `lambda_sync=0` 且 `g_sync=1`；第二阶段必须重新评审后才能启用 `L_sync`。

### 9.1 最终任务损失 `L_task`

使用原有问答语言模型 loss 或冻结模型下的任务代理损失。首轮冻结 M4 时，必须确认该损失
仍能向 Gate 和 fusion 产生有限梯度。

### 9.2 排序损失 `L_gate_rank`

比绝对 gate 标签更重要，主要约束：

```text
gate(correct_clean) > gate(correct_noisy)
gate(correct)       > gate(mismatch)
gate(sync)          > gate(shifted)
gate(audio_question)> gate(visual_question)
```

使用带 margin 的 pairwise ranking loss，减少人工规定绝对 gate 数值带来的偏差。

### 9.3 子 Gate 监督

- `L_quality`：清晰/加噪/静音/削波的质量排序或软标签；
- `L_relevance`：正确配对、同视频无关窗口、跨视频错配和问题类型监督；
- `L_sync`：人工时间平移和冻结 offset scorer 置信度监督；
- `L_visual_suppression`：纯视觉问题下惩罚非必要音频影响；
- `L_calibration`：使用 Brier loss 或 ECE 相关代理，使 gate 数值具备可解释性。

## 10. 防止 Gate 塌缩

必须同时防范：

1. **全零塌缩**：关闭音频最安全，任务 loss 可能鼓励永远不用音频；
2. **全开塌缩**：训练音频总是正确时，gate 可能退化为常数；
3. **关键词捷径**：仅看到 `sound/hear` 就开门，不检查音频内容；
4. **响度捷径**：只把高能量声音判为高质量；
5. **数据来源泄漏**：从路径、长度或处理方式识别 correct/mismatch；
6. **错误校时放大**：低置信 offset 被当成确定校正；
7. **融合尺度掩盖**：Gate 有区分但 residual 太小或 projector 抹平语义差异。

对应措施：

- 同一音频配不同问题、同一问题配不同音频；
- 负样本保持音量、时长和编码方式一致；
- 单独报告三个子 gate 和最终 gate 分布；
- 同时报告 gate 区分度、残差比例和最终任务变化；
- 保留正确/错配/静音/加噪/延迟的成组样本；
- 每轮检查 gate 饱和率、梯度和表示差异。

## 11. 分阶段实施路线

### P0：合同与只读基线

- 冻结当前 checkpoint、config、overlay 和数据 manifest SHA；
- 固定 behavior-off、gate0、fixed-gate 三条参考路径；
- 建立 correct/mismatch/silence/noisy/shifted/visual-question 成组合同；
- 记录现有预测、gate、delta ratio 和任务指标；
- 固定离线整段、因果 prefix、逐 chunk 和输出 token streaming 四种基线；
- 固定回答触发规则、最小上下文和 memory snapshot 字段；
- 不训练、不访问 formal test。

通过标准：样本配对、媒体来源、扰动方式、物理视频隔离和回退均可机器审计。

### P1：CPU Gate 合同

- 实现 QuestionRouter、QualityGate、RelevanceGate 的纯 CPU shape 合同；
- 为第二阶段预留 `SyncGate` 接口，但第一阶段必须逐元素恒等返回 1；
- 实现按 `stream_id` 隔离的 EMA、hysteresis、reset 与 memory snapshot 合同；
- 验证 mask、padding、全静音、全空窗口、NaN/Inf 和 batch 行为；
- 验证 `force_audio_gate=0` 逐元素精确回退；
- 验证初始 gate 有界且不会覆盖用户已有权重。

流式附加通过标准：future invariance、prefix equivalence、stream isolation、state reset、
snapshot immutability 和 gate0 streaming fallback 全部通过。

通过标准：定向单测全部通过，gate 和融合输出有限，回退逐元素一致。

### P2：合成扰动与监督审计

- 构建可复现的噪声、静音和削波版本；
- 构建跨视频错配与同视频无关事件窗口；
- 时间平移版本只生成、审计并冻结为第二阶段资产，不进入第一阶段训练和模型选择；
- 固定随机 seed、扰动参数和 manifest SHA；
- 检查 train/validation/test 物理媒体泄漏；
- 独立验证质量与相关性标签没有互相混淆，并验证第二阶段时间平移资产未混入首轮训练。

通过标准：无媒体泄漏，所有扰动可重现，质量/相关性/同步性可独立变化。

### P3：Gate-only 训练

- 冻结 BEATs、M4/Qwen2、视觉塔、现有 audio projector 和 fusion；
- 首轮只训练 Gate 与必要的低维投影；
- 按 `2-step -> 20-step -> 150-step` canary 推进；
- 每阶段保存 stdout、stderr、配置、开始时间、参数冻结审计和显存；
- Gate-only 参数量较小时优先单卡开发或普通 DDP，不默认使用所有 GPU。

通过标准：

- 只有授权 Gate 参数发生变化；
- 第一阶段 quality/relevance/ranking/task loss 和梯度有限，`L_sync` 保持关闭；
- correct/mismatch 排序准确率显著高于随机；
- visual-question gate 明显低于 audio-question gate；
- 不出现全零、全开或单一常数 gate。

### P4：冻结 7B 小规模链路验证

- 先做 `behavior-off` 与 `gate0` 精确回退；
- 再做最多 10～30 条 dev 的 correct/mismatch/noisy/silence/visual-question 对照；
- 固定 residual ratio cap；
- 比较 gate、delta、首 token、生成长度和预测变化；
- 比较 fixed/confidence gate 的首 token P50/P95、token/s、输入实时因子和状态缓存；
- 用同一输入分别执行离线快照生成和流式 chunk 累积后快照生成；
- 不因小样本有利结果扩大到 formal test。

通过标准：

- gate0 预测与 token 精确一致；
- correct gate 高于 mismatch/noisy/shifted；
- 纯视觉问题音频残差接近零；
- correct-only 新增答对多于 mismatch-only 新增答对；
- 无冻结权重漂移、nonfinite、EOS 崩溃或异常空输出。

### P5：受控训练与独立验证

只有 P4 通过后，才允许：

- 解冻轻量 fusion 或 norm-preserving projector；
- 保持 M4/Qwen2、BEATs 和视觉塔冻结；
- 在与评测物理视频严格隔离的训练集上训练；
- 使用一次性 validation 比较 fixed gate 与 confidence gate；
- 按问题类型、噪声等级和 offset 分层报告。

P5 仍失败时，回退到 P3 Gate-only checkpoint，不允许直接解冻全模型补救。

### P6：可选的软时间校正

只有 `g_sync` 和冻结 offset scorer 在独立数据上通过后，才允许把 offset 用于软重采样：

```text
aligned_audio(t) = sum_k P(offset=k) * audio(t+k)
```

低 margin 时保持原窗口并降低 gate。硬 argmax 移动不是默认方案。

## 12. 验收指标

### 12.1 Gate 本身

- correct vs mismatch gate AUC；
- audio-question vs visual-question AUC；
- clean vs noisy quality ranking accuracy；
- 第二阶段才报告 sync vs shifted sync ranking accuracy；
- Brier score / ECE；
- gate 均值、标准差、饱和到 0/1 的比例；
- 每类问题和事件的 gate 分布。

### 12.2 融合安全性

- `gate0_exact_fallback = true`；
- `delta_to_video_ratio` 分位数；
- correct/mismatch residual 距离；
- 空音频、静音、无效 mask 和缺失时间戳行为；
- 冻结权重运行前后 SHA 一致；
- 无 NaN/Inf、异常 EOS 或空输出。

### 12.3 最终任务

- 总体准确率不能显著低于 behavior-off/fixed-gate 基线；
- 音频问题 correct 条件优于 behavior-off；
- mismatch/noisy/shifted 不得优于 correct；
- 纯视觉问题不得因音频门控显著下降；
- correct-only 新增答对必须多于新增答错；
- 使用物理视频级 bootstrap CI，避免把窗口当成独立样本夸大显著性。

### 12.4 流式系统

- future invariance 违规数为 0；
- prefix/chunk 结果在预注册数值容差内一致；
- stream isolation 与 state reset 逐元素一致；
- 回答绑定不可变 `memory_version`；
- confidence gate 的首 token P95 增量处于预注册预算内；
- token/s 不因逐 token 重算 Gate 而下降；
- 长流运行中状态缓存有明确上限，不随总时长无界增长；
- 迟到窗口不会静默改变已输出 token。

## 13. 停止条件

出现以下任一情况应停止扩大：

- correct/mismatch gate 无可重复区分；
- gate 全零、全开或只依赖问题关键词；
- Gate 有区分，但最终 residual 被 fusion projector 再次压平；
- correct 与 mismatch 对最终答案影响相同；
- 纯视觉任务退化超过预注册容忍线；
- 数据泄漏、冻结权重漂移或扰动合同不成立；
- 小规模 dev 无收益时仍需靠放大 gate 才能产生变化；
- 需要解冻全模型才能掩盖 Gate 监督失败。
- 当前 gate 受未来窗口影响或不同 chunk 切分产生不可解释差异；
- 多条 stream 出现状态串扰、reset 不完整或 KV Cache 复用错误；
- confidence gate 需要在每个生成 token 上重算才能工作；
- 首 token P95、实时因子或显存开销超过预注册预算且无法由低维投影解决；
- 必须静默修改已输出 token 才能利用迟到音频。

停止后保留失败结果和诊断，不访问 formal test，不删除基线 checkpoint。

## 14. 回退机制

必须保留：

```text
AS_M4_ENABLE_SCENE_AUDIO=0
    -> 完全关闭 scene audio

AS_M4_FORCE_AUDIO_GATE=0
    -> 音频编码和诊断保留，融合残差精确为零

AS_M4_AUDIO_GATE_MODE=fixed
    -> 回到冻结 fixed-gate 基线

AS_M4_AUDIO_GATE_MODE=confidence
    -> 启用新三分量 Gate

AS_M4_STREAMING_GATE_STATE=disabled
    -> 关闭 EMA/hysteresis 状态，回到无状态逐窗口诊断

AS_M4_STREAMING_MEMORY_MODE=snapshot
    -> 回答触发后冻结 memory，本次生成不接收后续窗口
```

新 Gate 使用独立 run name 和 checkpoint，不覆盖：

- 原始 M4；
- 12k/32k no-freeze 基线；
- `M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1`；
- 已冻结 v4 semantic projector；
- 仍需复现或继续诊断的 fusion checkpoint。

## 15. 推荐实施顺序

最终推荐顺序为：

1. 先完成 `QuestionRouter + RelevanceGate`，解决当前最明确的 task routing 缺失；
2. 再加入波形统计驱动的 `QualityGate`；
3. 将前两者改造成严格因果逐窗口 Gate，加入状态隔离、平滑和 memory snapshot；
4. 在 `g_sync=1` 下通过流式 correct/mismatch/noisy/visual-question 选择性和延迟门禁；
5. 选择性与流式门禁通过后，再训练轻量 fusion；
6. 第二阶段才把冻结 offset scorer 作为 `SyncGate` 降权信号；
7. 最后才考虑软时间重采样；
8. MUG 式伪标签或全模型联合训练另立计划，不与本轮同时引入。

这一路线把“是否需要音频”“音频是否可靠”“音频是否同步”分开验证，能够在每一步失败时
明确定位原因，并保持对原 M4 行为的精确回退。

## 16. 2026-08-29 P0/P1 实施状态

已完成：

- 冻结机器可读 P0 合同 `audio_confidence_streaming_p0_contract_v1.json`；
- 新增 `QuestionAudioRouter`；
- 新增严格逐窗口的 `FirstStageAudioConfidenceGate`；
- 第一阶段 `sync` 输出逐元素恒为 1；
- 新增按 `stream_id` 隔离的 causal EMA、hysteresis、reset 和 immutable snapshot；
- 新增 future invariance、prefix/chunk equivalence、stream isolation、state reset、snapshot
  immutability、gate0 streaming fallback 等 CPU 合同；
- 定向回归 `44 passed, 2 warnings`；
- 完整 CPU 回归 `644 passed, 2 warnings`。

当前边界：

- 新 Gate 尚未接入 `StreamingAVModule` builder 或 `llava_arch.py` 正式融合路径；
- 尚未构建 P2 训练扰动 manifest；
- 尚未训练 Gate；
- 尚未运行 7B、GPU、真实流式延迟或任务收益评测；
- 现有 checkpoint 和旧 `AudioConfidenceGate` 的加载路径保持不变。

下一步应进入 P2：先构建与审计 correct/mismatch/noisy/silence/visual-question 配对合同，
继续保持 `g_sync=1`，不把时间平移样本混入第一阶段训练。

## 17. P2 母数据集调研结论

P2 推荐使用 `Valor32k-AVQA v2.0` 作为唯一母数据集，并派生
`AS-M4-CGF-Valor32K-v1`。原因是其原生提供 `visual/audio/audio-visual` 问题模态、
真实世界 10 秒音视频、同视频多问题、开放式答案和 MCQ。

第一阶段只使用 `model=gpt4o + quality_rating=obvious`，并平衡三种 modality。
`quality_rating` 不是声学质量标签；QualityGate 必须由原音频与确定性 noisy/silence/low-gain
派生版本的相对排序监督。Mismatch 只能在同 split、不同物理视频之间构造。

在下载媒体前必须先执行许可证、链接、媒体覆盖率、音轨、时长、物理视频身份和排除集合审计。
完整调研见 `M4_PTPA_AUDIO_CONFIDENCE_DATASET_RESEARCH_ZH.md`。

## 18. 2026-08-30 P2 实施状态

P2 已完成并通过构建器与独立审计器双重门禁：

- official train pilot150：150 个物理视频、450 个三模态问题组；
- 150/150 媒体可读并同时含音视频流；
- 每个媒体记录相对路径、大小、SHA、时长、采样率、声道和窗口数；
- 每个问题固定 14 个条件，共 6,300 条；
- 第一阶段 5,400 条包含 clean、9 档确定性噪声、silence 和 mismatch；
- `shift_-1.0s/+1.0s` 共 900 条只冻结为第二阶段资产，首轮训练混入数为 0；
- mismatch 自映射、跨 split、缺少目标长度规范和类别 fallback 均为 0；
- 独立审计的条件集合、noise seed、标签、阶段和来源错误均为 0。

条件 manifest SHA-256 为
`007d5b1d936323d28adee252ce6f407d458d24b3042b1caf1bb8a0086f301e26`。详细结果见
`M4_PTPA_AUDIO_CONFIDENCE_P2_RESULT_ZH.md`。

P2 通过不自动授权训练。下一步进入 P3 运行前合同和虚拟扰动数据加载器复现测试；只有冻结
审计、可训练参数白名单和 `2-step` 停止门槛评审通过后，才允许启动 Gate-only canary。

## 19. 2026-08-30 P3 训练前门禁状态

P3 preflight 已通过，训练仍关闭：

- P2 manifest 增加目标采样率、声道、时长和窗口数，新 SHA 为
  `007d5b1d936323d28adee252ce6f407d458d24b3042b1caf1bb8a0086f301e26`；
- 新增 ffmpeg float32 虚拟波形执行器，white/pink/background noise、silence、mismatch 和 shift
  均按冻结 recipe 执行；
- 真实 MP4 的 10 dB smoke 实测为 10.0 dB，静音全零，重复生成逐元素一致；
- 新增严格 `MM_TUNABLE_PARTS=audio_confidence_gate` 白名单和 optimizer 审计；
- hidden 3584/projection 128 下只允许 24 个 tensor、1,910,671 个参数训练；
- dormant FirstStage Gate 不进入默认 forward，并保持旧模块初始化顺序和旧 state dict 非严格加载兼容；
- `2 -> 20 -> 150` 的指标门槛与停止条件已冻结。

当前状态为 `PASS_P3_PREFLIGHT_TRAINING_CLOSED`。冻结特征预计算、Gate loss/collator 和 gate-only
checkpoint 保存恢复尚未实现，因此 2-step 仍未授权。详细报告见
`M4_PTPA_AUDIO_CONFIDENCE_P3_PREFLIGHT_ZH.md`。

## 20. 2026-08-30 P3 冻结特征管线代码状态

P3 训练侧与冻结特征管线代码已推进，但训练仍关闭：

- 冻结特征包升级为 `as_m4_audio_confidence_frozen_features_v2`，quality 与 relevance 使用
  独立监督 mask，避免视觉问题的空 relevance 标签进入排序损失；
- collator 已按已有 batch 维拼接，不再错误增加额外维度；
- 已实现 router、quality、relevance、visual suppression loss，第一阶段继续强制
  `lambda_sync=0`；
- 已实现 gate-only checkpoint 保存/恢复首版合同；
- 已实现 P2 row、虚拟波形、冻结 M4/BEATs/视觉/问题编码器到单样本特征包的预计算入口；
- 正式特征来源要求记录运行时模块 state SHA，禁止用随机投影或测试替身生成训练资产；
- 已实现 feature manifest 的独立 SHA、shape、监督 mask、shifted 排除和编码器来源审计；
- 已实现只消费独立审计 feature manifest 的 Gate-only 2-step 入口，按 pair group 组成 batch，
  禁止跨问题构造排序 pair，并检查 loss、gradient 与 gate 输出有限；
- 预计算入口读取机器合同并 fail closed；当前 `feature_precompute=false`，不会加载模型或 GPU。

当前仅表示代码门禁就绪。尚未生成 5,400 条正式冻结特征、尚未通过真实资产独立审计，
也尚未把这些特征接入 Gate-only optimizer step。因此状态仍为
`PASS_P3_PREFLIGHT_TRAINING_CLOSED`，`2-step` 继续禁止。下一步先评审并单独授权物理 GPU2
的冻结特征预计算；先运行极小 limit smoke，核对运行时模块 SHA、显存、窗口数和重复生成
一致性，通过后才允许生成完整 P3 特征资产。

## 21. 2026-08-30 P3 物理 GPU2 特征 smoke 合同

已建立 `audio_confidence_p3_feature_smoke_v1.json`，只授权冻结特征预计算，不授权训练：

- 固定物理 GPU2，即 `CUDA_VISIBLE_DEVICES=2`、程序内逻辑 `cuda:0`；
- 精确锁定同一物理视频上的 1 条 visual clean 与 1 条 audio clean，共 2 条；
- 输出只能写入 `intersuit/harness/artifacts/audio_confidence_p3_feature_smoke_v1/features`；
- 必须记录 stdout/stderr、开始时间、合同快照、运行时模块 state SHA、GPU 前后显存与内核错误；
- 必须通过 2 条、每条 9 个完整窗口、hidden 3584、finite、来源 SHA 和独立审计门禁；
- `two_step_training`、20/150-step、P4 7B 和 formal test 均保持关闭。

任何 GPU2 非空闲、驱动异常、SHA/shape 漂移、NaN/Inf、OOM、Xid/AER/PCIe/hung task
都会立即停止；本 smoke 通过也只表示真实特征接线可用，不自动授权完整 5,400 条预计算或训练。
