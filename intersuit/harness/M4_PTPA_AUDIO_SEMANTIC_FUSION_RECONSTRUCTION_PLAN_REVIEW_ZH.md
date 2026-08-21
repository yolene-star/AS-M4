# 音频语义、答案表示与融合位置重构计划审查

审查版本：`v2`（2026-08-20）

## 1. 审查结论

结论：`PASS_FOR_DATASET_QUALIFICATION_ONLY`。

本计划正确承接了 Oracle selector 官方门禁失败：selector 路线保持停止，新路线只授权 CPU、只读的 S0 合同与数据审计。GPU、特征提取、训练、融合修改、confirm 和正式 test 均未授权。

v2 将数据责任实例化为 LLP→S1、Clotho-AQA→S2、AVCaps→S3a、
LongVALE→S3b/S6，但计划通过不等于数据资格通过。除用户已明确授权的
LongVALE `part_19` 补充下载外，不解锁其他媒体下载。

## 2. 已关闭的 blocker

### B-001：旧失败目标可能被重复包装

旧 R2 的四个文本目标全部失败。计划已将其锁定为反证，不允许重跑后改名晋级。

### B-002：teacher-confirm 已访问

旧 `pad__ensemble3` teacher 只能作开发候选；`teacher_confirm` 不得作为新 confirm 或最终门禁。

### B-003：clip label 被误当窗口标签

计划显式禁止将 clip label 复制到每个 1 秒窗口。same-video window hard negative 只有在真实时间监督存在时才允许。

### B-004：首轮联合训练无法归因

计划固定为语义目标、答案表示、融合位置三个单模块依次做冻结上界；至少两个单模块独立通过后才允许两模块组合。

### B-005：旧 selector 权限泄漏

新路线使用独立 lock，selector、deterministic、confirm 权限全部固定为 false，不能修改旧锁解锁。

### B-006：官方评测输入合同

LongVALE 必须按 annotation 字典顺序提交 predictions，并使用官方 METEOR corpus `EVAL`；该合同已经由提交 `e7a1f13` 和 `52ce60c` 固化。

## 3. 仍需在 S0 关闭的 blocker

1. 新路线尚无完整历史 split 访问台账和物理媒体交集矩阵；
2. hard-negative 候选尚未逐条证明 canonical label 不冲突；
3. teacher 的许可证、来源模型和输入归一化尚未纳入新 lock；
4. 新 internal confirm 尚未证明未被历史资产访问；找不到时必须停止，不能复用旧 confirm；
5. S1 需要哪些冻结特征尚未由 manifest SHA 锁定。
6. LLP 已将 112 份历史 manifest 快照、AVE、AS-M4 Stage2 和 LongVALE 归一为
   3712 个 YouTube 物理 ID，冻结的 train 100 + validation 50 pilot 与该并集重叠为 0；
   M4-IT 本地 9963 行是 image/speech instruction ID，不伪装成 YouTube ID。仍缺 AVQA
   本地物理 ID 清单，因此不能声称全历史完全闭合。仓库 GPL-3.0 不外推到第三方 YouTube
   视频；逐条 availability/license probe 的 2 条 smoke 均因页面 reload/反机器人认证失败，
   未使用 cookies、未取得媒体、未解锁下载。metadata probe 与下载后 SHA/ffprobe/音轨/时长
   验收合同已实现，但正式媒体 downloader 尚未实现/Review，逐视频许可、存活率和媒体审计仍未完成。
7. Clotho-AQA 尚未锁定 Zenodo 实体、每文件许可证、官方 split 文件哈希和物理去重。
8. AVCaps 尚未锁定 Hugging Face revision/Zenodo MD5、物理 split 交集、历史暴露和媒体解码。
9. LLP class、Clotho-AQA answer 和 AVCaps caption 属于不同监督空间，统一语义中介表示、
   数据集内指标和跨阶段不矛盾合同尚未由代码锁定。

LongVALE 原 blocker 已于 2026-08-21 关闭：`part_19` 大小/SHA/ZIP CRC/member index 通过，
排除历史 6 和预留 6 后的 186 个物理视频已全部解压并通过逐文件
CRC/size/SHA/ffprobe/音轨/事件解码/时长边界验收。该关闭只表示媒体合格，
不解锁 LongVALE 效果访问。

因此当前必须记录 `dataset_qualification_allowed=true`、
`next_stage_allowed=false`；前者只允许 metadata/许可/物理去重/媒体 pilot 计划审计，
后者禁止进入 S1/GPU/特征/训练或任何效果 test。

## 4. v2 数据责任审查

### D-001：LLP 作为 S1 主数据

计划级通过。LLP 的 10 秒视频、25 类和 val/test 1 秒密集 audio/visual annotation
与 `1s/1s + frozen BEATs + M4` 合同匹配。本地初审证明官方 split 物理交集为 0，
但 train 是弱标注、raw media 未在本地，因此只允许下一步构建 pilot 计划，不允许下载 full/test。

### D-002：Clotho-AQA 作为 S2 主数据

计划级通过。它提供人工音频 QA 和物理隔离 split，能避免事件标签模板捷径。
它没有视频，且答案偏 yes/no/单词，所以只能证明 S2，必须分层报告，不得替代 S3。

### D-003：AVCaps 作为 S3a 主数据

计划级通过。独立 audio/visual/audio-visual captions 可用于审计模态贡献和融合稀释。
它没有 canonical class 和 1 秒边界，因此不能回流到 S1；caption 任务也不能替代 LongVALE 端到端生成门禁。

### D-004：LongVALE 保留长视频职责

通过。保留已冻结的官方 METEOR corpus 输入顺序、stats-list EVAL、
物理媒体 paired bootstrap 和 `+0.01` 门禁。媒体资格已以 186/186 PASS 关闭，
封存 manifest 与 5 套 mismatch 映射已锁定；权限仍为 `qualified_effect_locked`，
必须等待 S1/S2/S3a 前置门禁，不复活失败的 selector 路线。

### D-005：禁止跨数据集挑分

通过。每个数据集只回答一个预注册问题，分数不求平均。下游失败不得被上游高分抵消；
S1/S2/S3a 都通过也不等于 S3b/S6 通过。

## 5. 统计审查

- 主单位为物理媒体，不按 QA 行伪增样本量；
- bootstrap seed 和次数必须在运行前冻结；
- 3 seed 指的是独立初始化或独立数据采样，不得复制同一确定性结果冒充；
- development 用于候选选择，internal confirm 仅一次；
- 已访问的 dev760/PT/PA/teacher-confirm/LongVALE pilot 不能称为 untouched test；
- inconclusive 按失败处理，不事后扩样或放宽阈值。

## 6. 人话结论

现在只批准“把四套数据的来源、许可、物理去重、可下载率和职责查清”。
还没有批准下载 LLP/Clotho-AQA/AVCaps 全量、提特征、改模型或训练。
原计划的考试顺序不变，只是现在为每一科指定了不同的合法试卷。
