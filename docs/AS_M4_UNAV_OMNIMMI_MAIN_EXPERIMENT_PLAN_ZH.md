# AS-M4 主实验收敛计划：UnAV-100 机制验证与 OmniMMI/M4 原任务验证

> 2026-09-19 状态更新：本计划已被 `docs/AS_M4_AUDIO_CAPABILITY_DIMENSION_SPLIT_PLAN_ZH.md` 覆盖。UnAV-100 继续负责时间定位与预算选窗；LLP/AVVP 从历史诊断升级为独立的模态依赖与音频选择性副基准；OmniMMI 继续负责最终问答效用。强制执行“单维度单测试集”，禁止跨数据集混合分母或综合总分。

日期：2026-09-10（Asia/Shanghai）

状态：`MAINLINE_FROZEN_UNAV100_PLUS_OMNIMMI_M4`

## 1. 主线决策与边界

当前停止 MUSIC-AVQA 路线，主实验只保留两条线：

1. `UnAV-100`：验证场景音频编码、时间定位与 Dynamic Window Selector 机制；
2. `OmniMMI/M4`：验证 AS-M4 在原任务上的最终收益、回退安全性和 PT/PA/turn-taking 保真。

本阶段固定 M4/Qwen2、视觉塔和现有场景音频主体，不做联合训练，不启动 150-step 或正式大规模训练，不新增模型结构。允许的 learned selector 仅限复用现有 selector 结构、在冻结特征上独立训练和评估；其结果不能反向更新模型主体。

MUSIC-AVQA、AVE generation、AVUT 和 LLP 只保留为历史诊断证据，不再作为当前主实验的晋级门禁，也不继续扩大。

## 2. 第一条线：UnAV-100 机制验证

### 2.1 研究问题

- 冻结 BEATs/现有音频投影后，音频窗口是否包含可用的事件语义；
- selector 是否能在固定窗口预算下定位官方事件区间；
- Dynamic Window Selector 是否能稳定优于 Random，并回收多少 Oracle headroom；
- 时间定位收益是否来自真实音频内容，而不是能量、时长或标签泄漏。

### 2.2 固定比较矩阵

所有策略使用同一批 UnAV-100 validation 物理视频、同一 micro-window 定义、同一 K/预算和同一冻结特征：

| 策略 | 定义 | 当前状态 |
| --- | --- | --- |
| Random | 五个固定 seed 的随机窗口 | 已有 K=4/K=8 结果 |
| Energy | 按窗口能量排序 | 已有 K=4/K=8 结果 |
| Eventness | 冻结 cached Eventness 排序 | 已有 K=4/K=8 结果 |
| Temporal | 冻结时间定位分数排序 | 缺同 schema、同预算正式结果；历史 bundle 不兼容，不得直接复用 |
| Oracle | 使用官方事件边界的非因果上界 | 已有 K=4/K=8 结果 |
| learned selector | 复用现有 selector，在冻结特征上独立训练 | 尚未完成同口径冻结评测；不得更新模型主体 |

`Onset` 和 `Uniform` 保留为补充对照，但不替代上述六项主矩阵。

### 2.3 已有冻结证据

- 官方 validation：443 个事件样本、335 个物理视频；
- BEATs 冻结特征：335/335 视频、14,814 个 1 秒窗口；
- K=8 Event Coverage：Random `0.178958`、Energy `0.298830`、Eventness `0.319828`、Oracle `0.999802`；
- K=8 Positive-window Recall：Energy `0.281106`、Eventness `0.298202`、Oracle `0.998846`；
- K=8 相对 Random：Energy `+11.9871pp`、Eventness `+14.0870pp`、Oracle `+82.0843pp`，三者 cluster bootstrap 95% CI 下界均大于 0；
- K=4 Event Coverage：Random `0.084179`、Energy `0.202441`、Eventness `0.202994`、Oracle `0.967883`；
- K=4/K=8 已双重复现，未加载 M4、未更新模型参数、未访问 official test；
- 冻结 BEATs 全量特征提取已通过：7,941 条记录、334,769 个窗口，train/validation 为 6,224/1,717；
- 独立 projector 训练曾取得 validation window accuracy `0.416953`，但它不等于 selector 定位结果，也不作为当前主矩阵的 learned selector 成绩。

### 2.4 当前缺失项

1. 将 Random、Energy、Eventness、Temporal、Oracle、learned selector 放进同一 evaluator、同一 K/预算的统一结果表；
2. 为 Temporal 构造与正式 1 秒 micro-window schema 一致的冻结分数，不能对历史 20 个重叠窗口 bundle 做零填充或静默适配；
3. 对 learned selector 冻结 train/validation、参数白名单、早停规则和 checkpoint SHA；
4. 统一报告 Event Coverage@K、Window Hit@K、Positive-window Recall@K、首个命中排名、mean best tIoU、Oracle headroom 和 headroom recovery；
5. Random 使用固定五 seed，并按物理视频做 cluster bootstrap 95% CI；
6. 补长短视频、事件占比、单/多事件分层和实际保留窗口/token/延迟；
7. 明确 cached Eventness 不节省 BEATs，仅证明选窗排序能力；计算收益需要另行报告在线编码边界。

### 2.5 机制验证通过条件

- Oracle 相对 Random 存在明确且统计显著的 headroom；
- 至少一种非 Oracle 策略显著优于 Random，并胜过至少 4/5 个 Random seed；
- learned selector 只有在独立 validation 上优于 Random，且不低于最强规则策略时才称为有效；
- 若 learned selector 未超过 Eventness/Energy，只报告“规则机制有效，learned selector 未证明增益”；
- UnAV-100 结论只用于机制层，不外推为 M4 最终任务准确率。

## 3. 第二条线：OmniMMI/M4 原任务验证

### 3.1 固定模型比较

- 原始 M4：官方公开 checkpoint 和官方 PT/PA/turn-taking 路径；
- AS-M4：固定待评 checkpoint，除场景音频开关/条件外权重保持不变；
- 不使用不同语言模型或视觉基座的结果直接归因场景音频；同时报告“官方原始 M4 绝对参考”和“AS-M4 同权重四条件增量”。

### 3.2 四条件合同

每个样本冻结以下四条件，除场景音频外，视频、问题、prompt、解码、帧采样和非音频 token 必须逐项一致：

1. `correct_audio`：目标视频的正确场景音频；
2. `mismatch_audio`：来自不同物理视频、语义不匹配且长度处理规则冻结的音频；
3. `video_only`：不注入 scene-audio token；
4. `silence`：注入物理全零音频，并要求回退到 video-only。

原始 M4 也使用同一 manifest 和评分入口；若其本身不消费场景音频，四条件应表现为不受音频条件影响，不能伪装成 AS-M4 对照。

### 3.3 子集拆分

预测前必须完成问题级固定标注，至少拆分：

- `Audio-required`：去掉音频后无法可靠回答，声音/语音/声源或听觉时序是必要证据；
- `Visual-only`：答案只依赖画面，音频不应带来变化；
- `Audio-Visual Joint`：需要音频与视觉共同判断；
- `Overall`：完整原任务结果。

标注必须保存规则、来源记录、物理视频 ID、问题 ID、审查状态和 manifest SHA。不得在查看模型预测后修改类别。歧义样本单独标记，不强行纳入 Audio-required。

### 3.4 已有证据与可复用资产

- OmniMMI PT/PA 各 200 条媒体已在本地；PT 中 184 条有音轨、16 条无音轨，PA 200 条均有音轨；
- 官方原始 M4 已完成 200 条 PT 与 200 条 PA：官方 evaluator PT Accuracy `57.0%`，PA Accuracy `21.5%`、Precision `31.82%`、IoU `14.02%`；
- PT 另有同批 logits 的离线重判 `63.0%`，必须与官方 evaluator 的 `57.0%` 分开报告，不能混为一个基线；
- dev760 已有 video-only/correct/mismatch 历史诊断，但不具备本次四类原任务拆分，且旧 checkpoint correct 未优于 mismatch；只作历史风险提示；
- 现有 dev760 runner 已支持 correct/mismatch/silence/gate0；OmniMMI PT/PA 的场景音频接线与逐帧审计仍需按原任务入口确认。

### 3.5 当前缺失项

1. OmniMMI 问题级 `Audio-required / Visual-only / Audio-Visual Joint / ambiguous` 标注与独立 review；
2. 四条件 manifest、mismatch 一一映射、音频 SHA、无音轨回退和物理媒体隔离审计；
3. 原始 M4 与 AS-M4 的统一模型/输入合同、checkpoint SHA 和解码参数快照；
4. PT runner 场景音频接入、完整 logits/预测和音频分支诊断；
5. PA 在线逐帧路径中 scene audio 确实进入视觉 embedding 的证据；
6. turn-taking 原任务的小规模回归集合及“不破坏”指标；
7. 分子集指标、配对差值、置信区间、回退一致率和错误类型报告；
8. smoke/canary 的预注册样本清单与晋级门禁。

### 3.6 核心验收条件

1. AS-M4 在 `Audio-required` 上优于原始 M4；
2. AS-M4 的 `correct_audio` 优于 `mismatch_audio` 和 `video_only`；
3. `silence` 与 `video_only` 保持逐样本一致，或在预注册容差内近似回退；
4. `Visual-only` 不发生明显退化；
5. PT、PA 和 turn-taking 不出现超过预注册容差的退化；
6. 结果同时报告四个子集，不以 Overall 掩盖 Audio-required 或 Visual-only 的反向变化。

## 4. 执行顺序与门禁

### 阶段 A：只读盘点与合同冻结

1. 整理 UnAV-100 已有结果表、SHA 和缺失项；
2. 补齐同口径 Temporal 与 learned selector 评测设计；
3. 筛选并人工复核 OmniMMI Audio-required/Visual-only/Joint；
4. 冻结四条件 manifest、mismatch、模型与评分合同；
5. 完成 CPU contract、dry-run、数据和泄漏审计。

### 阶段 B：小规模 smoke

- UnAV-100：先用冻结小子集验证六策略 evaluator 的输入输出、指标和 Random 可复现性；
- OmniMMI：每类抽取少量已审核样本，四条件齐全；验证输出非空、correct/mismatch 真正不同、silence/video-only 回退，以及 PT/PA 音频接线确实生效。

### 阶段 C：canary

- 仅在 smoke 全部门禁通过后扩大到预注册 canary；
- canary 仍使用冻结模型，不训练主体；
- 先查看分子集配对结果，再决定是否扩大完整评测。

### 阶段 D：扩大评测

只有同时满足 Audio-required 收益、正确音频选择性、silence 回退、Visual-only 非劣和 PT/PA/turn-taking 保真，才允许扩大评测。任何一项失败都回到数据、合同或接线诊断，不启动 150-step 或正式训练。

## 5. 当前即时下一步

1. 生成 UnAV-100 六策略“已有/缺失/可复现资产”清单；
2. 从 OmniMMI PT/PA 原始标注中筛选候选 Audio-required、Visual-only 和 Joint 问题；
3. 在看任何新预测前完成人工 review 和四条件合同；
4. 只准备 CPU/mock 与 GPU smoke 入口，不启动 GPU 大规模运行；
5. 将 MUSIC-AVQA 配置保持关闭，禁止沿旧路线继续 NLL、generation 或计数实验。

## 6. 人话结论

UnAV-100 用来回答“耳朵和选窗器本身好不好用”，OmniMMI/M4 用来回答“装上这只耳朵后，原来的任务到底有没有变好、会不会把原能力弄坏”。前者证明机制，后者证明最终价值；两条线不再混用结论。
