# Dynamic Window Selector V2 计划与数据集整体 Review

日期：2026-07-25（Asia/Shanghai）

## 1. 最终结论

计划结论：`review_updated_selector_first_v2_pilot_authorized`。

数据集组合结论：`review_passed_with_blockers`。

V2 只保留两个效果数据集：

```text
primary_oracle_utility: LongVALE
primary_localization_and_training: UnAV-100
ave_status: frozen_contract_regression_only
```

删除项：

```text
QVHighlights: removed_from_v2
MUSIC-AVQA: removed_from_v2
AVQA: removed_from_v2
```

保留不是基于 README 推测。LongVALE 与 UnAV-100 均已审查官方仓库固定 commit、逐条验证官方
annotation schema，并实际运行官方指标的最小完美预测夹具。UnAV 的 410 个历史暴露视频已从
三个 split 排除并按 `(video_id,event_label)` 重新冻结。后续完整 V1 已在 443 samples / 335
physical videos 上通过，不能再沿用“停止在媒体 pilot 前”的旧状态。

当前锁定 M4 checkpoint 的音频路径 selectivity 历史结论仍为 FAIL，但根据用户 2026-08-03
确认的 selector-first 主线，该失败只作为 V2 的显式风险和 mismatch 对照，不阻止全冻结、
无 optimizer 的 LongVALE `oracle_pilot`。pilot 未通过时立即停止 selector；通过后才解封 confirm。

## 2. 计划 Review

### 2.1 已通过项

- `pass`：V1/V2 完全不训练，V3 只允许 `streaming_av_module.question_conditioned_window_selector.*`，且启动时要求可训练参数量大于 0。
- `pass`：Oracle 是唯一训练授权门禁；Oracle 不改善冻结 M4 时立即停止 selector。
- `pass`：All、Uniform、Energy、Onset、cached Eventness、Oracle 和 Random 均有同预算对照。
- `pass`：Temporal scorer 继续 fail-closed，不复用 schema 不兼容的历史资产。
- `pass`：定位能力与下游语义收益分开；UnAV 定位结果不能替代 LongVALE Oracle utility。
- `pass`：V3 包含 Audio-only、Question-only、shuffled-query 和 empty-query，可诊断文本捷径。
- `pass`：ranking loss、优化器、batch、early stopping、seed 和 checkpoint 选择均在 validation 前冻结。
- `pass`：未把任何未验证数据集保留为辅助支线、正式 QA 门禁或失败后的替代数据。
- `pass`：1 秒窗口已固定为 `window=1.0/hop=1.0`，partial tail 的 timestamp 截断到媒体真实时长并保存有效采样数。
- `pass`：causal memory 已显式保存 Top-K feature/score/timestamp/source-index/mask 与 `next_source_index`，逐窗和不同 chunk-size 回放测试一致。
- `pass`：候选 checkpoint 的 config、index、4 个 shard 与 BEATs SHA 已锁定；其资格只限
  `diagnostic_only`，不得解释为已通过音频路径资格。

### 2.2 保留风险

- `risk_scope`：主结论只适用于 `event_duration / video_duration <= 10%` 的稀疏事件和固定 `K=8`。
- `risk_query`：UnAV 的问题来自 event label 模板，V3 只能证明 `event-label query-conditioned ranking`，不是开放域 QA。
- `risk_bridge`：LongVALE 只检验 Oracle 音频效用。参考 caption 不得同时作为 selector query 和生成评价答案。
- `risk_media`：当前只验证了官方仓库、annotation 和指标链；媒体有效率仍须由小规模 pilot 确认。
- `resolved_dependency`：M4 已固定 `pycocoevalcap==1.2`、`pycocotools==2.0.11`；LongVALE official golden 可在当前环境直接运行。UnAV 的 NumPy 弃用别名兼容仍被限制在 adapter 内。
- `risk_causality`：V1–V3 是离线实验，只有 V4 strict-causal replay 通过后才能讨论在线接入。
- `failed_checkpoint`：锁定 semantic-contrast checkpoint 的 selectivity 已 FAIL（video-only/correct/mismatched=254/251/259）；Fixed Stage2 也 FAIL（254/255/259）。当前没有合格替代基座。

### 2.3 执行授权

```text
contract_and_metadata_work_allowed: true
small_media_availability_pilot_allowed: true
current_route_stopped: false
oracle_pilot_allowed: true
oracle_confirm_allowed: false
selector_training_allowed: false
formal_test_allowed: false
semantic_contrast_allowed: false
```

## 3. LongVALE 审查

### 3.1 来源与版本

- GitHub：`https://github.com/ttgeng233/LongVALE`
- 审计 commit：`2eb88ce3f72458faea6f3db8f841b41e6485a814`
- Hugging Face：`https://huggingface.co/datasets/ttgeng233/LongVALE`
- annotation 许可：CC BY-NC-SA 4.0。

### 3.2 数据格式实审

官方 training：

```text
video_id
  duration: number
  segments:
    "start_end": caption
```

官方 eval：

```text
video_id
  duration: number
  timestamps: [[start, end], ...]
  sentences: [caption, ...]
```

逐条 schema 检查：

| split | 视频 | 事件 | 非法记录 |
| --- | ---: | ---: | ---: |
| training | 7,240 | 91,863 | 0 |
| eval | 1,171 | 13,867 | 0 |

training/eval 物理视频隔离。数据具备 duration、精确 timestamp 和 event caption，可直接构造 1 秒窗口的 Oracle 选择并执行 segment captioning。

### 3.3 官方指标实审

官方 `eval.py` 支持 `grounding`、`captioning` 和 `seg_captioning`。本计划使用的 `seg_captioning` 预测格式为：

```json
{
  "video_id": "video-a",
  "task": "seg_captioning",
  "query_id": 0,
  "answer": "A person rings a bell.",
  "info": {"sentence_id": 0}
}
```

官方 `metric.py` 实际计算：

- BLEU-4；
- METEOR；
- ROUGE-L；
- CIDEr。

已在 M4 Conda 环境中对完全匹配的两事件 golden fixture 运行官方实现，实际输出：

```text
BLEU4: 0.9999999997728718
METEOR: 1.0
Rouge: 1.0
CIDEr: 10.0
```

官方代码的 ROUGE-L 返回键是 `Rouge`，不是 `ROUGE_L`。正式报告允许将字段名规范化为 `ROUGE-L`，但不得替换 scorer、改变分词或重算分数。

### 3.4 依赖与判定

M4 环境现已安装并冻结 `pycocoevalcap==1.2` 与 `pycocotools==2.0.11`，服务器 OpenJDK 1.8.0_452 可满足 METEOR。Codex 沙箱内 site-packages 只读，官方 PTBTokenizer 原本会在 jar 同目录创建临时文件而失败；adapter 只把临时输入移到 `/tmp`，保持 jar、命令参数、token filtering 和 scorer 不变。无临时 `PYTHONPATH` 的四指标 golden 已再次通过。

依赖清单固定在 `intersuit/configs/dynamic_selector_v2_metric_requirements.txt`；运行脚本会校验精确版本，版本漂移时 fail-closed。

判定：

```text
format_verified: true
metric_verified: true
media_verified: false
status: format_verified_metric_verified_with_dependency_gate
```

用途仅限冻结 M4 的 Oracle-vs-All segment captioning。不得把参考 caption 用作 selector query。

## 4. UnAV-100 审查

### 4.1 来源与版本

- GitHub：`https://github.com/ttgeng233/UnAV`
- 审计 commit：`1609deba4fbaf25222c6ee6c7d43b0d0ddfab20d`
- Hugging Face：`https://huggingface.co/datasets/ttgeng233/UnAV-100`
- Dataset Card 许可：CC BY-NC-SA 4.0。

### 4.2 数据格式实审

官方仓库 loader 读取：

```text
database
  video_id
    subset
    duration
    annotations[]
      segment: [start, end]
      label
      label_id
```

官方 Hugging Face parquet 实际字段：

```text
video_id
duration
timestamp: [start, end]
event_label
```

逐条 parquet schema 检查：

| split | 行/事件 | 视频 | 非法记录 |
| --- | ---: | ---: | ---: |
| train | 18,123 | 6,489 | 0 |
| validation | 6,036 | 2,134 | 0 |
| test | 5,900 | 2,167 | 0 |

410 个历史暴露视频位于 N0 审计产物 `historical_overlap_by_dataset.unav100`，不是当前 LongVALE–UnAV 直接交集；后者为 0。使用错误的空字段 `longvale_unav100_overlap_ids` 会导致污染排除失效，正式构建脚本必须读取前者。

去污染并按 `(video_id,event_label)` 合并同标签多 occurrence 后冻结为：

| split | samples | 视频 | 原 annotation | 多 occurrence samples | union ratio `<=10%` |
| --- | ---: | ---: | ---: | ---: | ---: |
| train | 9,948 | 6,229 | 17,477 | 3,921 | 1,380 |
| validation | 3,337 | 2,052 | 5,830 | 1,301 | 443 |
| test | 3,352 | 2,099 | 5,730 | 1,273 | 455 |

同一 sample 的 `target_intervals` 是全部同标签 occurrence 的区间 union。positive 与任一区间正重叠；negative 必须与全部区间都无重叠。旧稀疏计数 7,408/2,448/2,294 是 annotation-level 口径，已删除资格。

本次冻结哈希：

```text
excluded_video_ids_sha256: 3028f5262b42a4550e12df8e8c3601487477d51e27e9b4aef41007c80b3e03c1
label_mapping_sha256: 7d5e3c6f2912d6431509680ed229d2686ade58f2341e787a7e54f9788a52e525
train_manifest_sha256: dfacca2a17e83afad954b20926d5f77d8e4636ba05eb6e1e3c1156e302798b5c
validation_manifest_sha256: 34dc790cd07962d8f34a1c8797942915e8b65875f73a8cc0fb9e33b769eddd84
test_manifest_sha256: 61aaf8e40e5536f68f3dc8efc1387f5df9a50c04c0eae641470f8ba9e42ae9f4
```

parquet 没有 `label_id`。V0 manifest 必须对排序后的唯一 `event_label` 建立稳定 ID 映射，记录映射 SHA，并在 GT 与 prediction 中使用同一映射。

selector 主指标可无歧义地从 timestamp 计算：

- Event Coverage@K；
- Positive-window Recall@K；
- mean best tIoU。

这些主指标不依赖 `label_id`。类别 ID 只用于官方 class-aware temporal detection mAP 辅助指标。

### 4.3 官方指标实审

官方 `ANETdetection`：

- 读取 `segment`、`label_id` 和 prediction score；
- 使用完整 `segment_iou` 与 interpolated AP 实现；
- UnAV dataset config 固定 tIoU 阈值为 0.1–0.9；
- 输出每个 tIoU 的 mAP 和平均 mAP。

官方源码在当前 M4 的 NumPy 中原样运行失败，原因是使用已删除的 `np.float`。仅增加数值等价兼容别名 `np.float = float` 后，使用两个类别、两个视频的完全匹配 golden fixture，九个 tIoU 阈值均得到：

```text
mAP@0.1 ... mAP@0.9: 1.0
average_mAP: 1.0
```

正式适配必须用 `float` 替换弃用别名并保留 golden test。该补丁只能处理类型兼容，不得修改 AP、排序、类别映射或 tIoU 公式。

### 4.4 污染与判定

UnAV 与本项目历史 AVE/Stage2 显式清单重叠 410 个物理视频，三个 split 已完成排除和规模重算。正式产物仍须把排除清单来源、源 parquet SHA 与 manifest SHA 一并冻结；official test 在 V3/V4 结构、checkpoint、阈值和 review 完全冻结前不得运行。

判定：

```text
format_verified: true
metric_verified: true
media_verified: false
status: format_verified_metric_verified_after_compatibility_patch
```

用途仅限 V1 定位上界、V3 event-label query-conditioned selector 训练和 V4 causal replay。官方 mAP 为辅助指标，不能替代 selector 主指标。

## 5. 删除项审查

### 5.1 QVHighlights

删除原因：

- annotation 主要服务视觉 moment retrieval；
- 尚未验证原始媒体音轨存在率与可解码率；
- 即使 query 与 moment schema 可用，也不能证明音频窗口 selector；
- 保留“自然查询辅助席位”会扩大结论但不补齐因果证据。

判定：`removed_from_v2`。

### 5.2 MUSIC-AVQA

已确认 updated annotation 的物理视频严重重叠：

```text
train-test: 6,389
train-validation: 3,808
validation-test: 2,296
```

本项目历史 Stage2 也已触及该数据。它不能形成新的未见 test，也不能作为训练失败后的辅助证明。

判定：`removed_from_v2`。

### 5.3 AVQA

本地官方媒体下载、分片校验、解压完整性、物理 ID 污染审计和官方指标可执行验证均未完成。当前不能确认完整数据与指标链可运行，因此不保留“未来 V5”占位。

判定：`removed_from_v2`。

### 5.4 AVE

AVE D1 已冻结为：

```text
test_failed
rerun_allowed=false
d2_run_allowed=false
```

执行锁 SHA：

```text
ba53930a831060cbcd131be3a2a7b8dc2dda636b3f9ed3649d8f7355997d5866
```

AVE 只保留 All=Fixed、contract 和历史回归，不是 V2 效果候选，不得重跑或改判。

## 6. 数据集组合 Review

LongVALE 与 UnAV-100 当前可解析的官方物理 ID 交集为 0。该结果不替代媒体 SHA、M4-IT 和历史 harness 的后续交叉审计。

| 研究问题 | 唯一数据集 | 当前资格 |
| --- | --- | --- |
| Oracle/确定性定位上界 | UnAV-100 | 完整 V1 PASS；443 samples / 335 physical videos |
| 冻结 M4 Oracle 下游效用 | LongVALE | 数据和依赖资格通过；授权全冻结 `oracle_pilot` |
| event-label query-conditioned selector 训练 | UnAV-100 | 代码 contract 已建立；等待 V2 confirm 授权 |
| strict-causal replay 与一次性 test | UnAV-100 | 未启动；等待 V3 通过 |

这套组合能回答“正确音频选窗是否有用”和“受限 event label 条件下能否学会定位”，不能回答开放域自然问题、正式音视频 QA 转化或 Semantic Contrast 的收益。

许可结论：LongVALE 与 UnAV-100 均为 CC BY-NC-SA 4.0，V2 仅批准学术研究用途。YouTube/第三方媒体仍受原始内容权利约束；不得把该结论解释为允许商业训练或重新分发媒体。

## 7. 下一步与总门禁

严格按以下顺序执行：

已完成基础项：

1. LongVALE/UnAV 官方 commit、annotation SHA、物理 ID 审计已固定；
2. LongVALE 四指标与 UnAV 九阈值 mAP golden fixture 已进入代码并用官方实现实际通过；
3. UnAV 410 个历史视频已排除，sample unit、多 occurrence union 与新规模已冻结；
4. 1 秒 tail/hop contract、可训练 question-conditioned scorer、K=8 causal memory 和 checkpoint lock 已实现。

历史风险已经冻结：

1. metric 依赖已固定，官方 golden 通过；
2. 锁定 semantic-contrast checkpoint 的 dev760 selectivity 正式 FAIL；
3. Fixed Stage2 checkpoint 同样 FAIL；
4. 12k/32k M4 baseline 不含 scene-audio/projector/fusion 权重，不能替代；
5. 当前 checkpoint 只能用于诊断，不具备正式音频路径资格。

下一步严格运行 LongVALE `oracle_pilot`：全部模型模块冻结，只改变音频窗口集合，比较
text-only、All、Uniform、Energy、Onset、cached Eventness、Random、Oracle 和
Oracle-mismatch。Temporal scorer 只有独立资格通过后才加入，否则明确记录 `not_qualified`。
pilot 失败立即停止 selector 并转向音频语义目标、答案表示和融合位置；pilot 通过才解封
confirm，confirm 通过才训练 question-conditioned selector。

任何一个保留数据集若无法通过依赖 golden test、媒体 pilot、污染排除或许可冻结，立即从 V2 删除，并重新 review 整体计划；不得临时启用已经删除的数据集补位。

当前批准 V2 所需的受控媒体 preflight 和全冻结 Oracle pilot；不批准 selector 训练、联合训练、
正式 test、教师扩展训练或继续 Semantic Contrast 调参。
