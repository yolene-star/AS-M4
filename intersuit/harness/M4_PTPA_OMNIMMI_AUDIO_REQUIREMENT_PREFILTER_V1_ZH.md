# OmniMMI PT/PA 音频依赖盲审预筛 V1

日期：2026-09-10（Asia/Shanghai）

状态：`PENDING_HUMAN_MODALITY_REVIEW`

## 1. 范围

本轮只读取 OmniMMI 原始 `proactive_turntaking.json` 和 `proactive_alerting.json`，没有读取任何模型预测，也没有启动 GPU。自动规则仅从问题/答案文字中寻找听觉和视觉线索，生成待人工复核候选；它不是正式模态标签。

输入资产：

- PT SHA256：`e79fb40e7ef66e7d6821a241aab67f992ea83e08a7191ce6759f79c562b08433`；
- PA SHA256：`2fc0944f3c4a6ba8b7297788b9dc8728977aa4bbe82e82e7c5eb7d3e14536366`；
- PT：200 个视频样本、672 个带答案 QA turn；
- PA：200 个视频样本、200 个时间段问题；
- 合计：872 个问题级 review rows。

## 2. 词边界修复

首轮简单子串扫描会把 `using` 中的 `sing`、`wearing` 中的 `ring` 和 `basket` 中的 `ask` 错判为音频线索。该版本已废弃。V1 改用英文词边界，并增加定向回归测试；重新生成后的候选 manifest SHA 为：

```text
931bf89d4d9b0cc8731f409bd5c80f40a7afc3e268a62d22e060fcd20887bf6d
```

## 3. 自动候选分布

| 任务 | Audio-required 候选 | Audio-Visual Joint 候选 | Visual-only 候选 | Ambiguous 候选 |
| --- | ---: | ---: | ---: | ---: |
| PT | 9 | 9 | 283 | 371 |
| PA | 14 | 19 | 108 | 59 |
| 合计 | 23 | 28 | 391 | 430 |

## 4. 必须人工复核的原因

- `talking`、`crying`、`ring` 或 `alarm` 出现在文字里，不等于答案必须听音频；例如“穿什么颜色”即使描述中有人在说话，仍可能是 Visual-only；
- PA 的“某人在说话”可能仅凭嘴型和人物位置定位，也可能必须依赖语音内容，必须查看原媒体；
- PT 的最终 turn-taking 决策与其历史 QA 内容不是同一个模态问题，不能把 PT 全部 QA 的文字线索直接等同于 turn-taking 音频依赖；
- `Sound engineer`、`a ring` 等词可能是职业或物体，不是听觉证据；
- 正式 Audio-required 必须满足“去掉音频后，问题所需的关键事实无法从画面和题面可靠获得”。

## 5. 人工 review 规则

每条记录只能选择：

- `audio_required`：语音内容、声音类别、声源或听觉时序是必要证据；
- `visual_only`：画面足以回答，音频不应改变结论；
- `audio_visual_joint`：声音与画面缺一不可；
- `ambiguous_excluded`：仅靠题面和媒体仍无法稳定归类，不进入主子集。

复核时必须填写 `review_status=approved/rejected`、`final_modality` 和 `review_note`。正式 manifest 只从审核通过项生成，并在任何新预测前冻结 SHA。

## 6. 四条件合同状态

配置 `intersuit/configs/omnimmi_m4_four_condition_contract_v1.json` 已固定：

- `correct_audio / mismatch_audio / video_only / silence`；
- 原始 M4 绝对参考与 AS-M4 同权重增量两层对比；
- 必须拆分 Audio-required、Visual-only、Audio-Visual Joint 和 Overall；
- PT、PA、turn-taking 保真必须独立报告；
- GPU smoke、canary、full evaluation、150-step、正式训练和新增结构全部关闭。

当前合同仍为 `PENDING_MODALITY_REVIEW_AND_MANIFEST_FREEZE`。人工 review、音轨存在性、mismatch 一一映射、媒体 SHA 和最终 manifest 尚未完成前，不得解锁 GPU。

## 7. 下一步

先复核 51 条带显式音频线索的 Audio-required/Joint 候选，再从 Visual-only 候选按 PT/PA 和媒体分层抽取安全控制集。复核通过后才生成最终三类 manifest 和四条件展开；歧义项不用于凑数量。

## 8. 媒体前置审计

51 条显式音频线索候选涉及 47 个物理视频。文件存在性和视频流检查全部通过；其中 `ego_34.mp4` 没有音轨，对应 `pt:60:1` 必须从 Audio-required/Joint 正式候选中排除。其余 50 条、46 个有音轨视频进入人工听看复核。

审计状态为 `PASS_CANDIDATE_MEDIA_PREFLIGHT_WITH_EXCLUSIONS`。该排除已写入四条件合同，不允许后续用 silence、外部音频或其他视频音轨补造正确音频。

## 9. 人工复核工作包

已生成 `intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/manual_review_packet.jsonl`：

- 50 条有音轨 Audio/Joint 候选：PT 17、PA 33；
- 40 条 Visual-only 控制：PT 20、PA 20，按稳定 SHA256(review_id) 抽样；
- 共 90 条，均保持 `final_modality=null`、`review_status=pending_human_review`；
- 音频候选和 39 条合格 Visual-only 控制已有媒体 SHA/音轨审计；`pt:185:8` 因无音轨排除，不能当作四条件控制。
- 控制集审计另发现 `ego_63.mp4` 无音轨，对应 `pt:185:8` 已加入排除清单；因此四条件控制候选当前为 39 条，剩余 39 条均通过文件、视频流和音轨前置审计。

当前仍不能生成正式 `audio_required / visual_only / audio_visual_joint` manifest，也不能冻结 mismatch 映射。
