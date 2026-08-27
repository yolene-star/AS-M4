# 音频语义、答案表示与融合位置重构计划审查

审查版本：`v3`（2026-08-27）

## 1. 审查结论

结论：`PASS_PLAN_ONLY_CANARY20_MEDIA_QUALIFIED`。

本计划正确承接了 Oracle selector 官方门禁失败：selector 路线保持停止，新路线只授权 CPU、只读的 S0 合同与数据审计。GPU、特征提取、训练、融合修改、confirm 和正式 test 均未授权。

v3保留LLP原S1正式FAIL和“停止S2/S3/S4”决定，新增独立UnAV S1-R强时间重构。
UnAV官方13卷、10790成员映射和train10+validation10 canary20媒体资格均PASS；这只证明
媒体与强标注入口可用，不证明冻结表示有效。全量train/validation解压、特征、validation效果、
test和训练继续未授权。

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
6. LLP 已将 112 份历史 manifest 快照、AVE、AS-M4 Stage2、LongVALE 和 AVQA 归一为
   54,664 个保守 casefold YouTube 物理比较键。AVQA annotation-only 镜像固定 revision 后的
   `40425+16910=57335` QA、57,015 唯一 clip 与官方数量完全一致，解析出 51,157 个源 YouTube ID；
   未使用其 56,666 个第三方视频子集。旧 pilot 被发现与 AVQA 重叠 3 个视频，已作废；排除完整
   历史后 LLP train/validation/test 候选为 `9468/615/1137`，重选的 train 100 + validation 50
   pilot 与完整并集重叠为 0。M4-IT 本地 9963 行是 image/speech instruction ID，不伪装成
   YouTube ID。仓库 GPL-3.0 不外推到第三方 YouTube
   视频；经用户授权使用本机 Firefox profile 的 150 条 authenticated metadata-only probe
   结构完整，但只有 9 条取得 public metadata，141 条失败（reload 81、反机器人 49、
   unavailable 4、其他 7），且 150 条 license 字段全部为空。未输出 cookie、未取得媒体、
   未解锁下载。metadata probe 与下载后 SHA/ffprobe/音轨/时长
   验收合同与正式媒体 downloader 已实现/Review，逐视频许可、存活率和媒体审计仍未完成。
   AVQA 官方项目页、固定 GitHub 提交与 `YouTube ID/start/split` 合同继续作为主来源证据；
   Hugging Face 只承担 annotation 镜像传输，不承担视频媒体资格或正式结果来源。
   独立 official-protocol downloader 已实现并通过 plan-only Review：固定官方 commit、`best`
   源下载、精确 10 秒、`libx264+aac`、11 位 ID 兼容命名、逐条 resume、临时目录原子提交和
   SHA/ffprobe/音轨/时长后验收。canary2 与 pilot150 计划已分别锁定 2/150 行；聊天中
   暴露的 cookie 未确认轮换，禁止把已暴露 cookie 写盘或重放。
   用户随后明确要求跳过账号轮换继续；该授权只转换为 `anonymous canary2`，不授权使用已暴露
   cookie。匿名 canary 不携带任何凭据，登录/反机器人/不可用错误均按失败保留；pilot150
   仍禁止，canary 未通过前不改变 metadata、特征、训练或效果权限。
   anonymous canary2 已运行并在媒体传输前 `0/2` 失败，两条均为 YouTube
   `The page needs to be reloaded`；未生成视频，临时目录已清理。该结果不允许改跑 pilot150，
   也不允许回退使用聊天中暴露的 cookie。
   用户随后提供重新登录后导出的新 cookie 文件；脱敏比较确认文件不同、15/22 个同名 cookie
   值变化并新增 3 项，权限 600、格式/过期检查通过。rotated-cookie canary2 再次 `0/2`：
   两条均进入媒体 fragment 阶段后遭 HTTP 403，yt-dlp 报 YouTube 强制 SABR、最终
   `The downloaded file is empty`；未生成视频。当前下载链还提示 base Python 3.9 支持已弃用，
   在独立升级 Review 前不得重跑或进入 pilot150。
   用户随后授权在既有 M4 环境更新 stable yt-dlp。已安装 `yt-dlp 2026.08.19` 与 EJS；
   `[default]` 首次把 websockets 11.0.3 升到 16.1.1，触发既有 gradio-client `<12` 冲突，
   已立即恢复 websockets 11.0.3并验证 gradio-client 导入。`pip check` 只剩升级前已有的
   decord 平台提示。新 canary2 authorization 已绑定 M4 环境、yt-dlp 版本与可执行文件 SHA；
   只允许再运行 canary2，pilot150 仍禁止。
   stable `2026.08.19` canary2 已运行并再次 `0/2`，两条均在媒体传输前报
   `The page needs to be reloaded`，生成视频数 0。stable 更新未关闭下载 blocker；nightly、
   player client、JS runtime 或 PO token 属于下一层协议变更，必须独立 Review/授权，不能直接尝试。
   用户随后明确要求新建隔离下载环境。`llp-download` 已按 Python 3.11.15、nightly yt-dlp
   `2026.08.20.234504`、EJS 0.8.0、FFmpeg 8.1.2、Node 26.5.1 建成，`pip check` 无损坏依赖；
   环境 spec 与三个关键可执行 SHA 已冻结。首次 conda-forge 全量求解因 repodata 网络分块中断，
   改用 defaults 两阶段创建；FFmpeg+Node 联装时 Node Content-Length 中断，拆分重试后均成功。
   新 authorization 只允许该隔离环境重跑 canary2，M4 不再承担下载，pilot150 仍禁止。
   隔离 nightly canary2 已运行并仍为 `0/2`，两条均在媒体传输前报 page reload，生成视频数 0。
   该结果排除了 M4/base 依赖污染、Python 3.9和旧 stable 版本作为充分原因；剩余 client/PO-token/
   账号-IP 协议问题未解决，必须停止，不得继续堆叠参数或运行 pilot150。
   后续按官方 EJS 指南发现 Node 必须显式以 `--js-runtimes node:/path` 启用；此前只安装未启用，
   因此“隔离 nightly 失败”不能代表 Node+EJS 失败。显式启用后 metadata-only canary2 达到
   `2/2 metadata_available`、public 2、probe_failed 0，D2 门禁通过；license 字段仍为空。
   authorization 已绑定相同 Node runtime，只允许再运行媒体 canary2，pilot150 仍禁止。
   首次 Node+EJS 媒体 canary 因官方 `-f best` 不存在而 `0/2`；两条 `--list-formats` 均证明
   MP4 video-only 与 M4A audio-only HTTPS 流存在。兼容路线因此冻结为
   `bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio` 并合并 MP4；仅源获取选择改变，
   官方 FFmpeg 10 秒、libx264+aac 与 ID 命名不变。新格式仍只允许 canary2。
   兼容媒体 canary 随后已完成源下载/合并，但 defaults FFmpeg 因无 GPL `libx264` 报
   `Encoder not found`。conda-forge 替换再次因 repodata `IncompleteRead`失败且环境未改变；
   服务器已有静态 FFmpeg 3.1.3 经核验包含 libx264+A​​AC且无动态依赖，已复制进隔离环境并绑定
   SHA。官方未锁 FFmpeg 版本，只锁编码器，因此该修复恢复而非放宽官方输出合同；仍只重跑 canary2。
   最终 Node+EJS+分离 MP4/M4A+静态 libx264 canary2 达到 `2/2`：两条均为精确 10.000s、
   H.264+A​​AC、44.1kHz 双声道，独立 SHA/ffprobe 复验通过，临时目录为空。canary2 权限已消费并
   关闭；用户 cookie 授权仅限 canary2，因此 canary20/pilot150 均不得自动启动。
   用户于 2026-08-22 明确要求继续下载直到成功，按冻结顺序解释为先授权 canary20；20 行计划
   SHA 已锁，单线程且每条间隔 5 秒，复用已有 2 条时仍重新验收。canary20 只允许媒体资格，
   特征/效果/pilot150 仍为 false；任何系统性 reload/403/empty-file 必须停止。
   canary20 已完成 `20/20`：复用 2、新下载 18、失败 0，总计 42,586,025 bytes；H.264+A​​AC
   与 SHA 全通过，时长 10.000–10.03434 秒，18 条双声道、2 条单声道。声道保持源属性且不违反
   官方合同。canary20 权限已关闭，pilot150 现仅开放媒体下载；特征/效果仍为 false。
   pilot150 首轮为 137/150；SSL 与 decoder 两项分别通过同格式重试和 avc1源格式重试恢复，
   得到 139 个合格媒体。剩余 11 个为永久缺失（Private 6、unavailable 4、age-restricted 1），
   train/validation合格数 93/46。全部139条为H.264+A​​AC，时长9.130998–10.064秒且在±1秒门禁内。
   在效果访问前按原 stable-key、同 split、排除完整历史与原pilot生成7 train+4 validation backfill；
   计划11行、历史/原pilot重叠0、非内容/效果选择。只开放backfill11媒体下载，pilot150权限关闭。
   backfill11 首轮8/11，3个替补均因上传账号终止而 unavailable；合格总数达到147。按相同规则
   继续排除原pilot与首轮11行，生成round2 3行（train2/validation1），历史/先前计划重叠0、
   非内容/效果选择。只开放replacement3媒体下载，所有特征/效果权限仍为false。
   replacement3 已 `3/3`，可访问媒体总数达到150；但直接拼接后的标签最低频次降为train3/
   validation1，不满足原4/2门槛。最终覆盖修复以139个固定原媒体为基数，先最大化未满足标签、
   stable-key打破平局，再填满7+4；11行中复用4、新下载7，预计最低频次恢复4/2，全部缺口清零，
   历史/原pilot/已知失效backfill重叠0。只开放coverage-repair媒体下载。
   coverage-repair首轮10/11，唯一Blender候选unavailable；round2排除该候选后复用10并新增1条，
   达到11/11。最终manifest为原pilot合格139+覆盖修复11，共150个唯一物理媒体，train/validation
   `100/50`、25类、最低频次`4/2`、历史重叠0。逐文件H.264+A​​AC/44.1kHz/SHA/ffprobe通过，
   时长9.046009–10.064秒，总字节336,984,348。媒体状态为`qualified_effect_locked`；下载权限
   已消费并关闭，特征/效果/训练继续false。逐视频YouTube license仍无法验证，是剩余独立Review边界。
   独立license ledger已覆盖最终150行：媒体合格150，逐视频license验证0，CC BY/标准YouTube
   已验证均为0。本机无YouTube Data API key；LLP仓库GPL不传递给第三方YouTube媒体，公开视频/
   成功下载也不证明训练或再分发权。当前状态为
   `blocked_pending_per_item_license_or_institutional_authorization`；可通过API查询`status.license`、
   机构/法务内部非商业研究授权记录，或重建CC BY子集来关闭。本Review不是法律意见。
   为回答“是否严格执行官方原获取层”，另建 `llp-official-download`：Python 3.8.20、
   youtube-dl 2021.12.17、FFmpeg 8.1.2。两条直接逐字执行官方
   `youtube-dl --ignore-config URL -o <id>_full_video.mp4 -f best`，不使用 cookie/client/PO token，
   均在 webpage/API JSON 阶段报 page reload，源媒体 0、FFmpeg 未到达。官方完整 wrapper 另需
   pandas 0.24.1 读取 CSV，但该非因果依赖不能修复已直接复现的 YouTube 获取失败。
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

## 3A. v3 UnAV S1-R计划审查

机器合同：`intersuit/configs/m4_ptpa_unav_s1r_plan_v1.json`，SHA256
`28f1821a5fef27371da7b16df55f4ecedc3bc70673707f7ab7e19b4fac133c80`。

审查结果为 `PASS_PLAN_ONLY`，理由如下：

1. LLP原S1结果报告和失败分析SHA已锁，状态固定为`FAIL_UNCHANGED`，禁止改阈值或改名晋级；
2. UnAV与LLP任务责任分开：UnAV只检验强onset/offset是否修复prototype geometry和margin；
3. train只做development，validation只在候选冻结后访问一次，test保持媒体/特征/效果三重封存；
4. 重叠官方事件按多标签处理，same-video negative必须处于所有官方事件均不活跃的窗口；
5. 100类随机基线锁为0.01，macro Recall@1与balanced accuracy门槛均为0.21；margin点估计和
   95% CI下界必须严格大于0，correct必须高于mismatch，三个固定seed方向一致；
6. canary20授权已经消费，当前所有全量媒体、GPU、特征、效果、test和训练权限均false。

仍未关闭的blocker：

- 6226个train和1717个validation合格媒体尚未获得全量选择性解压授权与逐文件资格；
- 冻结BEATs/teacher输入归一化、窗口缓存格式和输出manifest尚未形成新Review；
- validation internal confirm尚未生成sealed manifest，效果访问次数仍为0；
- test 2098个合格ID保持封存，不能因validation通过自动开启；
- 原视频逐文件许可仍未验证，当前只允许项目操作者授权的本机内部非商业媒体资格工作。

因此`next_stage_allowed=false`。下一步只能申请S1-R全量train/validation媒体资格授权；
不得直接提特征、看validation效果或训练。

## 4. v3 数据责任审查

### D-001：LLP 作为历史冻结S1

结果级冻结。LLP已完成100 train+50 validation的冻结BEATs评测，Recall高于随机但semantic
margin和CI门禁失败；formal test未访问。该FAIL不能被UnAV结果覆盖。

### D-001R：UnAV-100作为S1-R重构候选

计划级通过、媒体canary级通过。UnAV提供100类强onset/offset，归档成员与metadata精确匹配，
canary20全部具备H.264视频与AAC音频并通过完整解码。它尚未获得全量媒体、特征或效果资格，
所以当前不能声称S1-R上界通过。

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

现在只批准UnAV S1-R的科学合同，并承认其归档与20条媒体canary合格。
还没有批准全量train/validation解压、提特征、访问validation/test效果、改模型或训练。
人话说：试卷和20道样题已经验真，但还没批准正式开卷考试。
