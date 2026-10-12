# Valor32k-AVQA v2.0 官方标注审计报告

日期：2026-08-29（Asia/Shanghai）

## 1. 结论

本轮标注审计结论为：

```text
PASS_WITH_EXCLUSIONS
```

Valor32k-AVQA v2.0 可以进入“仅使用 official train 的 100--200 媒体 pilot”，但不能把全量
JSON 原样作为训练合同。首阶段 canonical 标注必须固定为 flattened JSON，并且只保留
`model=gpt4o && quality_rating=obvious`，再删除 2 条精确重复记录。

本结论不授权下载完整媒体，不授权使用 official validation/test，不授权训练或 7B 评测。

## 2. 来源锁

- 官方仓库：`https://github.com/inesriahi/valor32k-avqa-2`
- 官方标注：`main/data.zip`
- GitHub blob SHA：`71dfe0d0862f0d6c15cb507dc624c08e4f16008d`
- 文件大小：`37,028,544` 字节
- 本地 SHA-256：`fe95e7e9a2485f49d8465c2cc953243f0bb4b49aefd3c33ffde52fda5d9b48b7`
- ZIP 完整性：6/6 文件通过，无压缩错误
- 解压内容：train/val/test 的 flattened 与 grouped JSON，共 6 份，约 401 MB
- 许可证：官方仓库 `LICENSE` 为 CC BY-NC 4.0；只允许非商业用途，并需要署名。原始视频仍受原上传者和平台权利约束。

## 3. 官方发布数量与独立交叉核验

本地官方 ZIP 与 Hugging Face Dataset Viewer `/size` 完全一致：

| split | 问题数 | 物理 `video_id` 数 |
|---|---:|---:|
| train | 177,132 | 22,455 |
| validation | 22,267 | 2,942 |
| test | 26,088 | 3,464 |
| 合计 | 225,487 | 28,861 |

Dataset Viewer 报告 13 个字段，train/validation/test 行数分别为
`177132/22267/26088`，与本地 flattened JSON 一致。官方 README 中仍有
`225,495` 个问题、`28,863` 个视频的旧统计；当前发布实际少 8 个问题、少 2 个视频，后续合同
必须以本次 SHA 和实际读取值为准。

三组物理视频交叉结果：

```text
train ∩ validation = 0
train ∩ test       = 0
validation ∩ test  = 0
```

因此官方 split 在 `video_id` 层没有发现交叉泄漏。

## 4. 全量标注问题

全量 flattened JSON 不应直接使用：

1. GPT-3.5 回退记录共 `3,578 + 442 + 591 = 4,611` 条，其 `quality_rating` 为空、
   `source_tags=[]`；
2. train 中有 2 条 GPT-3.5 记录的一个 MCQ option 为非标准 JSON `NaN`：
   `video_id=2ctgUIqyaBk` 与 `video_id=5bKeBLyRTz0`；
3. train 的 `gpt4o+obvious` 子集中仍有 2 条精确重复问题，应在 manifest 中按完整签名去重；
4. grouped JSON 的 GPT-3.5 视频组缺少 `prompt`，且 grouped 问题总数比 flattened 少
   train/validation/test=`42/10/9`；官方说明 grouped 会过滤少量异常标签，而 flattened 已就地修正，
   两种发布格式不能混合作为同一 canonical 真值。

因此本项目首阶段固定使用 flattened JSON；grouped 只用于按 `video_id` 检索上下文，不作为问题
真值来源。

## 5. 可用候选子集

过滤 `model=gpt4o && quality_rating=obvious` 后，字段合同没有发现错误：

| split | 候选问题 | 候选视频 | visual | audio | audio-visual |
|---|---:|---:|---:|---:|---:|
| train | 169,062 | 21,072 | 96,205 | 34,648 | 38,209 |
| validation | 21,301 | 2,770 | 12,677 | 4,082 | 4,542 |
| test | 24,917 | 3,235 | 14,914 | 4,746 | 5,257 |
| 合计 | 215,280 | - | 123,796 | 43,476 | 48,008 |

去除 train 的 2 条精确重复后，候选问题为 `215,278` 条。当前项目下一阶段只允许从 official
train 候选中抽取 pilot，不读取 validation/test 的答案或媒体。

## 6. 媒体可用性边界

- Hugging Face 当前只附带 test 视频；train 和 validation 源视频没有在该 HF 发布中重新分发；
- 使用 HF test 视频会触碰 formal test，当前禁止；
- 官方 VALOR-32K 百度网盘链接仍可返回 HTTP 200，但它是完整媒体共享入口，不是已验证的逐文件
  pilot 下载接口；
- 原 VALOR 仓库公开 issue 已报告百度网盘下载速度问题。

因此“标注审计通过”不等于“媒体已经就绪”。下一阶段必须先列出官方共享目录、核对文件命名与
`video_id` 映射，并确认能够只取 100--200 个 official train 视频；不得为了方便改用 HF test 视频。

## 7. 下一阶段门禁

下一阶段允许：

1. 从 official train 中按 `audio/visual/audio-visual` 与 category 分层生成 100--200 个候选
   `video_id` manifest；
2. 对 manifest 做现有 dev/test/AVE/UnAV 媒体 ID 与 SHA 排除；
3. 只读列出官方 VALOR-32K 媒体共享目录并核对文件名映射；
4. 若支持逐文件下载，再下载 pilot 并统计成功率、音轨率、时长、采样率、文件 SHA 和大小。

下一阶段禁止：

- 下载完整 VALOR-32K 媒体包；
- 使用 official validation/test 做模型选择；
- 在媒体可用率门禁前启动 Gate 训练、7B 推理或 full dev；
- 从非官方镜像补齐缺失媒体。

## 8. 审计资产

- 机器结果：`intersuit/datasets/VALOR32K_AVQA_V2_AUDIT/annotation_audit_v2.json`
- 首轮严格失败结果：`intersuit/datasets/VALOR32K_AVQA_V2_AUDIT/annotation_audit_v1.json`
- 审计日志：`intersuit/datasets/VALOR32K_AVQA_V2_AUDIT/logs/`
- 官方许可证副本：`intersuit/datasets/VALOR32K_AVQA_V2_AUDIT/LICENSE.upstream`
- 审计脚本：`intersuit/scripts/audit_valor32k_avqa_annotations.py`
