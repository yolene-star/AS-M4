# UnAV-100 机制验证证据与缺失项清单

日期：2026-09-10（Asia/Shanghai）

状态：`PASS_INVENTORY_READY_FOR_UNIFIED_EVALUATOR`

## 1. 固定范围

- 用途：只证明场景音频表示、事件时间定位和选窗机制；
- 数据：UnAV-100 官方 validation，443 个事件样本、335 个物理视频；
- 窗口：1 秒、不重叠、尾窗补齐并将结束时间钳制到媒体时长；
- 主体：BEATs、projector、M4/Qwen2、视觉塔和融合均冻结；
- 禁止：联合训练、150-step M4 训练、official test、用旧 AVE Temporal 资产静默替代；
- Random seeds：`20260720`、`20260721`、`20260722`、`20260723`、`20260724`；
- bootstrap：按物理视频 cluster，10,000 次，seed `20260724`。

## 2. 六策略状态表

| 策略 | K=4 | K=8 | 证据状态 | 进入统一 evaluator 前动作 |
| --- | --- | --- | --- | --- |
| Random | 已完成 | 已完成 | `FROZEN_REFERENCE` | 保留五个逐 seed 结果，不只保留均值 |
| Energy | 已完成 | 已完成 | `FROZEN_RULE_BASELINE` | 复用冻结结果并补实际窗口/token/延迟 |
| Eventness | 已完成 | 已完成 | `FROZEN_CACHED_BASELINE` | 明确其已编码全部 BEATs，不宣称节省 BEATs |
| Temporal | 缺失 | 缺失 | `MISSING_SCHEMA_COMPATIBLE_ASSET` | 生成 UnAV 1 秒窗口同 schema 的冻结时间分数；旧 AVE bundle 禁止复用 |
| Oracle | 已完成 | 已完成 | `FROZEN_NONCAUSAL_UPPER_BOUND` | 保持只作上界，不解释为可部署能力 |
| learned selector | 缺失 | 缺失 | `NOT_EVALUATED_NOT_AUTHORIZED` | 先冻结 train/validation、参数白名单、早停和 SHA；只允许 selector-only |

补充策略 `Uniform` 和 `Onset` 已有结果，统一表中继续报告，但不替代六项主矩阵。

## 3. 已有定位结果

### K=8 主结果

| 策略 | Event Coverage | Positive-window Recall | mean best tIoU | 相对 Random Event Coverage |
| --- | ---: | ---: | ---: | ---: |
| Random mean | 0.178958 | 未在冻结 review 中单列 | 未在冻结 review 中单列 | — |
| Energy | 0.298830 | 0.281106 | 0.200019 | +11.9871pp |
| Eventness | 0.319828 | 0.298202 | 0.233469 | +14.0870pp |
| Oracle | 0.999802 | 0.998846 | 0.507910 | +82.0843pp |

Energy、Eventness、Oracle 相对 Random 的物理视频 cluster bootstrap 95% CI 下界均大于 0，并均胜过 5/5 Random seeds。

### K=4 补充结果

| 策略 | Event Coverage | 相对 Random |
| --- | ---: | ---: |
| Random mean | 0.084179 | — |
| Energy | 0.202441 | +11.8062pp |
| Eventness | 0.202994 | +11.8615pp |
| Oracle | 0.967883 | +88.3504pp |

## 4. 冻结资产

- K=8 双重复现 summary SHA：`89062ed93a750d572ce70e0b4d6c915c47394d2cd4495a6d7653f00b10236abb`；
- K=4 双重复现 summary SHA：`7f0292c0e92d52b097b414b846195e8c75211a205a95e2d80e3c02cefdc2d340`；
- UnAV validation feature manifest SHA：`c4d574535adac9beb22836b37499deb63b509d06d76edda072eedccdf2907493`；
- BEATs checkpoint SHA：`d43cbfad4d7b56381c061d7a24774f908d4d94c72961f6eb1d9090ff18cd8d34`；
- 全量 S1-R 冻结特征：7,941 条记录、334,769 个窗口，状态 `PASS_FROZEN_BEATS_FEATURE_EXTRACTION`；
- 当前 review 文件 SHA：`8080eeb9f0dbbe95e6f3b510a1bb4f872e54c5d5fdb6cd80663befe31a5a8af7`。

## 5. 不可混用的历史资产

- 历史 AVE Temporal scorer 使用 20 个长度 1 秒、hop 0.5 秒的重叠窗口，与本次 UnAV 1 秒不重叠 micro-window 不兼容；
- 该 scorer 还依赖 BEATs、CLIP、RGB 和 scalar 多路输入，不能在只有 BEATs 缓存时零填充；
- `unav_s1r_temporal_projector_v2` 的 300-step 训练状态为 `PASS_TEMPORAL_PROTOTYPE_PROJECTOR_TRAINING`，但它是音频语义 projector，不是六策略矩阵中的 Temporal selector 成绩；
- 历史 question-conditioned selector 仍为 `NOT_AUTHORIZED`，没有可作为 learned selector 正式结果的冻结 checkpoint。

## 6. 统一 evaluator 必须补齐的字段

每个策略、每个 K/预算必须输出：

- Event Coverage@K；
- Window Hit@K；
- Positive-window Recall@K；
- first-hit rank；
- mean best tIoU；
- 相对 Random 五 seed 的 paired delta 与 cluster bootstrap 95% CI；
- Oracle headroom：`oracle - random_mean`；
- headroom recovery：`(selector - random_mean) / (oracle - random_mean)`；
- 实际选中窗口数、音频 token、selector 延迟、峰值显存；
- 按视频时长、事件占比、单/多事件分层结果。

## 7. 当前结论

现有证据已经足以证明：UnAV-100 上存在很大的 Oracle 定位上界，Energy 和 cached Eventness 也显著优于 Random，因此“音频窗口包含可定位事件信息、规则选窗有效”已获得机制级证据。

尚不能证明：Temporal 有效、learned selector 优于规则策略、选窗能改善 OmniMMI/M4 最终任务，或 cached Eventness 能节省 BEATs 计算。这些必须分别补证，不能由现有结果外推。
