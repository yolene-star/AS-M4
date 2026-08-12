# 音频语义、答案表示与融合位置重构计划审查

## 1. 审查结论

结论：`PASS_FOR_S0_ONLY`。

本计划正确承接了 Oracle selector 官方门禁失败：selector 路线保持停止，新路线只授权 CPU、只读的 S0 合同与数据审计。GPU、特征提取、训练、融合修改、confirm 和正式 test 均未授权。

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

因此当前必须记录 `s0_audit_continuation_allowed=true`、`next_stage_allowed=false`；前者只允许关闭 S0 blocker，后者禁止进入 S1/GPU/训练。

## 4. 统计审查

- 主单位为物理媒体，不按 QA 行伪增样本量；
- bootstrap seed 和次数必须在运行前冻结；
- 3 seed 指的是独立初始化或独立数据采样，不得复制同一确定性结果冒充；
- development 用于候选选择，internal confirm 仅一次；
- 已访问的 dev760/PT/PA/teacher-confirm/LongVALE pilot 不能称为 untouched test；
- inconclusive 按失败处理，不事后扩样或放宽阈值。

## 5. 人话结论

现在只批准“查清数据、负样本和历史资产还能不能合法使用”。还没有批准提特征、改模型或训练。先证明音频表示真的懂事件语义，再谈答案和融合。
