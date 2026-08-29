# M4/PTPA 音频置信度融合完整数据集调研

日期：2026-08-29（Asia/Shanghai）

## 1. 需求定义

第一阶段只解决音频融合，不处理音视频错位。目标数据集必须支持：

1. `QuestionRouter`：区分纯视觉、纯音频和音视频联合问题；
2. `QualityGate`：比较清晰、加噪、静音、低音量、削波或重叠声音；
3. `RelevanceGate`：比较正确音频、跨视频错配音频和同视频无关声音；
4. 原始音视频：可以重新提取 BEATs 和视频特征；
5. 流式切窗：视频片段长度和时间信息足以构建因果窗口；
6. 物理视频级 train/dev/test 隔离；
7. 可构造成 correct/mismatch/noisy/silence/visual-question/audio-question 成组样本；
8. 有明确许可证、来源和可复现下载方式。

## 2. 调研结论

没有找到一份公开数据集原生同时提供：

- 音频是否与问题相关；
- 声学质量或 SNR 标签；
- 正确/错配音频配对；
- 流式窗口状态；
- AVQA 答案监督。

因此不能诚实地把任何现成数据集直接称为“完全贴合”。最优方案是选择一份单一母数据集，
在不引入第二份语义数据集的前提下，派生出完整的置信度融合训练合同。

本次推荐：

```text
唯一母数据集：Valor32k-AVQA v2.0
派生数据集：AS-M4-CGF-Valor32K-v1
```

论文与发布入口：

- 论文：ACM Multimedia 2025，`Valor32k-AVQA v2.0: Open-Ended Audio-Visual Question Answering Dataset and Benchmark`；
- 项目页：https://inesriahi.github.io/valor32k-avqa-2/
- 官方 GitHub：https://github.com/inesriahi/valor32k-avqa-2
- Hugging Face：https://huggingface.co/datasets/inesriahi/valor32k-avqa-v2
- 许可证：CC BY-NC 4.0 / research use only；
- 母数据来源：VALOR-32K / AudioSet 10 秒视频片段。

## 3. 为什么选择 Valor32k-AVQA v2.0

### 3.1 原生问题模态标签

每条问题带有：

```text
modality = visual | audio | audio-visual
```

这可以直接构造第一阶段路由目标：

```text
visual       -> task_audio_need = 0
audio        -> task_audio_need = 1
audio-visual -> task_audio_need = 1
```

相比从问题关键词猜测是否需要音频，原生 modality 字段更适合训练 `QuestionRouter`。

### 3.2 同一视频包含多类问题

数据同时发布 flattened 和按视频分组的 unflattened JSON。每个视频平均约 7.8 条问题，项目示例显示
同一视频可同时拥有 visual、audio 和 audio-visual 问题，适合构造：

```text
同一媒体 + 不同问题模态
```

这对排除“Gate 只识别媒体、不理解问题”的捷径非常重要。

### 3.3 真实世界与短视频

母数据来自 AudioSet/VALOR-32K 的真实世界可听视频。每个样本约 10 秒，适合当前第一阶段：

```text
window_sec = 1.0
hop_sec = 1.0
约 10 个因果窗口/clip
```

无需先解决长视频时间对齐，即可测试逐窗口 Quality/Relevance Gate 和 memory snapshot。

### 3.4 问题与答案形式完整

字段包括：

- `video_id`；
- `question`；
- 四个 `options`；
- `correct_answer_idx`；
- 三个 `rephrased_answers`；
- `modality`；
- `category`；
- `source_tags`；
- `quality_rating`；
- `oid`；
- 标注生成模型。

同时支持选择题和开放式答案，可先用稳定的 MCQ/排序监督训练 Gate，再用于 M4 开放生成验证。

## 4. Dataset Viewer 实际审计结果

本次使用 Hugging Face Dataset Viewer API 只读核对，而不是只引用项目页标称值。

### 4.1 实际总量

```text
总行数：225,487
train：177,132
validation：22,267
test：26,088
字段数：13
```

项目页标称总数为 225,495，实际 Viewer 少 8 条；train 标称 177,140，实际少 8 条。
后续必须以下载文件 SHA 和实际行数为准，不把网页标称值写死为合同。

### 4.2 训练集模态分布

```text
visual：101,558
audio：36,026
audio-visual：39,548
```

训练集视觉问题明显更多，不能直接按原分布训练 Router，否则模型容易偏向关闭音频。

### 4.3 `quality_rating` 分布

```text
obvious：169,062
guess：4,492
空值：3,578
```

必须特别说明：`quality_rating` 表示“问题答案是否可以明显推断”，不是音频声学质量。
它只能用于过滤 QA 标注可靠性，不能作为 `QualityGate` 的音质标签。

### 4.4 `obvious` 子集的模态分布

```text
visual obvious：96,205
audio obvious：34,648
audio-visual obvious：38,209
合计：169,062
```

这三个子集足以构造约 3.4 万/类的平衡 Router 基线，而无需使用 `guess` 或空值条目。

### 4.5 标注来源与质量风险

- 训练集 `gpt4o`：173,554；
- `gpt3.5`：3,578；
- 问题和答案由自动提示流程生成；
- 只在代表性子集上进行人工验证；
- 官方曾修复 options 数量、重复选项、rephrased answers 数量、modality/category 异常等格式问题。

因此第一轮建议只使用：

```text
model = gpt4o
quality_rating = obvious
```

同时必须对拟用 pilot 做人工抽检，不能把 `obvious` 当成完整人工真值保证。

## 5. 与其他候选数据集比较

| 数据集 | 问题模态标签 | 真实世界 | 声学质量 | 错配构造 | 流式切窗 | 主要缺点 |
|---|---:|---:|---:|---:|---:|---|
| Valor32k-AVQA v2.0 | 强 | 强 | 需派生 | 强 | 强 | 媒体外部下载、自动标注、非商用 |
| MUSIC-AVQA / v2.0 | 强 | 弱 | 需派生 | 中 | 中 | 主要是音乐演奏，场景过窄 |
| AVQA ACM MM 2022 | 弱 | 强 | 需派生 | 强 | 强 | 大多数问题依赖 Both，路由极不平衡 |
| FortisAVQA | 有 | 音乐域 | 无 | 弱 | 中 | 主要测试语言偏差，不研究音频质量 |
| Missing-AVQA | 有 | 混合 | 缺失模态 | 中 | 中 | 重点是模态重建，结构过重 |

本地 AVQA 标注审计进一步显示：

```text
train 40,425 条：Both=40,146，Sound=199，View=80
validation 16,910 条：Both=16,820，Sound=64，View=26
```

其 `Sound/View` 数量不足以单独训练稳定的 QuestionRouter，所以不作为唯一母数据集。

本地 `MUSIC-AVQA-v2.0` quarantine 只有 196 个媒体文件，且包含 `_flip` 等派生媒体，不能视为完整发布。

## 6. 派生完整数据集：AS-M4-CGF-Valor32K-v1

### 6.1 数据单元

每条记录不是孤立 QA，而是属于一个 `pair_group_id`：

```text
同一 video + 同一 question
    clean_correct
    noisy_correct
    silence
    mismatch
```

同一物理视频中的 visual/audio/audio-visual 问题再通过 `router_group_id` 关联。

### 6.2 第一阶段条件

#### `clean_correct`

- 使用原始视频音轨；
- 只代表“未额外退化”，不绝对标为高质量；
- 作为所有相对质量排序的 anchor。

#### `noisy_correct`

首轮使用确定性退化配方：

```text
snr_db = 20, 10, 0
noise_type = white | pink | same-dataset-background
seed = hash(pair_group_id, snr_db, noise_type)
```

语义音频保持不变，预期仅降低 `g_quality`，不把它标成 relevance negative。

#### `silence`

- 与原音频长度完全一致；
- 全零波形；
- `quality_target=0`；
- 最终 gate 应精确关闭。

#### `mismatch`

- 音频来自同一 split 的不同物理视频；
- 长度、采样率、编码和窗口数保持一致；
- 优先匹配相同 category、不同答案/不同音频事件；
- 禁止从 validation/test 借音频给 train；
- 对 audio/audio-visual 问题，要求 `relevance(correct) > relevance(mismatch)`；
- 对 visual 问题，correct 与 mismatch 都应保持低 task gate。

#### `low_gain/clipping`

作为第二批质量扰动：

- `low_gain_db = -12, -24`；
- clipping 阈值按原波形峰值比例固定；
- 不与首轮噪声条件同时叠加，避免 QualityGate 无法区分因素。

### 6.3 第一阶段明确排除

- 不生成时间平移条件参与训练；
- 不启用 `SyncGate`；
- 不把 offset 当 Quality/Relevance 标签；
- 不引入 ASR 文本错误；
- 不从其他语义数据集混入 QA；
- 不访问 official test 做模型选择。

## 7. 标签映射

### 7.1 Router

```text
visual       -> router_target = 0
audio        -> router_target = 1
audio-visual -> router_target = 1
```

首轮也可以将 audio-visual 设置为 soft target，但必须预注册，不能根据验证结果反复修改。

### 7.2 Quality

不使用虚假的绝对 clean 标签，采用相对排序：

```text
quality(clean_correct) > quality(noisy_20)
quality(noisy_20)      > quality(noisy_10)
quality(noisy_10)      > quality(noisy_0)
quality(any_valid)     > quality(silence)
```

如果原音频本身嘈杂，以上仍只表示“额外退化后的版本不应更可靠”。

### 7.3 Relevance

```text
audio/audio-visual question:
    relevance(correct) > relevance(mismatch)

visual question:
    task_audio_need(correct) ~= task_audio_need(mismatch) ~= 0
```

Quality 与 Relevance 必须解耦：高质量但语义错配的音频应表现为 `quality 高、relevance 低`。

## 8. 流式字段

推荐 manifest 字段：

```text
dataset_version
split
physical_video_id
clip_id
question_id
pair_group_id
router_group_id
video_path
audio_source_path
audio_source_physical_video_id
question
answer
options
modality
category
condition
corruption_recipe
corruption_seed
window_sec
hop_sec
stream_trigger_sec
router_target
quality_rank
relevance_rank
source_annotation_sha256
source_media_sha256
license_provenance
```

第一版固定：

```text
window_sec = 1.0
hop_sec = 1.0
stream_trigger_sec = [2, 4, 6, 8, 10]
g_sync = 1
```

派生音频优先按配方在线生成，不为每个 QA 复制多份 WAV，避免存储量随 condition 数量成倍增长。

## 9. Split 与泄漏合同

不能只相信官方 JSON 文件名。正式使用前必须：

1. 以 `video_id`、原 YouTube ID、起止时间和媒体 SHA 建立物理媒体身份；
2. 检查 train/validation/test 是否存在同源视频或重复片段；
3. 同一物理视频的所有问题和派生条件必须处于同一 split；
4. mismatch 音频只能来自相同 split；
5. 排除当前冻结 dev300、dev760、AVE/UnAV 验证和所有 formal test 的媒体 SHA/ID；
6. official test 全程封存，不用于阈值、扰动强度或 checkpoint 选择；
7. 先从 official train 内按物理视频 hash 划分 P2 train/internal-dev；
8. official validation 只在结构和阈值冻结后使用一次。

## 10. 建议规模

### 10.1 P2 pilot

先构建：

```text
1,000 个可解码物理视频
每种 modality 至少 2,000 条 obvious/gpt4o QA
约 6,000 条 base QA
约 2.8 万条 condition manifest 记录
```

pilot 只用于验证媒体、字段、配对、退化和泄漏合同，不训练 7B。

### 10.2 完整第一阶段

以 `obvious` 子集中的最小模态数量为上限，目标可设为：

```text
visual：最多 34,648
audio：最多 34,648
audio-visual：最多 34,648
base QA：最多约 103,944
```

实际数量必须在媒体可用率、人工抽检、物理去重和排除集合审计后重新计算，不能提前承诺。

## 11. 媒体与许可证风险

### 11.1 媒体不是自包含发布

Hugging Face Dataset Viewer 的 13 个字段只有标注，没有训练视频列。项目页提供外部“全部视频”链接，
但 VALOR 原项目同时说明受版权限制，主要发布 YouTube ID、起止时间和标注。因此必须先验证：

- 外部链接是否仍可访问；
- 是否真的覆盖 train/validation；
- 每个文件是否包含音轨；
- 物理 ID 和 clip 时间是否能可靠恢复；
- 下架视频比例；
- 下载内容是否允许按 CC BY-NC 4.0 进行本研究。

在完成可用率审计前，Valor32k-AVQA v2.0 只能称为“最佳候选母数据集”，不能称为已经准备好的
完整本地训练集。

### 11.2 非商业限制

官方 LICENSE 为 Creative Commons Attribution-NonCommercial 4.0。适合当前学术研究，但：

- 必须保留 attribution 与来源；
- 派生 manifest 和报告要记录许可证；
- 不能默认用于商业发布；
- 原视频仍可能受上传者和平台权利限制。

### 11.3 本机现状

```text
Valor32k-AVQA v2.0 本地媒体：未发现
现有 AVQA：只有 23 MB train/val 标注，无媒体
现有 MUSIC-AVQA-v2 quarantine：196 个媒体，约 640 MB，不完整
当前磁盘可用：约 881 GB
```

当前主要风险是媒体完整性和许可证，不是磁盘容量。

## 12. 下载前硬门槛

在用户授权下载前，只允许先获取小型 annotation ZIP 或 Dataset Viewer 元数据。正式媒体下载必须满足：

1. URL/镜像来源是官方项目、原 VALOR 或 Hugging Face 官方发布；
2. 许可证已记录；
3. 下载目标目录明确；
4. 先下载不超过 100～200 个 pilot 媒体；
5. 统计成功率、音轨率、时长、采样率、SHA 和实际每文件大小；
6. pilot 达到预注册可用率后，才估算全量空间与时间；
7. 不通过代理绕过平台条款，不从不明来源补媒体；
8. 网络或沙箱失败时报告原始错误，不伪造缺失媒体。

## 13. 最终判断

### 是否存在“开箱即用且完全贴合”的公开数据集？

**不存在。**

### 哪一份最接近？

**Valor32k-AVQA v2.0。** 它在问题模态标签、真实世界覆盖、短视频、QA 数量、问题类别和
同视频多问题方面明显优于其他候选。

### 怎样才能完全贴合？

以 Valor32k-AVQA v2.0 为唯一母数据集，构造 `AS-M4-CGF-Valor32K-v1`：

- 原生 modality 监督 QuestionRouter；
- correct/mismatch 监督 RelevanceGate；
- 原音频与确定性退化的相对排序监督 QualityGate；
- 10 秒视频按 1 秒因果窗口形成流式合同；
- 物理视频级重分组和排除集合防止泄漏；
- 第一阶段始终固定 `g_sync=1`。

该方案满足“先解决融合、后处理错位”，并且所有首轮训练信号都可从同一母数据集内部构造。
