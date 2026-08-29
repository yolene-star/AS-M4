# M4/PTPA 音频置信度融合 P2 数据合同结果

## 结论

`AS-M4-CGF-Valor32K-v1-pilot150` 已通过 P2 构建器与独立审计器双重门禁，状态为
`PASS_P2_INDEPENDENT_AUDIT`。本阶段只冻结数据映射和确定性扰动配方，没有运行 GPU、7B、
Gate 训练或 formal test。

P2 通过只表示“训练题目和对照条件构造正确”，不表示 Gate 已训练，更不表示正确音频能提高
M4 的回答准确率。

## 来源与媒体

- 母数据集：Valor32k-AVQA v2.0 official train；
- 许可：CC BY-NC 4.0；
- 官方 tar 大小：`41,046,312,960` bytes；
- tar SHA-256：`c44ef39d5add2a8f547906b094ae67835897fd83920183d87955a3429e5a1d76`；
- pilot manifest SHA-256：`71e3d4c2289fd8d14b2b62c98bad67716cd3ab43676d8fe91b9a8d9a411f443c`；
- pilot 媒体：`150/150` 可读，`150/150` 同时包含音频流和视频流；
- 提取媒体总大小：`194,768,833` bytes；
- 时长范围：`2.047–10.127` 秒，均值约 `9.940` 秒；
- 采样率：150 个均为 `44.1 kHz`；
- 声道：121 个双声道、29 个单声道。

每个媒体资产均记录相对路径、文件大小、SHA-256、时长、采样率、声道和流式窗口数。
媒体资产清单 SHA-256 为
`0c3b23f86a69d91594e7206c4f71e66d4a3fbf15e6e1e8205944bda6336587cc`。

## 条件合同

150 个物理视频各自固定 visual、audio、audio-visual 三个问题，共 450 个 `pair_group_id`。
每组固定 14 个条件，总计 6,300 条：

- 第一阶段 5,400 条：`clean_correct`、9 个 `noise_type × SNR`、`silence`、`mismatch`；
- 第二阶段冻结资产 900 条：`shift_-1.0s`、`shift_+1.0s`；
- 噪声类型：white、pink、same-dataset-background；
- SNR：20、10、0 dB；
- shifted 条件进入第一阶段的记录数为 0；
- mismatch 自映射、跨 split、缺少目标长度规范的记录数均为 0；
- mismatch 全部优先保持相同选择类别且答案不同，本次 category fallback 为 0。

mismatch 音频在消费时必须按目标媒体执行确定性重采样、声道转换、截断或补零，确保目标时长
和流式窗口数一致。噪声、静音和 shift 当前冻结为虚拟波形配方，不重复物化数千份媒体；训练
数据加载器必须按 manifest 中的 recipe 与 seed 生成，并接受复现测试。

条件 manifest SHA-256 为
`b24b1583d9360b7940e9949851a8cdc86fedaa684fed67b55e83d02fffdd58ba`。

## 独立审计

独立审计器不信任构建器的 PASS 字段，重新逐组检查：

- 每组 14 个条件是否完整且唯一；
- 所有噪声 seed 是否可由 `pair_group_id/noise_type/SNR` 重算；
- Router、Quality、Relevance、Sync 标签是否符合预注册定义；
- correct、mismatch、same-dataset-background 的物理来源是否合法；
- mismatch 是否无自映射、同 train split、相同选择类别且答案不同；
- mismatch 的目标采样率、声道、时长和窗口数规范是否完整；
- shifted 是否只存在于 `second_sync_only` 且不进入首轮训练。

结果：条件集合、seed、标签、阶段、来源五类错误计数全部为 0。

## 边界与下一步

当前只允许进入 P3 运行前合同设计。P3 合同必须继续冻结 BEATs、M4/Qwen2、视觉塔、旧 audio
projector 和 fusion，并规定只有 Gate 与必要低维投影可以更新。合同评审和数据加载器复现测试
通过前，不启动 `2-step`，不接入 7B，不运行 P4，也不访问 formal test。

下一步先实现只消费本 manifest 的虚拟扰动数据加载器与配方复现测试，再建立 Gate-only
`2-step -> 20-step -> 150-step` canary 合同。
