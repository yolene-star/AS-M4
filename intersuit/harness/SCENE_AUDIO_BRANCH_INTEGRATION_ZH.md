# 场景音频特殊分支接入说明

## 目标

最新场景音频能力以可选旁路接入原 M4，而不是替换原视觉主干或用户问题语音路径：

```text
视频帧 -> vision tower -> video features ----------------------+
                                                               +-> 残差融合 -> 原 M4 语言模型
场景音频 -> scene_audio_encoder -> 选窗/对齐/门控 -> audio delta +
```

音频分支只在以下条件同时满足时运行：

1. `enable_scene_audio=True`；
2. 调用方实际传入 `scene_audios`；
3. `scene_audio_window_mode` 不是 `disabled`。

统一判定入口为 `scene_audio_branch_is_active()`。训练、普通生成、并行生成和流式生成共享这一契约。即使数据批次仍携带缓存音频，行为回退关闭分支后也会直接走原 M4，不计算音频对比损失。

## 兼容性

- 保留 `scene_audio_encoder.*` 和 `streaming_av_module.*` 权重键，不移动已有模块，不需要转换旧 checkpoint。
- 保留底层 `MM_TUNABLE_PARTS` 名称：`scene_audio_projector`、`streaming_av_module`、`streaming_av_fusion`。
- 新增高层训练别名 `scene_audio_branch`：训练音频投影/适配层和完整 `streaming_av_module`，冻结 BEATs、torchaudio 等大型音频骨干。
- query speech 仍使用原 `speech_encoder` / `speech_projector`，不与场景音频混用。

## 推荐启动方式

只训练特殊音频分支的 Stage A smoke：

```bash
CUDA_VISIBLE_DEVICES=0,2,3,4 \
DEEPSPEED_CONFIG=none \
TORCHDYNAMO_DISABLE=1 \
DDP_FIND_UNUSED_PARAMETERS=True \
MM_TUNABLE_PARTS=scene_audio_branch \
bash intersuit/scripts/run_as_m4_stage.sh 12k-smoke
```

在原 M4 可训练部分上联合训练：

```bash
MM_TUNABLE_PARTS=mm_vision_tower,mm_mlp_adapter,mm_language_model,scene_audio_branch \
bash intersuit/scripts/run_as_m4_stage.sh 12k-canary20
```

精确行为回退：

```bash
AS_M4_ROLLBACK_MODE=behavior \
bash intersuit/scripts/run_as_m4_stage.sh 12k-smoke
```

`gate0` 只关闭残差影响，但仍执行音频编码、选窗和诊断；`behavior` 才是完全绕开特殊音频分支。
