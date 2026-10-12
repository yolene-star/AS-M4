# Dynamic Selector V2 S1 Metadata 与媒体 Pilot 冻结 Review

日期：2026-07-25（Asia/Shanghai）

## 1. 结论

```text
metadata_oracle_headroom: PASS
unav_media_pilot_qualification: PASS
energy_onset_pilot_signal: PASS
full_v1: NOT_PASSED
learned_selector_training: NOT_AUTHORIZED
official_test: FORBIDDEN
```

本轮证明了两件事：UnAV-100 的稀疏事件存在很大的 Oracle 定位上界；真实媒体上不训练的
Energy/Onset 规则能够在 32-video pilot 中显著优于 Random。它没有证明完整 validation 的
确定性门禁已经通过，也没有证明正确选窗能改善冻结 M4 下游输出。

## 2. 官方数据仓库审查

Hugging Face 官方数据集为 `ttgeng233/UnAV-100`，Dataset Viewer 状态中 preview、viewer、
search、filter、statistics 全部可用。唯一 config 为 `UnAV-100`，split 为 train/val/test；
行数分别为 18,123、6,036、5,900，字段为：

```text
video_id: string
duration: float64
timestamp: list[float64]，固定长度 2
event_label: string
```

Hub 主分支还包含 13 个 `unav_raw_videos.tar.gz.part_*`，总计 65,775,604,313 bytes
（约 61.26 GiB）。该整包是原视频，不是 Dataset Viewer 报告的 555,820-byte parquet 数据。
仓库未包含 `download_video.csv`，但论文作者公开 Google Drive 目录中存在该文件：

```text
Google Drive file id: 1yHg-QZJ-DlL3K2I52ixtjGX4rJRa9Oc3
bytes: 294050
rows: 10790
sha256: f1f9b36e607ed0cea04e818cf23027acd1cec4cdd9b7c118c93408e5654be735
schema: video_id, source_start_sec, source_end_sec, split
```

对去污染后的 official validation manifest 做全量交叉核验：

- manifest 3,337 samples；
- `download_video.csv` 缺失 video_id：0；
- split 错配：0；
- `source_end-source_start` 与 manifest duration 的 0.05 秒容差错配：0；
- 稀疏子集：443 samples / 335 physical videos / 66 labels。

因此数据格式、时间坐标和媒体源定义可用于定位评测，不删除 UnAV-100。

## 3. Metadata-only 上界

冻结配置：1 秒 window、1 秒 hop、partial tail timestamp 截断、K=8、Random 五 seed、物理视频
cluster paired bootstrap 10,000 次、seed `20260724`。

Validation 结果：

| 策略 | Event Coverage |
| --- | ---: |
| All | 1.000000 |
| Oracle Top-K | 0.999802 |
| Random Top-K 五 seed 均值 | 0.178958 |
| Uniform Top-K | 0.180667 |

Oracle−Random 为 `+0.820843`，95% CI `[0.808502,0.832940]`。两次独立运行的全部
per-sample、report 和 summary SHA 完全一致。该结果通过 Oracle headroom 子门禁，但不包含
Energy/Eventness，不等于完整 V1。

## 4. 媒体 Pilot 构造与审计

候选只来自去污染后的 official validation 稀疏事件，不读取 test。冻结 48 个物理视频：

- short `<30s`、medium `30–50s`、long `>=50s` 各 16；
- single-label/single-occurrence 与 multi-label-or-occurrence 各 24；
- 82 samples，覆盖 54 个 label；
- 六个 strata round-robin，优先覆盖未见 label，最终用固定 SHA tie-break；
- allowlist SHA：`2f770d288fe86c4c02514408368ceb5c4f4326dac571b6bcf4486a85bcd5f33f`。

可用性探测为 47/48。真实下载后不放宽 0.25 秒时长门禁，按冻结顺序补位：共审计 35 个，
32 个严格合格，3 个因相对官方 duration 分别短 0.306、0.376、0.296 秒而拒绝。合格媒体均
包含视频流和音频流，并经 ffmpeg 完整解码到 16 kHz 单声道 PCM。媒体只保存在 `/tmp`
quarantine，未加入 Git 或正式数据目录。

## 5. Energy/Onset Pilot

冻结实现：

- Energy：每个 1 秒窗口的 `log(mean(waveform^2)+1e-12)`；
- Onset：16 kHz 单声道，400-sample Hann frame、160-sample hop，正向 log-spectral flux，
  每个 1 秒窗口取 frame mean；
- 分数降序、原始 window index 升序 tie-break；
- 同一物理视频的 waveform score 对该视频不同 event-label sample 共用；
- 不读取问题、答案、test、M4 权重或事件 timestamp 来生成 Energy/Onset 分数。

32 videos / 60 sparse events / K=8：

| 策略 | Event Coverage | Positive-window Recall | mean best tIoU |
| --- | ---: | ---: | ---: |
| Random 五 seed均值 | 0.206742 | — | — |
| Uniform | 0.206808 | 0.227083 | 0.172047 |
| Energy | 0.347220 | 0.330179 | 0.234259 |
| Onset | 0.421291 | 0.398492 | 0.262100 |
| Oracle | 1.000000 | 1.000000 | 0.508821 |

- Energy−Random：`+0.140479`，95% CI `[0.060197,0.222821]`，胜 5/5 seed；
- Onset−Random：`+0.214550`，95% CI `[0.126299,0.304241]`，胜 5/5 seed；
- Oracle−Random：`+0.793258`，95% CI `[0.738269,0.840398]`；
- Uniform−Random：`+0.000066`，95% CI `[-0.062359,0.064807]`。

最终代码冻结后，metadata 两次运行的 summary SHA 均为
`33489ae7fd4ba76c2218818fb3b233a5141ceea4097d735cf7c4e0c6aee87eb4`；media evaluator 两次运行的
summary SHA 均为 `267d64cdde37911d466ca2c7295fef8a9955b31fb9de96678788dea61a0fa912`，media audit SHA 均为
`6075370d213dbb2d1fe4fb4a06c4e857392d45919d647ed87acfebc4ed279e74`。

## 6. Bug Review

本轮发现并修复：

1. metadata runner 原本可误收 test manifest；现对 test 硬失败；
2. 重复 `sample_id` 原本会令策略汇总与 bootstrap 使用不同口径；现对重复 ID 硬失败；
3. manifest 混合 split、缺字段、空输入原本未统一验证；现均硬失败；
4. 下载补位原本会重复访问已下载文件；现复用非空 quarantine 文件，最终资格仍由独立 audit
   决定；
5. 媒体 evaluator 原本在首 32 个文件中出现 duration reject 时直接停止，无法按冻结顺序补位；
   现保持 0.25 秒门禁并继续后续候选，直到获得 32 个严格合格媒体；
6. `torchaudio.load(mp4)` 在当前 M4 环境无可用 backend；现固定使用项目已有 ffmpeg PCM 解码，
   不安装依赖、不换 Python 环境。

定位、数据、媒体与指标相关测试共 24 项通过；`git diff --check` 通过。

## 7. 下一步决策

继续 selector-first 主线，但仍不训练 selector：

1. 使用同一 `download_video.csv` 和冻结 manifest 扩展 335 个稀疏 validation 物理视频；
2. 保持 0.25 秒时长、音视频流、完整解码和 SHA 审计，不足时按预先冻结顺序补位；
3. 在完整稀疏 validation 运行 All/Uniform/Random/Energy/Onset/Oracle，并双次复现；
4. 独立审查并冻结 `eventness_cached_topk` 资产；没有合格资产就明确记录缺项，不得伪造；
5. 完整 V1 满足计划门禁后，才处理 V2 的冻结音频路径 blocker；
6. learned selector、M4 联合训练、Semantic Contrast 和 UnAV test 继续禁止。

Pilot 采用标签覆盖优先的分层抽样，可能高估规则在完整 validation 上的效果。因此当前最合理的
决策是“扩大验证”，不是“开始训练”。
