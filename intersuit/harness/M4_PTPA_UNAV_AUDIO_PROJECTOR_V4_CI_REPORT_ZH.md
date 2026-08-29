# UnAV 音频 projector v4 物理视频级 CI 报告

## 结论

v4 active hard-negative projector 在冻结 UnAV validation 上保持了 raw BEATs 的类别召回水平，并对正类相对最强错误类的 margin 给出了稳定、可重复的提升。该结果只证明纯音频表示边界改善，不证明 M4 问答效果提升。

## 数据与边界

- 特征清单：7941 个 train/validation 视频，SHA256=`57b0bc55...eb935f0`；
- 原型来源：6224 个 train 视频；
- 评估：1717 个 validation 物理视频、4070 个事件 query、2413 个唯一 video-label query；
- bootstrap 聚类单位：物理视频；
- seed：17、23、47；每个 seed 5000 次重采样；
- formal test 未访问，BEATs 和 M4 均未训练。

## 点估计

| 指标 | raw BEATs | v4 | v4 - raw |
|---|---:|---:|---:|
| Macro Recall@1 | 0.637547 | 0.637742 | +0.000195 |
| 正类减最强错误类 margin | 0.015302 | 0.018008 | +0.002706 |

raw 正确的唯一 video-label query 在 v4 下保持正确的比例为`99.67%`。

## 三 seed 95% CI

| seed | Macro Recall 差值 | v4 margin | margin 差值 | raw 正确保持率 |
|---:|---:|---:|---:|---:|
| 17 | [-0.002042, 0.002239] | [0.015861, 0.020172] | [0.002376, 0.003040] | [0.993498, 0.999331] |
| 23 | [-0.002122, 0.002314] | [0.015970, 0.020101] | [0.002392, 0.003028] | [0.993355, 0.999332] |
| 47 | [-0.002029, 0.002270] | [0.015866, 0.020151] | [0.002378, 0.003035] | [0.993382, 0.999334] |

三个 seed 的 Macro Recall 差值 CI 都包含 0，因此不能宣称 v4 显著提高召回；同时 v4 margin 和 margin 差值的 CI 下界全部严格大于 0，因此可以表述为“召回与 raw BEATs 统计上不可区分，margin 稳定提升”。

## 实际影响

v4 已满足进入 M4 gate0 接口诊断的纯音频统计前置条件，但仍需通过真实 7B behavior-off/gate0 回退验证。GPU 驱动查询恢复前不运行该诊断；gate0 通过前不授权非零 gate correct/mismatch，也不训练 fusion、M4 或 BEATs。
