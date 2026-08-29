# UnAV v4 非零 gate 极小诊断报告

## 结论

v4 音频链路在真实 7B 模型中可以产生稳定的非零残差，且没有空输出、首 token EOS、NaN/Inf、冻结权重漂移或 GPU 错误。但正确音频与错配音频在10条dev样本上都没有带来准确率收益，二者预测也几乎相同，因此没有观察到内容选择性。

结论为“技术链路PASS、效果无收益、停止扩大”，不允许进入full dev、formal test或训练。

## 配置

- 物理GPU2，Conda `M4`；
- 同一`M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1`基座；
- 前10条dev非test样本；
- behavior-off、正确音频和跨物理视频错配音频；
- 固定gate=`0.005`，delta ratio cap=`0.05`；
- 冻结BEATs、v4 projector、既有adapter、M4和fusion权重。

## 结果

| 条件 | 正确数 | 相对behavior预测变化 | delta/video ratio |
|---|---:|---:|---:|
| behavior-off | 3/10 | — | 0 |
| correct | 3/10 | 2 | 5.117e-5–6.558e-5 |
| mismatch | 3/10 | 1 | 5.556e-5–6.466e-5 |

correct与mismatch只有1条预测不同；双方独有新增答对均为0。残差确实进入模型，但量级很小，而且没有形成“正确音频优于错配音频”的决策结果。

## 下一步

不扩大样本、不提高gate、不训练fusion。优先做只读失败分析：比较correct/mismatch的v4语义向量、既有`768→3584` adapter输出方向和语言模型首token logit变化，确认选择性在哪一层消失。只有定位并修正明确机制问题后，才重新建立新的极小诊断合同。
