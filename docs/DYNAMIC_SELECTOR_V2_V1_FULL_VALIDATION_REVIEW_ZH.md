# Dynamic Selector V2 V1 完整 Validation 冻结 Review

日期：2026-07-27（Asia/Shanghai）

## 1. 结论

```text
v1_metadata_headroom: PASS
v1_media_pilot: PASS
v1_full_validation_media: PASS_WITH_OFFICIAL_PACKAGING_DURATION_DIFFERENCES
v1_eventness_cached_asset: PASS
v1_full_validation_k8: PASS
v1_full_validation_k4_supplement: PASS
v1_double_reproduction: PASS
selector_training_allowed: false
historical_v2_oracle_utility_allowed: false
historical_current_route: conditional_audio_semantic_blocker_remediation
```

完整 V1 定位上界与确定性策略门禁通过。该结果只证明固定容量音频窗口选择在
UnAV-100 稀疏 validation 上存在明确定位 headroom，并证明 Energy、Onset 与冻结
cached Eventness 均显著优于 Random；不证明冻结 M4 下游语义收益，也不授权 learned
selector、联合训练或 official test。

### 1.1 2026-08-03 主线授权覆盖

用户再次确认 selector-first 顺序后，本 review 的实验结果与 SHA 全部保持不变，但第 7 节
“先修复音频路径才可运行 V2”的执行授权被以下最新决策覆盖：

```text
v2_oracle_pilot_allowed: true
v2_oracle_confirm_allowed: false
selector_training_allowed: false
audio_semantic_teacher_expansion_allowed: false
semantic_contrast_allowed: false
```

当前 checkpoint 的 selectivity FAIL 必须作为 V2 风险完整披露，并通过 Oracle-mismatch、
All、Random、text-only 和输出退化条件共同判定。pilot 失败立即停止 selector 并转向音频
语义目标、答案表示和融合位置；pilot 通过才解封 confirm，confirm 通过才训练 selector。

## 2. 官方媒体资格

- Hugging Face revision：
  `daeb88bc14eeff48f8a4ac2836b67f6c350f847b`；
- 13 个官方分片共 `65,775,604,313` bytes，逐分片 SHA256 全部通过；
- 跨分片 tar/gzip 完整读取通过，共 10,791 个成员，其中 10,790 个 MP4；
- 完整稀疏 validation 为 443 samples / 335 physical videos；
- 335/335 官方 MP4 均包含 H.264 视频与 AAC 音频，完整 16 kHz 单声道解码通过；
- 9 个视频的官方 MP4 容器时长与官方标注/`download_video.csv` 相差
  `0.253–0.517` 秒。

这 9 项不是模型输出。`expected_duration` 同时来自官方 Hugging Face 标注和作者官方
`download_video.csv`，二者逐项一致；`actual_duration` 来自同一官方 Hugging Face
归档 MP4 的 `ffprobe`。用户于 2026-07-27 明确确认官方资产内部的封装时长差异不作为
阻断。执行仍要求：

- 官方 CSV SHA256 固定为
  `f1f9b36e607ed0cea04e818cf23027acd1cec4cdd9b7c118c93408e5654be735`；
- validation manifest SHA256 固定为
  `34dc790cd07962d8f34a1c8797942915e8b65875f73a8cc0fb9e33b769eddd84`；
- 所有目标事件必须落在实际可解码媒体范围内；
- 该例外不得迁移到其他来源、split、manifest 或媒体文件。

最终媒体审计状态为
`PASS_WITH_OFFICIAL_PACKAGING_DURATION_DIFFERENCES`，warning 9、reject 0。

## 3. 冻结 Eventness 资产

旧 AVE Eventness 缓存没有复用。当前资产只从 335 个合格 UnAV validation 视频重新生成：

- 物理 GPU 1，显式 `CUDA_VISIBLE_DEVICES=1`；
- M4 环境，PyTorch `2.5.1+cu121`；
- BEATs checkpoint SHA256：
  `d43cbfad4d7b56381c061d7a24774f908d4d94c72961f6eb1d9090ff18cd8d34`；
- 1 秒 window、1 秒 hop、partial tail padding；
- 14,814 个窗口，embedding dim 768；
- 335/335 All=Fixed 精确一致；
- test accessed：`false`；
- cached Eventness 不节省 BEATs，只作为已编码特征 baseline。

1-video、8-video 和 335-video 正式提取均通过。正式 feature manifest SHA256 为：

```text
c4d574535adac9beb22836b37499deb63b509d06d76edda072eedccdf2907493
```

## 4. K=8 主结果

主集合：443 samples / 335 videos。Random 为五个预注册 seed 的均值。

| 策略 | Event Coverage | Positive-window Recall | mean best tIoU |
| --- | ---: | ---: | ---: |
| All | 1.000000 | 1.000000 | 0.507910 |
| Uniform | 0.180667 | 0.175871 | 0.161083 |
| Random mean | 0.178958 | — | — |
| Energy | 0.298830 | 0.281106 | 0.200019 |
| Onset | 0.315874 | 0.290012 | 0.208692 |
| Eventness cached | 0.319828 | 0.298202 | 0.233469 |
| Oracle | 0.999802 | 0.998846 | 0.507910 |

物理视频 cluster paired bootstrap，10,000 次，seed `20260724`：

| 比较 | delta | 95% CI | 胜过 Random seed |
| --- | ---: | ---: | ---: |
| Oracle−Random | +82.0843pp | [+80.8502,+83.2940]pp | 5/5 |
| Energy−Random | +11.9871pp | [+8.4720,+15.4950]pp | 5/5 |
| Onset−Random | +13.6916pp | [+10.0849,+17.3703]pp | 5/5 |
| Eventness−Random | +14.0870pp | [+10.8184,+17.4406]pp | 5/5 |
| Uniform−Random | +0.1708pp | [−2.3513,+2.7657]pp | 2/5 |

Oracle 与至少一个确定性策略均通过 V1 门禁。Eventness 是三种确定性策略中 Event
Coverage 最高者，但当前结果不授权在下游阶段事后删除 Energy 或 Onset 对照。

## 5. K=4 补充

| 策略 | Event Coverage | 相对 Random |
| --- | ---: | ---: |
| Random mean | 0.084379 | — |
| Uniform | 0.115177 | +3.0798pp |
| Energy | 0.202441 | +11.8062pp |
| Onset | 0.199674 | +11.5295pp |
| Eventness cached | 0.202994 | +11.8615pp |
| Oracle | 0.967883 | +88.3504pp |

K=4 只作成本曲线补充，不参与 K=8 主门禁。

## 6. 复现与计算审计

- K=8 两次输出目录逐文件一致，summary SHA256 均为
  `89062ed93a750d572ce70e0b4d6c915c47394d2cd4495a6d7653f00b10236abb`；
- K=4 两次输出目录逐文件一致，summary SHA256 均为
  `7f0292c0e92d52b097b414b846195e8c75211a205a95e2d80e3c02cefdc2d340`；
- 同一 335-video 集合上的完整分布补充为 924 samples，不外推到未解包 validation
  视频；
- 解码音频 `14,761.67` 秒；
- 1 秒 micro-window 14,814 个；
- Eventness 离线 BEATs 编码 14,814 个窗口；
- 正式定位评估阶段 BEATs/projector/fusion forward 均为 0；
- 没有加载 M4 或创建 optimizer，不存在模型参数更新。

## 7. 决策

V1 完整通过后，决策树进入“冻结音频路径资格检查”。当前锁定的
`checkpoint-150` 已有冻结失败结论：

```text
audio_path_selectivity_gate_status: failed
historical_v2_oracle_utility_allowed: false
```

以下是 2026-07-27 的历史授权，已由第 1.1 节覆盖。当时要求先进入音频语义 blocker 修复，
并在新的 checkpoint 通过 `correct > mismatch` 且 `correct >= video-only` 之前：

- 禁止运行 LongVALE V2 Oracle utility；
- 禁止训练 question-conditioned selector；
- 禁止接入 M4 联合训练；
- 禁止访问 UnAV official test；
- 禁止启动 Semantic Contrast。

最新有效动作是全冻结 LongVALE V2 `oracle_pilot`；其余四项禁止仍然有效。
