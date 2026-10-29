# hygonstat

海光 DCU 的只读命令行监控工具。通过驱动自带 SMI 查询设备状态，复用
[gpustat](https://github.com/wookayin/gpustat) 1.1.1 的终端排版。
支持利用率、显存、温度、功耗、进程、持续刷新及 JSON 输出。

## 使用

```bash
hygonstat                 # 全部卡的状态
hygonstat -a              # 增加用户名、命令、PID、功耗
hygonstat -a -i 1         # 每秒刷新，Ctrl-C 退出
hygonstat --id 0,1        # 指定 SMI 编号
hygonstat --json          # 结构化快照
hygonstat --json -i 1     # 每行一个 JSON 快照
hygonstat --no-processes  # 仅设备状态
```

`sugon` 已由管理员安装为系统命令，所有用户登录后均可使用，无须激活 conda。
通过非交互 SSH 调用时也可使用系统路径：

```bash
ssh sugon '/usr/local/bin/hygonstat -a'
```

也可以直接使用服务器已有的原生命令，无需本工具：

```bash
/opt/dtk-26.04/bin/rocm-smi
/opt/dtk-26.04/bin/rocm-smi --showuse --showmeminfo vram
/opt/dtk-26.04/bin/rocm-smi --showpids
watch -n 1 /opt/dtk-26.04/bin/rocm-smi
```

这台机器的 `rocm-smi` 实际转交给海光 `hy-smi`。
`gpustat` 1.1.1 的采集层使用 NVIDIA NVML，因此找不到海光设备。
本工具只调用 gpustat 的数据对象和显示接口，不调用它的 NVML 查询入口。
不修改原有 gpustat，不改变驱动、频率或任务运行状态。

## 安装与卸载

要求 Linux、海光驱动/DTK 和 Python 3.9 或以上。
克隆后安装到自己的 Python 环境：

```bash
git clone git@github.com:gosgjkaj/hygonstat.git
cd hygonstat
python -m pip install .
```

仓库目前为私有，克隆需要相应 GitHub 权限。
仅安装到当前用户时，可使用 `python -m pip install --user .`。

### 系统安装（所有用户）

管理员在仓库目录执行：

```bash
sudo bash scripts/install-system.sh /path/to/python3
```

在 `sugon` 的 root 会话中使用：

```bash
bash scripts/install-system.sh /opt/miniforge3/bin/python
```

安装器创建由 root 拥有的独立 Python 环境，安装依赖并运行测试，通过后发布系统入口：

- 命令：`/usr/local/bin/hygonstat`
- 独立环境：`/opt/hygonstat/venv`
- 该次安装的源码和测试：`/opt/hygonstat/src`
- 安装记录和历史版本：`/opt/hygonstat/releases/`

安装不修改共享 conda base。系统入口用 Python 的隔离模式运行，避免当前目录、
`PYTHONPATH` 或个人 Python 包影响结果。CLI 仍以调用者身份运行，不提升权限。

再次执行安装器可更新，旧版本保留，可按新版本目录内的 `previous-release.txt`
将 `/opt/hygonstat/current` 指回上一版本。系统卸载时移除
`/usr/local/bin/hygonstat` 和确认不再需要的 `/opt/hygonstat` 安装目录；
个人安装使用 `python -m pip uninstall hygonstat`。

系统安装器不自动修改其他机器的登录提示。`sugon` 的登录欢迎页由管理员单独维护，
其中已加入 `hygonstat` 的常用命令。

## 数据含义与适配范围

- 编号沿用 SMI 的设备编号；并非 `/dev/dri/cardN` 编号，也不能假设与设置了可见设备变量后的 HIP 逻辑编号相同。JSON 同时提供 `pci.bus_id`。
- 温度使用 SMI 的 `edge` 传感器，其他传感器的读数可能不同。
- 显存单位为 MiB（1024² 字节）。终端沿用 gpustat 的 `MB` 标签；JSON 显式注明 `memory_unit: "MiB"`。
- 总显存是驱动报告的可用容量，可能略少于标称容量。板卡显存还包含驱动等开销，不要求等于进程占用之和。
- 单个进程的显存包括 HIP 运行时上下文等开销，不仅是代码显式分配的张量或缓冲区。
- `??` / JSON `null` 表示无法读取，绝不表示零。进程列表 `[]` 表示查询成功且没有进程；`null` 表示无法查询或未请求查询。
- `-a` 显示本工具支持的详细项；不包含 gpustat 的风扇、编解码器和 CPU 进程详情选项。
- 进程数据通过原生 ROCm SMI 库查询，并以 PCI 地址核对设备归属。单卡进程显存使用 `rsmi_compute_process_info_by_device_get`，不将多卡总量重复显示。
- 设备状态与进程列表为先后采样，任务启动/结束的瞬间可能存在短暂差异。
- 初始适配针对 `sugon` 的 DTK 26.04、驱动 `6.3.32-V1.6.0`、8 张 `Hygon BW`（每卡 65520 MiB）。其他版本需要实际核对。

## 配置与开发

SMI 搜索顺序：`HYGONSTAT_SMI` / `--smi`、PATH、常用 DTK 路径。
检测不到或需要选择特定驱动时：

```bash
hygonstat --smi /opt/dtk-26.04/bin/rocm-smi
HYGONSTAT_SMI=/opt/dtk-26.04/bin/rocm-smi hygonstat
```

采集命令设置 10 秒超时，可通过 `--timeout` 修改。持续刷新时查询失败会报告错误并退出，避免把旧状态显示成最新状态。

进程查询默认加载 `/opt/hyhal/lib/librocm_smi64.so` 或
`/usr/local/hyhal/lib/librocm_smi64.so`。如果驱动安装在其他位置，设置
`HYGONSTAT_SMI_LIBRARY=/absolute/path/to/librocm_smi64.so`。

Python 接口：

```python
import hygonstat

stats = hygonstat.new_query(id=[0, 1])
stats.print_formatted(show_user=True, show_cmd=True, show_pid=True)
data = stats.jsonify()
print(stats.warnings)
```

运行离线测试（不需要 GPU，需安装项目依赖）：

```bash
python -m unittest discover -s tests -v
```

已知兼容性事项见 [ISSUES.md](ISSUES.md)。

## 验证记录（2026-10-29，Asia/Singapore）

- 在 sugon 的 conda base Python 3.14 上执行 32 项单元测试：真实输出解析、显存单位、设备映射、权限与进程竞态、超时、CLI/JSON/持续刷新。
- 实际查询全部 8 张卡，并与原生 SMI 读数核对。
- 同一进程在两张卡上分别显式分配 64/32 MiB，观察到 204/172 MiB 的独立进程显存（含运行时开销），差值正确，PID、用户名和命令名正确。
- 测试进程退出后，两卡恢复到初始 2 MiB 的驱动占用，进程列表为空。
- 安装后验证设备筛选、非法编号退出码、彩色输出、JSON、5 帧 NDJSON 刷新和 Ctrl-C 退出；交互登录能直接找到 `hygonstat`。
- 未做长期负载测试；当前工程验证范围为上述单元测试和短时实机检查。
