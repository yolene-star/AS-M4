# P3 冻结特征预计算 GPU2 极小 smoke 结果

## 结论

状态为 `PASS_P3_FROZEN_FEATURE_SMOKE`。本结果只证明真实 M4/BEATs/视觉塔/问题 embedding
能够生成 Gate-only v2 冻结特征，并通过独立资产审计；不证明 Gate 已训练，也不证明正确音频
能改善 M4 最终答案。

## 锁定范围

- 代码提交：`91b7c74`；
- 物理 GPU：GPU2，程序内逻辑设备 `cuda:0`；
- 两条 official train pilot 条件：一条 visual clean、一条 audio clean；
- 两条来自同一物理视频，用于验证视觉缓存和问题路由输入，不访问 formal test；
- 没有 optimizer、反向传播或参数更新。

## 结果

- 独立审计：`PASS_P3_FROZEN_FEATURE_AUDIT`；
- feature manifest SHA：`999eb9b648c6bcc0d9967a18a46778c64d2a7d8d5959c9cf870fdc1578e95ea2`；
- 两条均为 `[1,9,3584]`，时间范围从 `[0,2]` 到 `[8,10]`；
- quality 监督窗口 18 个，relevance 监督窗口 9 个；
- 所有特征 finite，audio/video/question 三类运行时来源 SHA 均已记录；
- 同一 clean 媒体的 audio/video 特征一致，两道不同问题的 question norm 分别约
  `0.3175` 和 `0.3234`，符合预期；
- GPU 前后均为 11 MiB，进程退出后无残留，功率限制为 450W。

## 警告与边界

视觉塔 staged 初始化期间打印了 meta-copy warning；但运行时模块 state SHA 成功计算，视觉
forward 也输出 finite 特征，说明最终运行模块已实体化。后续入口已增加“任何残留 meta 参数立即
失败”的显式检查。

本机 `dmesg` 不支持 `--since`，当前用户的 `journalctl` 也无法完整读取系统日志。可见的完整
`dmesg` 相关尾部中，最新记录仍是 smoke 前一天的 NVIDIA 模块加载，没有本次运行后的 Xid、
AER、PCIe error 或 hung task。该可见性限制已写入结果锁，不把它伪装成完整 journal 审计。

## 下一步

本结果不自动开放完整 5,400 条预计算。下一阶段应先设计去重和缓存合同：同一物理视频的视觉
特征、同一问题的 question 特征以及相同音频 recipe 的 BEATs 特征必须复用，避免按 5,400 条
重复运行冻结 7B/视觉塔。缓存设计和磁盘预算通过 CPU 审计后，再单独授权完整特征生成。
