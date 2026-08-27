# UnAV-100 S1 重构：数据资格与媒体获取审查

## 结论

UnAV-100 metadata、官方13卷归档完整性和无解压成员审计均为 `PASS`，当前状态为
`canary20_media_qualified_effect_locked`。早期YouTube媒体canary2的 `FAIL`
保留为历史获取证据，不再阻塞官方归档路线。原LLP S1 FAIL不变；UnAV路线尚未解压媒体，
也未进入特征、效果或训练。用户已在收到精确范围提示后以“继续”授权：仅从已验证归档
选择性解压冻结canary20的10条train和10条validation，test为0；该授权已消费并关闭。

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

Test 的字节随不可拆分官方归档以不透明形式保存在本地，但只完成metadata资格、历史排除和
成员ID清点；没有解压、解码、提取特征或访问效果。

## 媒体发布结构

Hugging Face 官方仓库共约65.78GB；媒体为13个分卷：前12卷各5GiB，末卷约1.35GB。
仓库不提供按单视频路径下载的媒体文件。另一条官方路线是`download_video.csv`配合
`scripts/video_download.py`：

1. `yt-dlp --ignore-config <URL> -o <id>_full_video.mp4 -f b` 下载完整源视频；
2. `ffmpeg -ss <start> -i <full> -t <duration> -vcodec libx264 -acodec aac ...`
   裁成官方片段。

官方脚本 SHA256：`b21188a2ae237dc85a973facf5ec1e6ead6b753fa25ab800252815f13549700c`。

## 官方归档完成与结构审计

用户明确授权下载官方65.78GB完整归档后，part_00至part_08由断点curl完成，part_09至part_12
由同一固定revision的Hugging Face Xet handoff完成。组合终态重新流式计算全部13卷SHA：

- 归档卷：13/13 verified；
- 压缩总字节：`65,775,604,313`；
- 组合manifest SHA256：`576c46a0675b0a31b6a4c5c9a696efc63d880582133de98448b525982e236794`；
- Xet handoff TSV SHA256：`e87c5a5db097235fbd14ab1fff69791b7e2bb5b2f23fd7ae29ac0204ffa7e490`；
- 活跃下载进程：0。

在不写出任何MP4的条件下，流式扫描完整gzip/tar：

- 成员总数：10,791（目录1、普通MP4 10,790）；
- metadata物理ID匹配：10,790/10,790；
- train/validation/test：6,489/2,134/2,167；
- 历史排除后：6,226/1,717/2,098；
- 路径穿越、symlink/非规则成员、重复、缺失和多余ID：均为0；
- member index SHA256：`2c28d929d5f4eb6a8d8c60530eb9800c339513b532e0483078577569552ed345`；
- summary SHA256：`653a33f840906b932ab073bef9f605aa5b2154030a17fd79fd45f9283c47db0a`。

该PASS只关闭下载和归档结构blocker，不等于逐媒体解码、音轨、时长、许可、特征或效果资格。

## Canary20选择性解压与媒体资格

严格按冻结20行计划从分卷tar流式选择性写出媒体，未做一般归档解压：

- requested/passed/failed：20/20/0；
- split：train 10、validation 10、test 0；
- 总字节：`112,551,427`；
- 容器：MP4 20；视频H.264 20；音频AAC 20、44.1kHz 20；
- 音频声道：mono 5、stereo 15；
- metadata与媒体最大时长绝对偏差：0.529秒，小于冻结1秒门禁；
- 完整音视频解码：20/20；16kHz单声道音频解码：20/20；
- 额外媒体文件、媒体SHA mismatch、test媒体访问：均为0；
- 特征、效果、训练：均未执行。

结果SHA：summary `d9a495119f8cc8a25d2d95179d491b624dcfc6c227915c4605c5c9d8e5b3de42`，
results `03943a3d402e761915641dab0ab63e9c5bc025559196f072468591b703b7e883`，
independent audit `ab6206c3d466ac7abc4a410b85281fd76a9cff730bbed44a535f7717f322506f`。
独立审计状态为 `PASS_INDEPENDENT_CANARY20_MEDIA_AUDIT`。

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
- 完整归档下载：已消费并关闭；
- 归档成员清点：已完成并关闭；
- 选择性train/validation解压：canary20已消费并关闭；
- test解压/解码：关闭；
- canary20下载：关闭；
- test媒体/特征/效果：关闭；
- train/validation特征/效果：关闭；
- 训练：关闭；
- 再分发：关闭。

## 下一阶段门禁

权威S1计划仍需后续修订，明确UnAV仅为保留原LLP FAIL前提下的独立 `S1-R` 重构候选。
当前授权只关闭其前置媒体canary，不等于批准S1-R特征或效果。

canary20只允许10条train和10条validation，test为0；验收范围仅包括容器、音轨、时长、
事件边界和解码。canary通过后还需独立Review，才能申请全量合格train/validation选择性解压。

canary20媒体资格、S1-R权威计划修订和独立Review均已通过。用户随后以“继续”授权仅对
历史排除后的6226个train和1717个validation执行全量选择性解压与逐文件媒体资格；总计
7943文件、48,993,281,767字节，test为0。该授权不包含特征、效果、训练或再分发，执行完成
或失败后立即关闭。
