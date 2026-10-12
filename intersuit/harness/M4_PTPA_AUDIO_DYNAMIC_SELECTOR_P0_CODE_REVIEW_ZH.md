# M4 Dynamic Selector P0 代码 Bug Review 报告

日期：2026-08-11（Asia/Shanghai）

目标模型：`M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1`

审查范围：正式计划中 B-001 至 B-006 六个代码 blocker。

结论：**P0 CPU/合同级 Review 通过；六个代码 blocker 已关闭。尚未授权 P1、GPU full、Oracle pilot 或 selector training。**

## 1. B-001：统一 selection contract 与 registry

状态：已关闭。

实现：

- 新增不可变 `SelectionRequest` 和 `SelectionResult`；
- 新增显式 `SelectionPolicyRegistry`；
- external policy 包含 `fixed_all`、`external_indices`、`uniform_topk`、`energy_topk`、`eventness_topk`、`temporal_highconf_topk`、`random_topk`、`oracle_topk`；
- 现有模型内部 Micro/Question-conditioned selector 输出通过 adapter 转成同一 `SelectionResult` 审计结构；
- 未知策略、重复注册、空选择、重复索引、越界、非有限数值、时间逆序和 shape 不一致全部 fail-closed。

Review 中额外发现并修复：selection 控制证据如果保留在 CUDA、排序 index 在 CPU，会触发设备不一致。现在索引、timestamp、score、target interval 在合同入口统一 detach 到 CPU；原始音频仍保持原设备，只在最终 index-select 时迁移位置索引。

## 2. B-002：external Top-K 二次选择

状态：已关闭。

实现：

- runner 完成外部选择后显式传递 `scene_audio_selection_stage=external_pre_encoder`；
- Qwen forward/generate/parallel/stream 路径全部透传；
- `llava_arch` 使用局部 effective mode `fixed`，不会修改 `model.config.scene_audio_window_mode`；
- internal selector 被旁路，当前 checkpoint 的 `micro_window Top-8` 配置保持不变；
- fixed 路径主动清空旧 `_last_dynamic_window_selector_output`，防止上一样本的 source weights 污染当前样本；
- audio branch 关闭、window mode disabled、缺少音频或未知 stage 时不再静默忽略 external selection，而是直接失败。

## 3. B-003：true All

状态：已关闭。

实现：

- `fixed_all` 只选择 valid mask 内全部窗口；
- runner 再次断言 `selected_window_count == full_window_count`；
- trace 同时保存 full、valid、selected count、position、source index、timestamp 和 score；
- true All 与没有 policy 的 Current Micro Top-8 是两个不同入口，不再依赖目录名或实验名解释。

边界：本轮只验证合同/CPU 接线；真实长音频的显存与上下文容量仍必须在 P2 两样本 GPU smoke 中验证。若运行时无法容纳 All，必须 fail/报告，不得静默截断。

## 4. B-004：selector-only 冻结和 optimizer 审计

状态：已关闭。

新增严格 alias：

```text
scene_audio_selector
```

只允许：

```text
model.streaming_av_module.question_conditioned_window_selector.audio_projection.weight
model.streaming_av_module.question_conditioned_window_selector.question_projection.weight
```

防护：

- alias 不能与其他 tunable part 组合；
- 必须启用 scene audio；
- 参数解析必须恰好命中两个权重，缺失或重复直接失败；
- 解冻后审计全模型 `requires_grad`；
- optimizer 建立后按参数 identity 再审计，拒绝额外、缺失、重复或外部参数；
- 单步测试验证非 allowlist 参数无梯度且数值逐元素不变。

目标 checkpoint index 已只读确认上述两个参数都存在。

## 5. B-005：Oracle diagnostic-only 隔离

状态：已关闭。

Oracle 必须同时满足：

- `scene_audio_selection_diagnostic=true`；
- inference 且非 production；
- 明确提供 `target_intervals`；
- 明确提供 K；
- trace 固定写入 `uses_ground_truth=true`。

training、production、缺少 diagnostic flag、缺少标签或未知 policy 均直接失败。普通模型 config 没有 Oracle mode，Oracle 只存在于外部 diagnostic registry。

## 6. B-006：OmniMMI evaluator JSONL

状态：已关闭。

修改：

- 删除预测输入的逐行 Python `eval()`；
- 使用 `json.loads` 严格读取 UTF-8 JSONL；
- 支持标准 JSON `true/false/null` 和嵌套 diagnostics；
- 拒绝 Python literal、非 object、空文件及缺少 `answer/pred` 的记录；
- 错误包含文件路径和行号。

说明：OpenAI 评分响应仍使用安全的 `ast.literal_eval`，不执行任意代码；本 blocker 针对的是本地预测 JSONL 读取路径。

## 7. 测试证据

环境：

```text
/home/yjm/miniconda3/envs/M4/bin/python
```

已执行：

- 修改文件 `py_compile`；
- contract/policy/Oracle/random/score/tie/shape 测试；
- runner explicit/fixed_all/Oracle/trace 测试；
- model internal selector bypass、全局 config 不变、stale state 清理测试；
- selector-only allowlist、optimizer、梯度和冻结参数不变测试；
- evaluator 标准 JSON 与非法输入测试；
- 全部 `intersuit/tests` CPU 回归。

最终结果：`449 passed, 2 warnings`。两项均为既有 SWIG deprecation warning，没有 test failure。

## 8. 本轮没有做的事

- 没有加载 7B checkpoint；
- 没有运行 GPU；
- 没有运行真实 BEATs 推理；
- 没有运行 P1/P2/P3；
- 没有训练 selector；
- 没有修改任何模型权重、数据集、预测或历史结果；
- 没有把 PA/dev760/PT 当作未见 test。

## 9. Review 判定

```text
status=pass
blockers=[]
majors=[]
cpu_contract_tests_required=true
gpu_full_allowed=false
selector_training_allowed=false
next_stage_allowed=P1_offline_only_after_user_approval
```

人话解释：原来最大的问题是“外面选过一次，模型里面可能再选一次”，导致实验名字和模型实际看到的窗口不一致。现在外部选窗有统一凭证，模型收到凭证后明确走 fixed 旁路；All、Oracle、Random 等条件都能留下逐窗口证据。训练侧也只会把两个 selector 权重交给 optimizer，多一个参数都直接报错。代码层面的六个洞已堵住，但还没有证明真实 GPU 推理能承受 All，也没有证明 selector 有收益。
