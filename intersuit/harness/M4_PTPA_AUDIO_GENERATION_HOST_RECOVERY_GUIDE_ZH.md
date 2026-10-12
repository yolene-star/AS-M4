# AVE generation smoke 宿主机恢复手册

日期：2026-09-02（Asia/Shanghai）

## 当前结论

AVE generation 的数据、20 组 manifest、2 组 smoke runner、adapter checkpoint SHA、CPU 合同和输出审计均已完成。当前有两个互相独立的外部阻塞：

1. 宿主机 `nvidia-smi` 可见 5 张 GPU，但 M4 Conda 的 PyTorch CUDA/NVML 初始化曾长期卡住，部分 Python 进程进入 `D` 状态；
2. 当前 Codex 会话把 `/home/yjm/M4-main/.git` 单独挂载为只读，无法创建 `index.lock`。

## GPU 恢复流程

以下步骤应由宿主机管理员在正常终端执行，不应在当前 Codex 沙箱内反复运行。

### 1. 检查残留与等待通道

```bash
ps -eo pid,stat,wchan:32,etime,cmd | \
  grep -E 'conda run -n M4|check_m4_repro_preflight|torch.cuda|run_audio_generation_review_ave'
```

对于仍为 `D` 状态的 PID，管理员读取：

```bash
cat /proc/<PID>/wchan
sudo cat /proc/<PID>/stack
```

`D` 状态进程无法保证被 `SIGTERM` 或 `SIGKILL` 立即清除，必须先定位其内核等待点。

### 2. 检查内核错误

```bash
journalctl -k --since '2026-09-02 00:00:00' --no-pager | \
  grep -Ei 'NVRM|Xid|AER|PCIe Bus Error|GPU has fallen|hung task|nvidia_uvm'
```

若存在 Xid、PCIe/AER 或 `nvidia_uvm` 卡死，应保存原始日志后再决定恢复方式。

### 3. 恢复选择

恢复动作必须先确认没有其他用户 GPU 任务：

- 若只是当前诊断进程残留，先终止对应父子进程并等待退出；
- 若 CUDA 初始化持续进入 `D` 状态，不要继续堆积测试进程；
- GPU reset、驱动模块重载或服务重启必须由管理员协调，不能在有其他任务时执行；
- 如果 `D` 状态无法解除，且管理员确认无其他任务，协调重启宿主机通常比反复 reset/重载更安全。

### 4. 恢复后的最小门禁

先显式使用物理 GPU2，只运行一次：

```bash
CUDA_VISIBLE_DEVICES=2 \
conda run -n M4 python -c \
"import torch; assert torch.cuda.is_available(); x=torch.ones(1, device='cuda'); print(torch.cuda.get_device_name(0), x.item())"
```

要求在短时间内返回，且逻辑 `cuda:0` 对应物理 GPU2。随后运行：

```bash
cd /home/yjm/M4-main/intersuit
conda run -n M4 python scripts/check_m4_repro_preflight.py
```

两项全部通过前，不启动 generation smoke。

## Git 恢复流程

当前证据：

```text
/home/yjm/M4-main/.git -> ext4, ro,nosuid,nodev,...
工作区其他路径可写
```

这符合 Codex 会话对 `.git` 单独只读挂载的权限配置。解决方式应是：

- 重新启动/配置一个允许该仓库 Git index 写入的 Codex 会话；或
- 在用户自己的可写宿主机终端执行精确 `git add`/`git commit`。

不要在当前沙箱内尝试 `mount -o remount,rw`，也不要复制或重建 `.git` 绕过限制。

恢复后只暂存 AVE generation 本轮文件，并先检查：

```bash
git diff --cached --check
git diff --cached --stat
```

确保没有 checkpoint、媒体、数据集、缓存或运行日志进入提交。

## Smoke 恢复边界

GPU 与 Git 恢复后，仍只允许：

- 物理 GPU2 / 逻辑 `cuda:0`；
- 2 个 pair group、8 条条件记录；
- deterministic generation；
- 唯一空输出目录；
- 保存配置、开始时间、stdout/stderr、GPU 前后状态和独立输出审计。

不得直接扩大为 20 组、150-step 或 formal test。
