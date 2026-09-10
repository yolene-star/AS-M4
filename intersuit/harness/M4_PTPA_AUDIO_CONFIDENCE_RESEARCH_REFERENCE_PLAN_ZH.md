# M4/PTPA 音频问题条件化融合调研参考计划书

日期：2026-08-30（Asia/Shanghai）
状态：调研转工程前的简要计划，尚未授权实现、特征重算或训练

## 一、目标

借鉴 MUSIC-AVQA、Pano-AVQA 的问题条件模态选择，以及 CAV-MAE、ImageBind、LanguageBind 的跨模态语义对齐思想，解决 P3 relevance 接近随机的问题，同时保持 M4 原有视频能力。

目标不是复制论文模型，而是验证同一问题下 `correct audio` 是否稳定高于 `mismatch audio`，并让视觉问题不被无关音频干扰。

## 二、设计原则

- BEATs、视觉塔、Qwen2 主体和旧融合模块继续冻结，只训练轻量 Gate/selector。
- 问题表示改为冻结 Qwen2 contextual hidden state，禁止继续使用 raw token mean。
- 音频窗口和问题分别经过可训练 projection，再进入 query-conditioned selector；projection 不属于冻结特征 encoder SHA。
- selector 负责窗口相关性，Gate 负责最终音频影响大小；保留 `g_sync=1` 和 `gate=0` 回退。
- 论文总体 Accuracy 只作背景参考，不替代本项目的 Router/relevance 门禁。

## 三、执行阶段

1. **修订合同与接口**：锁定 contextual encoder 层数、pooling、mask、dtype、来源 SHA；明确 selector 的职责，并同步参数白名单、loss 和 checkpoint metadata。
2. **CPU/离线验证**：检查 shape、finite、确定性、因果性、projection 归一化和冻结参数；用同一批 300 对 correct/mismatch 做 raw/contextual A/B。
3. **特征资产重建**：A/B 不低于 raw 基线后，重新生成 5,400 条冻结特征。新 manifest 与旧资产分目录、分 schema，禁止混用旧 raw-question 特征。
4. **Gate canary**：按 `2-step -> checkpoint 审计 -> 20-step` 执行。20-step 要求 Router balanced accuracy >= 0.60、relevance ranking >= 0.55、Gate 非恒定且 loss/gradient finite。
5. **后续验证**：只有 20-step 全部通过，才评审 150-step、冻结 7B 验证和 formal test；失败时回到特征、标签、采样或优化分析，不降低阈值。

## 四、回退与证据

新特征、checkpoint 和报告使用独立 versioned 目录，不覆盖 v1、v5、v7 诊断资产。关闭 selector 或强制 `gate=0` 时必须回到原视频路径。每阶段保存配置、代码/模块 SHA、日志、指标和失败样本。

## 五、当前决策

目前只批准调研和合同修订；不批准代码实现、5,400 条特征重算、20/150-step 训练或 formal test。
