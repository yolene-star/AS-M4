# AVE audio generation smoke2 v3 结果

日期：2026-09-02（Asia/Shanghai）

## 结论

工程状态：`PASS_AUDIO_GENERATION_SMOKE` / `PASS_AUDIO_GENERATION_OUTPUT_AUDIT`。

科学状态：`SELECTIVITY_NOT_PROVEN`。

物理 GPU2 完成 2 组、8 条 `correct/mismatch/video_only/silence` generation。所有输出非空，silence 与 video-only 在两组中逐字一致，模型与 adapter 权重在只读运行中未变化；但 correct 与 mismatch 的命中数同为 `1/2`，没有证明正确音频优于错配音频，因此不能扩大到完整 20 组。

## 运行证据

- 完整 20 组 Manifest SHA：`e97d0b2e7ed0dbe8dc3d8a190a13d5b6696c0f26eb33b041e0eebac4d4ca9b5c`；
- 本次 2 组 smoke 子集 Manifest SHA：`a7ceaa7fe964543f105ee5fbef4de65096fd9842a4391baf966e78b388893ec7`；
- Adapter checkpoint SHA：`9169a61c48308e13f08f81435b903596f68104b797eba43896a1f2210eb8cca5`；
- 输出 SHA：`a9962bb5651ee1f7c0d78ae1dd1152790ebe16b1110e0908efa54858fad8dcf0`；
- 运行时间：16.73 秒；
- CUDA peak allocated：24,034,051,072 bytes（约 22.4 GiB）；
- GPU 后状态：5 张卡均 11 MiB、0%、450W；
- 内核检查：未见本次新增 NVRM/Xid/AER/PCIe/hung-task 记录。

## 两组结果

| 条件 | 命中数 |
|---|---:|
| correct | 1/2 |
| mismatch | 1/2 |
| video-only | 0/2 |
| silence | 0/2 |

- 组 0（Accordion）：correct 与 mismatch 都输出 Accordion；video-only/silence 输出无关内容。
- 组 1（Acoustic guitar）：四个条件均只输出泛化的 guitar 描述，没有命中完整目标短语 `Acoustic guitar`。
- 两组的 silence 与 video-only 逐字一致，结构性 silence bypass 生效。

## 决策

本结果证明 generation 路径、audio-token 注入、checkpoint 加载和 silence 回退可以工作，但没有证明 correct 音频相对 mismatch 的选择性。按预注册停止边界：

- 重新关闭 `gpu_generation_smoke`；
- 不扩大到 20 组；
- 不运行 150-step 或 formal test；
- 下一步先分析视觉泄漏、答案评分口径和 correct/mismatch 输出差异，必要时增加不训练的 logit/NLL 诊断，而不是扩大 generation 样本。
