# UnAV v4 新 fusion 非零 gate 极小诊断合同

本合同用于比较 `bypass` 与 `lightweight` 两种新 fusion projector 在正确/错配音频下的真实 7B 行为。每种条件最多 10 条 dev 样本，只使用物理 GPU2；不访问 formal test，不训练，不解冻 BEATs、v4 或 M4。

固定 gate=`0.005`、残差比例上限=`0.05`，correct 与 mismatch 除音频配对外逐字段一致。任何空输出、首 token EOS、nonfinite、残差超限、冻结 SHA 漂移都立即停止。

该实验只允许回答“新 fusion 是否保留正确/错配选择性”，不允许据此宣称 M4 端到端收益或自动授权训练。
