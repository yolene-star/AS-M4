# 音频语义重构 R0–R2 执行 Review

日期：2026-07-27（Asia/Shanghai）

## 1. 最终结论

本轮严格执行
`docs/AUDIO_SEMANTIC_RECONSTRUCTION_DIAGNOSTIC_PLAN_ZH.md`，在 R2 首个失败门禁停止：

```text
R0 contract: PASS
R1 raw BEATs pilot300: PASS
R1 raw BEATs expanded train/validation: PASS
R2 legal semantic target: FAIL
R3 projector-only training: NOT_AUTHORIZED
R4 fusion: NOT_AUTHORIZED
R5 downstream causal gate: NOT_AUTHORIZED
Selector V2 resume: NOT_AUTHORIZED
formal test: NOT_ACCESSED
```

结论不是“BEATs 没有音频事件语义”，而是“当前冻结 scene-audio projector 输出与所测
Qwen 文本目标空间不对齐”。R1 与 R2 把这两种故障明确分开了。

## 2. R0 数据与执行契约

R0 冻结内容：

- AVE_HF_EXPANDED：1000 个唯一物理视频；
- 内部诊断 split：608 train、196 validation、196 test_locked；
- 原 300 条 pilot：203 reference、97 query；
- 28 个 Hugging Face `ClassLabel` 与 AVE 官方 `Annotations.txt` 逐条一致；
- pilot 与 expanded 物理 ID 重叠为 0，各 split 物理 ID 重叠为 0；
- 所有样本固定 `supervision_level=clip`、`audio_required=true`；
- 正式窗口固定 16 kHz、1.0 秒 window、1.0 秒 hop、尾窗右侧补零，并保存
  `valid_sample_count`；
- `test_locked` 只冻结 manifest，从未进入特征输入或指标计算。

R0 decision SHA256：
`55ad63a4f8b29154537a5a7a0d007da0884346877e2570b844e183e730442e25`。

## 3. R1 冻结表示诊断

### 3.1 300 条正式 pilot

使用锁定 BEATs checkpoint：

```text
checkpoint: intersuit/checkpoints/BEATs_iter3_plus_AS2M.pt
SHA256: d43cbfad4d7b56381c061d7a24774f908d4d94c72961f6eb1d9090ff18cd8d34
```

特征 manifest 共 300 个唯一视频、3294 个 1 秒窗口、300 个有精确
`valid_sample_count` 的尾窗，坏张量为 0。

主结果：

| 指标 | raw BEATs |
| --- | ---: |
| macro Recall@1 | 71.1111% |
| accuracy / rank-first | 75.0000% |
| margin mean | 0.025678 |
| margin 95% CI 下界 | 0.015702 |
| 高于随机基线的合格类别比例 | 93.3333% |

五项 R1 门禁全部通过。

### 3.2 扩展 train/validation

只为 608 train + 196 validation 提取同契约特征，196 个 `test_locked` 样本没有进入输入。
共 804 个唯一视频、8831 个窗口、804 个尾窗，坏张量为 0。

| 表示 | macro Recall@1 | rank-first | margin CI 下界 |
| --- | ---: | ---: | ---: |
| raw BEATs clip mean | 72.4490% | 72.4490% | 0.021355 |
| raw BEATs RMS Top-5 | 72.4490% | 72.4490% | 0.025912 |
| 当前 scene-audio projector | 72.9592% | 72.9592% | 0.008205 |
| 固定随机线性 projector | 70.4082% | 70.4082% | 0.014742 |

raw BEATs 的 28/28 类 Recall@1 均高于 `1/28` 随机基线，R1 全部门禁通过。当前
scene-audio projector 后类别质心检索仍为 72.9592%，所以不能把后续失败解释为
“projector 已完全抹除类别结构”。

扩展 R1 decision SHA256：
`24152b1fd6e9b628df530779a779de98d051c56b46dd8dfddfaa6b3531cfb283`。

## 4. R2 文本目标诊断

R2 保持 BEATs、当前 scene-audio projector 和 Qwen input embedding 全部冻结。音频表示仍是
每个物理视频一个按 `valid_sample_count` 加权的 clip mean。文本目标在运行前固定为：

1. `old_answer_token_mean`：精确复现训练预处理的单条 assistant answer token 序列，
   包含 assistant chat envelope 与结束 token；
2. `canonical_event_label`：仅 canonical label 的无特殊 token 输入 embedding 均值；
3. `question_plus_canonical_answer`：固定音频问题模板加 canonical answer；
4. `label_description_prototype`：只使用 train canonical label 表生成的固定描述模板；
5. `answer_choice_text`：AVE clip-level 集合没有多选题，明确为 `NOT_APPLICABLE`。

196 条 validation 的一次性结果：

| 目标 | rank-first | margin CI 下界 | 相对旧目标 | 合法目标 |
| --- | ---: | ---: | ---: | --- |
| old assistant-answer token | 9.6939% | -0.009996 | - | 否 |
| canonical event label | 8.6735% | -0.023715 | -1.0204pp | 是 |
| question + canonical answer | 5.1020% | -0.013216 | -4.5918pp | 是 |
| label-description prototype | 5.1020% | -0.016286 | -4.5918pp | 是 |

R2 要求至少一个合法目标同时满足：

```text
rank-first >= 0.65
margin 95% CI lower bound > 0
relative to old target >= +10pp
```

通过目标数为 0。R2 decision SHA256：
`15b7e74cac13cf49c924d2d1f9f29cb56a4b5ebad9d628cb355d6f90b7fa36c8`。

## 5. 资源、复现与安全边界

- 所有 Python 命令均显式使用 Conda `M4`；
- BEATs 特征只使用物理 GPU 1，`CUDA_VISIBLE_DEVICES=1`；
- 提取前 GPU 1 为 11 MiB、0% utilization、450W，preflight 通过；
- 提取结束后五张 GPU 均回到 11 MiB、0% utilization；
- 内核日志未发现 `NVRM`、`Xid`、`AER`、`PCIe Bus Error`、`GPU has fallen`
  或 `hung task`；
- R0–R2 没有训练参数，没有访问 AVE official test；
- Git 只提交脚本、测试和中文文档；媒体、特征、checkpoint、日志与运行 artifact 均未提交。

R0/R1 实现提交：
`21f0cc5b90dadcb7ed477d64470fd2050c48e2ee`。

## 6. 停止决定

按照计划第 8 节，R2 FAIL 后立即执行以下限制：

```text
projector_training_allowed: false
fusion_changes_allowed: false
gate_sweep_allowed: false
selector_training_allowed: false
longvale_oracle_utility_allowed: false
formal_test_allowed: false
```

允许的后续工作只有：重新设计并资格审查音频语义目标，例如引入独立、冻结、已证明具备
音频文本对齐能力的 encoder；随后更新计划并重新执行 R2。不得用当前 R1 的高分类准确率
越过 R2，也不得通过直接训练 projector、放大 gate 或修改 fusion 来追认本轮通过。
