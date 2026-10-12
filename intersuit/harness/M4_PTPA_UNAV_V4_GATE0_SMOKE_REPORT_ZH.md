# UnAV v4 到 M4 的 gate0 smoke 报告

## 结论

冻结 BEATs、v4 semantic projector 和既有 M4 audio adapter 已能在真实 7B 模型中完成推理；强制`gate=0`时，音频虽被完整解码和编码，但对视频特征的残差影响精确为零，最终预测和生成 token 与 behavior-off 完全一致。因此接口回退门禁通过。

这项结果不证明 v4 能提升问答效果。唯一 smoke 样本在两种条件下都输出不完整且错误的`The sound event audible in this video is`。

## 冻结配置

- 物理 GPU：GPU 2，`CUDA_VISIBLE_DEVICES=2`，程序内为`cuda:0`；
- Conda：`M4`；
- 基座：`M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1`；
- 样本：dev 非 test 的`ave_stage2_QSgqjJ2iglM_audio`；
- v4 projector SHA256：`3c514457...7bc866`；
- adapter：复用并冻结基座已有`audio_projector`；
- 条件：`V4_BEHAVIOR_OFF`和`V4_GATE0`，未运行非零 gate。

## 结果

| 项目 | behavior-off | gate0 |
|---|---:|---:|
| 预测 | `The sound event audible in this video is` | 相同 |
| 生成 token 数 | 9 | 9，逐 token 相同 |
| 音频窗口 | 0 | 20 |
| audio norm | 无 | 9.935860 |
| gate | 无 | 0.0 |
| delta/video ratio | 无 | 0.0 |
| 峰值已分配显存 | 25.96 GB | 26.17 GB |

scene-audio encoder和streaming module推理前后SHA均不变。运行结束后五张卡均回到11 MiB，无残留计算进程；本次启动无新增Xid、RmInitAdapter失败、AER、PCIe Bus Error或hung task。

## 下一门禁

gate0只证明“关闭音频不会改变原行为”。若继续非零 gate，必须新建极小规模`behavior-off/correct/mismatch`配对合同，预先限定残差上限、预测空输出/EOS门禁和停止条件；不得直接扩大到全量评测或训练。
