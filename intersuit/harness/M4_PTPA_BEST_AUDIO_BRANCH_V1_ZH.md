# M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1 版本说明

日期：2026-08-10（Asia/Shanghai）

## 版本定位

该版本满足以下组合目标：

1. 模型基座严格使用最近 PT/PA 最优配置文件锁定的官方公开 `M4-LongVA-7B-Qwen2`；
2. 不导入 AS-M4 checkpoint 中语言模型、视觉塔或 projector 的训练后变化；
3. 只叠加最新场景音频分支权重；
4. PT/PA 最优运行参数以独立评测锁文件保存，不把评测参数混入模型权重；
5. 音频关闭时使用官方 M4 权重路径，音频开启时才加载旁路残差。

模型目录：

```text
intersuit/checkpoints/M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1
```

## 权重组成

- 官方 M4 基座权重键：`734`；
- 音频 overlay 权重键：`27`；
- combined index：`761`；
- overlay 大小：`54,117,448` bytes；
- overlay SHA256：`e95b87781d97b01ac26b0f50235b1588a23a32d93cee07de570cf60e5998bc68`；
- config SHA256：`359aed2660f9e8a3606d6d97d1fa89d5265d83415d414c56c3f21a5e17c2eeb8`；
- combined index SHA256：`202dbfc77a361e69e69a9fe10cd230922a26c597a19dcf86df071f6ca84d1470`。

模型目录约占 `52M`。官方四个大 shard 通过相对软链接复用，没有复制约 16GB 基座权重。

音频键来源：

- `25` 个键来自 `AS-M4-BEATs-semantic-contrast-canary150-v1/checkpoint-150`；
- 当前代码新增的两个 question-conditioned selector 投影不在旧 checkpoint 中，使用固定 seed `20260810` 确定性初始化；
- 当前正式运行锁定为 `micro_window`，上述两个 question-conditioned 权重不会进入该路径，但仍保存以保证当前代码 state dict 完整、加载无随机补权重。

## 音频运行配置

- encoder：冻结 BEATs；
- window mode：`micro_window`；
- window / hop：`1.0s / 1.0s`；
- top-k：`8`；
- fusion：`beats_simple_residual`；
- inference gate：`0.005`；
- residual scale：`1.0`；
- delta ratio cap：`0.0`。

## PT 最优评测锁

- 固定等距 `32` 帧；
- `gen=False`；
- token：`151644`；
- temperature：`1.0`；
- beta：`0.2`；
- 现有官方 M4 参考：`126/200 = 63.00%`。

## PA 最优评测锁

- 完整视频 `1 FPS`；
- 分辨率 `336`；
- 阈值：`mean + 2 * std`；
- grounding：`frame_count > 4`；
- 触发：`cnt > 4`；
- greedy，最多 `64` token；
- seed：`42`；
- 现有官方 M4 参考：Accuracy `21.50%`、Precision `31.82%`、IoU `14.02%`。

完整机器可读配置：

```text
intersuit/configs/m4_ptpa_best_audio_branch_v1.json
```

模型目录中的 `evaluation_config.json` 是指向该文件的相对软链接。

## 重建命令

所有 Python 操作必须使用 Conda `M4`：

```bash
PYTHONPATH=/home/yjm/M4-main/intersuit \
conda run -n M4 python \
  intersuit/scripts/build_m4_audio_overlay_checkpoint.py \
  --base-model intersuit/checkpoints/M4-LongVA-7B-Qwen2 \
  --audio-checkpoint intersuit/checkpoints/AS-M4-BEATs-semantic-contrast-canary150-v1/checkpoint-150 \
  --output-dir intersuit/checkpoints/M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1 \
  --runtime-lock intersuit/configs/dynamic_selector_v2_checkpoint_lock.json \
  --evaluation-lock intersuit/configs/m4_ptpa_best_audio_branch_v1.json \
  --model-version M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1
```

构建器拒绝覆盖已有输出目录。需要重建时，应先把旧目录移动到明确的备份位置，不能删除官方 M4 或音频训练 checkpoint。

## 已完成验证

- 构建器单元测试通过；
- 相关音频回归：`48 passed`；
- combined index `761` 键全部存在，missing/unmapped 均为 `0`；
- 当前代码期望音频键 `27`，overlay `27`，missing/unexpected 均为 `0`；
- 两个不同输出目录独立构建后，overlay/config/index SHA256 完全相同；
- 未启动 GPU。

## 资格边界

该版本当前标记为 `diagnostic_only`。原因是音频权重来源 checkpoint 未通过 dev760 正确音频选择性门禁，selector v2 oracle pilot 也已触发 stop gate。

这不影响它作为“官方最新 M4 基座 + 最新场景音频接线”的可复现新模型版本，但在完成新的 PT、PA、dev760 A/B 之前，不得宣称音频接入提升了模型效果。

## 下一步测试顺序

1. 有设备权限的 GPU preflight；
2. 同一模型 `behavior off` 与 `gate0` 精确回退 smoke；
3. 正确音频 1–2 条 smoke；
4. dev760 与 PT 小规模 pilot；
5. pilot 无灾难性退化后再运行完整 PT、PA、dev760；
6. 保存逐样本预测、融合诊断、配置、stdout/stderr、开始时间和 postflight。
