# M4 OmniMMI PT 论文配置复现审计报告

## 结论摘要

本轮没有复现出论文表 3 的 PT `62.00%`，但已经把公开资产下的结果和主要不确定项分离清楚。使用官方公开 M4 权重、官方 M4 revision、OmniMMI 的 200 条 PT 数据以及固定等距 32 帧时：

- 当前公开 adapter 配置（temperature `0.05`、beta `1e-6`、token `151644=<|im_start|>`）得到 `104/200 = 52%`。
- 仅将 beta 改成论文附录 G 的 `0.2`，得到 `100/200 = 50%`。
- 论文没有披露 PT softmax temperature；事后设为 `1.0` 并使用 beta `0.2` 会得到 `126/200 = 63%`，但这不是有论文依据的严格复现，不能把它报告为官方结果。
- 公开 16 帧配置得到 `114/200 = 57%`；改为 32 帧反而下降 5 个百分点。

因此，当前不能把 10 个百分点的差距归因于单一因素。已确认差异包括 PT 阈值参数；无法核验的因素包括榜单所用 checkpoint、PT softmax temperature、精确 adapter revision 和完整运行命令。

人话解释：本地模型文件没有被改坏，32 帧也确实跑对了。问题在于论文、公开评测代码和公开权重没有共同给出一套闭合配置，现有材料不足以严格重建论文里的 62%。

## 证据来源

1. OmniMMI/M4 论文：arXiv `2503.22952`。M4 是该论文提出的模型，不存在另一篇独立的 M4 论文。
2. 论文表 3：M4 为 Qwen2-7B，Num Frames 为 `32 / 1 fps`，PT 为 `62.00%`，PA 为 `25.50%`。
3. 论文附录 G：`alpha=2`、`beta=0.2`、`gamma=4`、Model Max Length `32000`、学习率 `1e-5`、warmup `0.03`、batch `1`、gradient accumulation `4`、epoch `1`。
4. 官方 M4 代码 revision：`574d2244576190cd150cd28aaaa8dbd2ea7502e6`。
5. 官方公开模型：`ColorfulAI/M4-LongVA-7B-Qwen2`，冻结 revision `c859c97d357a612183d8caa258b5812b7e78080f`；四个权重 shard 的 SHA-256 已记录在 `baseline_manifest.json`。
6. OmniMMI PT 数据：200 条，SHA-256 为 `e79fb40e7ef66e7d6821a241aab67f992ea83e08a7191ce6759f79c562b08433`。

## 可复现实验结果

| 配置 | 帧策略 | PT 结果 | 与论文 62% 差距 |
| --- | --- | ---: | ---: |
| 公开 adapter | 固定 16 帧 | 57% | -5 pp |
| 公开 adapter | 固定 32 帧 | 52% | -10 pp |
| 公开 temperature `0.05` + 论文 beta `0.2` | 固定 32 帧 | 50% | -12 pp |
| 未公开于论文的 temperature `1.0` + beta `0.2` | 固定 32 帧 | 63% | +1 pp |

固定 32 帧的完整 last-token logits 已保存。用这些 logits 离线重算公开配置时，与冻结的 32 帧预测逐样本 `200/200` 一致，说明诊断链路和原 runner 的 PT 判定一致。

## PT 判定差异

公开 adapter 的 PT 分支不执行文本生成，而是读取最后位置 logits，使用：

```text
temperature = 0.05
threshold = 1e-6 * exp(-entropy)
noise_token = 151644 = <|im_start|>
noise_probability > threshold 时判为静默
```

论文描述的动态阈值形式相同，但附录给出的 beta 为 `0.2`。在公开 temperature `0.05` 下，仅替换 beta 会把 52% 降到 50%，所以 beta 差异不能单独解释论文结果。

论文文字使用 `<bos>`，但当前 Qwen2 tokenizer 中三个相关 token 为：

| Token ID | 当前 tokenizer 语义 | temperature 0.05, beta 0.2 |
| ---: | --- | ---: |
| 151643 | `<|endoftext|>` | 0% |
| 151644 | `<|im_start|>` | 50% |
| 151645 | `<|im_end|>` | 30% |

把论文的 `<bos>` 直接映射成 `151643` 会得到 0%，与榜单明显矛盾。公开实现选择 `151644` 更可能代表对话开始/继续信号，但论文没有把该 token ID 和 tokenizer revision 写清楚。

## `32 / 1 fps` 的解释

公开代码存在两条独立路径：

- 非 online 的 `M4` adapter：一次性加载固定数量的等距帧；PT 在 `inference_utils.py` 中调用这条路径并设置 `gen=False`。
- online 的 `M4Online` adapter：设置 `frame_fps=1`，由 `online_inference_utils.py` 按时间流式输入帧，用于 PA 等 online 任务。

PT 的 200 个视频时长范围约为 206–6029 秒，中位数约 471 秒，全部超过 32 秒。若把 PT 解释为完整视频 1 FPS，则每条将输入 206–6029 帧，而不是 32 帧。这既不符合公开 PT 调用链，也与表格中斜杠同时列出两种预算的写法不符。

基于公开实现，最有证据支持的解释是：`32` 对应非在线任务，`1 fps` 对应 online/streaming 任务。由于论文没有逐任务明确拆分，该解释应标记为高可信推断，而不是作者确认的事实。完整 PT `fps1` 运行没有执行，因为它不符合公开 PT 路径，且最长样本会扩到 6029 帧，存在明显显存和上下文风险。

## Checkpoint 审计

本地公开权重四个 shard 已与冻结的 Hugging Face revision 哈希一致，可判定为未修改的官方公开权重。官方 M4 源码也使用独立解压目录和固定 revision。

但是，OmniMMI 历史 `model_testing_zoo.py` 中的 M4/M4Online 路径先赋值公开名，随后又覆盖成：

```text
longva7b-llavanextsub10k-qwen2-ORNS1111-ablate
```

该 checkpoint 本地不存在，公开仓库和论文也没有提供其权重哈希或到公开 HF 模型的等价证明。因此目前只能确认“本地使用的是官方公开权重”，不能确认“论文表 3 使用的就是该公开权重”。

## 当前模型配置与论文的差别

| 项目 | 本地严格复跑 | 论文/榜单 | 状态 |
| --- | --- | --- | --- |
| 基座 | Qwen2-7B | Qwen2-7B | 对齐 |
| 权重 | 官方公开 HF checkpoint | 未给运行权重哈希 | 无法证明一致 |
| 非在线采帧 | 固定等距 32 帧 | `32 / 1 fps` | 高可信对齐 PT 的 32 帧部分 |
| PT beta | `1e-6`（公开代码）或诊断 `0.2` | `0.2` | 公开代码与论文不一致 |
| PT softmax temperature | `0.05` | 未披露 | 无法对齐 |
| PT token | `151644=<|im_start|>` | 文字写 `<bos>` | 语义未完全对齐 |
| max length | checkpoint 配置与推理实际输入约束 | 训练表为 `32000` | 训练/评测口径不能直接等同 |
| 随机性 | PT 为确定性前向；样本顺序 seed 42 | 未披露精确命令 | PT 分数本身不受生成采样影响 |

## 建议的改进计划

1. 保留固定 16 帧 57% 和固定 32 帧 52% 两条公开资产基线，不修改官方 adapter 默认值。
2. 将固定 32 帧、temperature `0.05`、beta `1e-6`、token `151644` 设为“公开代码复现口径”。
3. 将固定 32 帧、beta `0.2` 的所有 temperature 扫描标记为诊断实验，不以接近 62% 选择参数。
4. 向作者索取表 3 的 checkpoint revision/hash、PT softmax temperature、检测 token ID、adapter commit 和完整命令。
5. 获得上述信息后，只需复用已保存 logits 即可先验证 threshold/token/temperature；只有 checkpoint 或采帧变化时才重新跑 GPU。
6. 后续论文或报告同时列出“公开代码结果”和“论文配置结果”，避免把未披露参数调到接近榜单后称为复现。

## 产物与限制

- 冻结清单：`baseline_manifest.json`
- 逐样本分数：`fixed32_full/artifacts/scores.jsonl`
- 完整 FP16 logits：`fixed32_full/artifacts/logits/`
- 基础扫描：`fixed32_full/basic_scan.json`
- 扩展扫描：`fixed32_full/grid_scan.json`
- 诊断 runner：`intersuit/scripts/diagnose_omnimmi_pt.py`
- 离线扫描：`intersuit/scripts/summarize_omnimmi_pt_scores.py`

本报告不声称完全复现论文 PT 62%。最严格的结论是：在可获得的官方公开权重、当前公开 OmniMMI PT adapter 和固定 32 帧协议下，结果为 52%；论文公开信息不足以唯一确定生成 62% 的评测配置。
