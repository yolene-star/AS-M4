# Valor32k-AVQA official train pilot150 与媒体门禁报告

日期：2026-08-29（Asia/Shanghai）

## 1. 结论

本阶段状态为：

```text
PASS_MANIFEST_BLOCKED_SINGLE_TAR_MEDIA_ACCESS
```

official train 的 150 视频/450 问题分层 pilot manifest 已构建并通过数据泄漏门禁，但官方媒体
入口只提供单个约 38.2 GiB 的 `VALOR32K_videos.tar`，无法只下载 pilot 对应的 150 个文件。
当前合同不允许完整媒体包，因此媒体下载与后续音轨测试暂停。

## 2. pilot 构建合同

数据只来自 official train，并固定：

- `model=gpt4o`；
- `quality_rating=obvious`；
- 先删除 2 条精确重复问题；
- 每个视频同时拥有 visual、audio、audio-visual 三种问题；
- 每个视频各固定一条三模态问题，共 450 条问题；
- seed：`as-m4-cgf-valor32k-pilot-v1`。

音频问题类别配额：

| 类别 | 视频数 |
|---|---:|
| description | 40 |
| count | 35 |
| temporal | 35 |
| action | 20 |
| location | 20 |
| 合计 | 150 |

官方 train 的 `gpt4o+obvious` 去重后有 169,060 条问题、21,072 个视频；其中 18,660 个视频
同时拥有三种问题模态。location 音频题在三模态视频中仅有 24 个候选，因此本 pilot 已经对稀缺
location 类做了高比例覆盖。

## 3. 泄漏与排除审计

使用项目现有统一排除库存和 dev760 冻结 manifest：

- 统一库存声明 ID 数：6,262；
- dev760：760 条；
- pilot 与既有排除集合交集：0；
- pilot 与 official validation 的 `video_id` 交集：0；
- pilot 与 official test 的 `video_id` 交集：0。

manifest：

```text
intersuit/datasets/VALOR32K_AVQA_V2_AUDIT/pilot/valor32k_train_pilot150_v1.json
SHA-256: 71e3d4c2289fd8d14b2b62c98bad67716cd3ab43676d8fe91b9a8d9a411f443c
```

manifest 属于运行资产，不进入 Git；机器锁保存在
`intersuit/configs/valor32k_avqa_train_pilot150_v1_lock.json`。

## 4. 官方媒体入口审计

使用项目页给出的官方 VALOR 百度网盘分享和公开提取码验证后，分享页只包含一个文件：

```text
文件名：VALOR32K_videos.tar
fs_id：653871849642408
大小：41,046,312,960 bytes
约：38.23 GiB
目录形式：单个 tar，不是逐视频目录
```

因此无法在服务端只选择 pilot150。tar 文件索引不是集中位于文件头，使用 HTTP range 也不能在
不知道成员偏移的情况下可靠提取指定 150 个成员；流式过滤仍然需要传输整个 38.2 GiB 文件。

Hugging Face 当前只提供 official test 视频，不能替代 train pilot，否则会污染 formal test。
当前也没有发现作者提供的官方逐文件 train 镜像。

## 5. 当前禁止事项

- 未经新授权下载完整 38.2 GiB tar；
- 使用 HF test 视频代替 train；
- 从非官方镜像或未知来源补媒体；
- 使用 YouTube ID 猜测片段时间并重建不确定媒体；
- 在媒体可用性门禁前启动 Gate 训练、7B 推理或 GPU 测试。

## 6. 可选解决路径

### 路径 A：授权完整官方 tar

下载完整 `VALOR32K_videos.tar`，验证文件 SHA/成员表后只解出 pilot150。优点是来源官方、映射最
可靠；缺点是必须传输约 38.2 GiB，百度网盘可能要求账号或客户端，耗时和稳定性未知。

### 路径 B：用户提供已下载的官方 tar 或挂载目录

如果服务器或其他机器已有官方 tar/解压目录，可只读核对文件大小、成员名和 SHA，再提取
pilot150。这是最省网络成本的路线。

### 路径 C：向作者索取逐文件 train 入口

等待作者提供 Hugging Face/Google Drive/对象存储逐文件镜像，之后按 manifest 下载。来源最
清楚，但完成时间不可控。

### 路径 D：更换母数据集

改用具有可逐文件获取 train 媒体的 AVQA 数据集。它会改变已提交的 P2 数据路线，需要重新审计
问题模态标签、媒体质量、许可证和泄漏，不能静默替换。

## 7. 推荐

优先检查用户是否已经拥有 VALOR32K 官方 tar；若没有，再由用户明确决定是否接受完整 38.2 GiB
下载。当前不建议为了省事使用 formal test 或不明镜像。
