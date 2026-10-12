# AVE audio generation review manifest 构建与 CPU 审计结果

日期：2026-09-01（Asia/Shanghai）

## 结果

状态：`PASS_AUDIO_GENERATION_REVIEW_BUILD` / `PASS_ADAPTER_PILOT_MANIFEST_AUDIT`。

本轮只使用 `AVE_HF_EXPANDED` 的 `reserve` split，未混入 AVUT、MUSIC-AVQA、LLP 或其他数据集。

## 资产

- 输入：`intersuit/datasets/AS_M4_STAGE2/reserve_manifest.json`；
- 输出：`intersuit/harness/artifacts/audio_generation_review_ave_reserve_v1/manifest.jsonl`；
- 输入 SHA256：`e7ad0ca5a5c8438a2a81e908a7567a5633351fb4f2ae77d074745d6c8ea9a1f0`；
- 输出 SHA256：`e97d0b2e7ed0dbe8dc3d8a190a13d5b6696c0f26eb33b041e0eebac4d4ca9b5c`。

## 内容与格式

- 20 个 pair group、80 条记录；
- 每组完整包含 `correct/mismatch/video_only/silence`；
- 20 个 correct 目标 physical media，标签一组一个，覆盖 20 个不同 AVE 事件标签；
- source dataset 唯一为 `AVE_HF_EXPANDED`，split 唯一为 `reserve`；
- 训练 pilot 的 physical media 已排除；
- 视频和音频路径均存在，音频 SHA 字段完整；
- correct/mismatch 共用同一问题和答案；
- mismatch 来自同一 AVE reserve，且不同 physical media、不同 event label；
- video-only 无音频；silence 保留 correct 音频源，后续由现有 loader 物化为同形状全零波形。

## 实验边界

该 manifest 适合做 AVE 事件识别场景的 generation-level 对照，不代表通用音频问答测试。当前只完成数据构建与 CPU 审计，未加载 7B、未访问 GPU、未运行 generation。

下一步必须另行申请 GPU2 generation smoke；在 smoke 结果和 visual-only 独立测试通过前，保持 `gpu_150step=false`、`formal_test=false`。
