# Audio generation 测试数据审查报告

日期：2026-09-01（Asia/Shanghai）

## 测试内唯一来源

- 数据集：`AVE_HF_EXPANDED`；
- 来源清单：`intersuit/datasets/AS_M4_STAGE2/reserve_manifest.json` 中 `source_dataset == AVE_HF_EXPANDED` 的行；
- split：`reserve`；
- 记录数：72；
- physical media：72；
- source revision：`sha256:7c5a67e34c6dbd53892cb3a66f0fa3886dac77dec3d17986391ec30c7509aa1e`。

本测试不混入 AVUT、MUSIC-AVQA、LLP 或其他来源。

## 审查结果

| 项目 | 结果 | 说明 |
|---|---|---|
| 来源与 split 统一 | PASS | 72 条均为 AVE_HF_EXPANDED/reserve |
| 与 adapter pilot 媒体隔离 | PASS | 当前候选与 pilot physical media 无重叠 |
| 视频/音频存在性 | PASS | 72/72 视频和音频路径存在 |
| 输入格式 | PASS | 72 条记录声明 16 kHz 音频；路径和 SHA 完整 |
| 样本/媒体唯一性 | PASS | sample_id 和 physical media 均无重复 |
| 事件标签 | PASS | 25 个标签，长尾分布；可固定类别配额 |
| 问答格式 | PASS WITH LIMITATION | 使用统一音频事件问题模板，答案为 event_label；不代表开放域问答泛化 |
| mismatch 可构造性 | PASS | 可在同一 AVE reserve 内选择不同 physical media、不同 event label |
| generation-level 适用性 | PASS WITH LIMITATION | 适合事件识别生成对照，不适合宣称通用音频问答收益 |

## 允许范围

- 首轮最多冻结 20 组；剩余样本保留为 reserve-of-review；
- 组内条件为 `correct/mismatch/video_only/silence`；
- mismatch 固定使用同一 AVE_HF_EXPANDED/reserve 来源；
- 每个标签至少 1 组，`Speech` 等高频标签不得占据大多数；
- 训练使用过的 30 个 pilot physical media 全部排除。

## 结论

状态：`PASS_WITH_SCOPE_LIMIT`。

该数据可以作为独立 audio generation 测试，但结论只能解释为 AVE 事件识别场景下的 generation-level 对照，不能直接外推到通用音频问答。
