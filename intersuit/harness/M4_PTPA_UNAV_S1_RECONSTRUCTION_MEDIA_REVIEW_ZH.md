# UnAV-100 S1 重构：数据资格与媒体获取审查

## 结论

UnAV-100 metadata 资格为 `PASS`，媒体 canary2 为 `FAIL`，当前状态为
`blocked_media_canary2_youtube_bot_auth`。原 LLP S1 FAIL 不变；UnAV 路线尚未进入特征、效果或训练。

UnAV-100 是当前唯一满足“train/validation/test 都有公开事件类别与准确 onset/offset”的
顶会主候选。官方 train/validation/test 三个 split 共10,790个唯一视频、30,059个事件、
100类；物理 split 交集0，所有时间区间合法。

## 官方来源锁

- 项目：`https://unav100.github.io/`；
- 论文：CVPR 2023；
- GitHub commit：`1609deba4fbaf25222c6ee6c7d43b0d0ddfab20d`；
- Hugging Face revision：`daeb88bc14eeff48f8a4ac2836b67f6c350f847b`；
- metadata SHA：
  - train：`d8b1302fc12dc22a62ca23b592392cb000d00d2a487a70aa40876dde52742d55`；
  - validation：`551e1781c8a8fd77cb7a1daf0ad9b71346c615864de7bba6fb5dba3565dadc6b`；
  - test：`36a7d528772c1be736c105bfc65955255c822e99b7ceded64ef6e1b9c969df45`；
  - `download_video.csv`：`f1f9b36e607ed0cea04e818cf23027acd1cec4cdd9b7c118c93408e5654be735`。

Hugging Face CLI 0.36.0 在HEAD metadata检查处因代理/Content-Length兼容问题失败，未修改环境；
随后使用同一固定revision的官方HTTPS直链取得约850KB metadata。四个文件均通过预期大小和SHA。

## 规模与历史排除

| Split | 官方视频 | 官方事件 | 历史排除后视频 | 类别 | 每类最少唯一视频 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Train | 6,489 | 18,123 | 6,226 | 100 | 22 |
| Validation | 2,134 | 6,036 | 1,717 | 100 | 6 |
| Test | 2,167 | 5,900 | 2,098 | 100 | 12 |

排除源包括：410个既有历史UnAV物理ID、335个已用于selector的UnAV validation媒体、
LLP pilot150。排除理由可以重叠；最终按物理ID union计算。

Test 只完成metadata资格与历史排除，没有下载媒体、提取特征或访问效果。

## 媒体发布结构

Hugging Face 官方仓库共约65.78GB；媒体为13个分卷：前12卷各5GiB，末卷约1.35GB。
仓库不提供按单视频路径下载的媒体文件。另一条官方路线是`download_video.csv`配合
`scripts/video_download.py`：

1. `yt-dlp --ignore-config <URL> -o <id>_full_video.mp4 -f b` 下载完整源视频；
2. `ffmpeg -ss <start> -i <full> -t <duration> -vcodec libx264 -acodec aac ...`
   裁成官方片段。

官方脚本 SHA256：`b21188a2ae237dc85a973facf5ec1e6ead6b753fa25ab800252815f13549700c`。

## Canary计划

冻结计划共20条：train/validation各10，test 0；覆盖38类，单事件/多事件各10，短/中/长
duration分层为8/8/4。计划 SHA256：
`96a25a25d8c9b578544ada7ce889ae89820c225e4eb6d5a70a25e278bc633d49`。

## Probe结果

| Probe | 结果 | 凭据 | 媒体文件 |
| --- | ---: | --- | ---: |
| 严格官方 `-f b` canary2 | 0/2 | 无 | 0 |
| Node EJS + MP4/M4A兼容 canary2 | 2/2 | 无 | 0 |
| Node EJS + MP4/M4A兼容 canary20 | 20/20 | 无 | 0 |

严格官方命令失败原因是当前YouTube不再提供预合并`best`格式，不是视频失效。兼容路径显式
使用Node EJS并选择MP4视频流+M4A音频流；未使用cookie、client或PO token。

## 实际媒体 Canary2

授权范围为1条train和1条validation。两条在匿名下载阶段均返回HTTP 429和
`Sign in to confirm you’re not a bot`：

- requested：2；
- passed：0；
- failed：2；
- cookies used：false；
- media files created：0；
- test accessed：false。

结果 SHA256：`121696fd4e33ffb491f6408ee88e7f381b327d2d806ec3d03285475b159a910b`。

## 当前权限

- metadata audit：允许；
- media probe：已消费并关闭；
- train/validation媒体下载：关闭；
- canary20下载：关闭；
- test媒体/特征/效果：关闭；
- train/validation特征/效果：关闭；
- 训练：关闭；
- 再分发：关闭。

## 解除阻塞所需用户选择

推荐方案：提供重新登录后导出的新YouTube cookie文件，并明确声明“仅授权用于UnAV
canary2”。不能复用此前仅授权LLP canary的cookie。

备选方案：明确授权下载官方65.78GB、13分卷完整媒体归档。该方案不依赖YouTube会话，但
下载量和解压空间明显更大。

在用户作出选择前，不继续媒体下载，也不进入特征、效果或训练。
