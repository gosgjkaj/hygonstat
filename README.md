# hygonstat

海光 Hygon DCU 的轻量、只读命令行监控工具，提供 gpustat 风格的单行状态、进程信息、持续刷新和 JSON 输出。

**hygonstat 继承了 [gpustat](https://github.com/wookayin/gpustat) 的交互与显示设计，并直接复用其 `GPUStat`、`GPUStatCollection` 数据对象和终端排版。** 本项目新增独立的海光数据采集层，通过驱动提供的 SMI 命令和原生库读取指标。感谢 gpustat 作者 **Jongwook Choi** 及所有贡献者。

这是独立维护的衍生工具，不是 gpustat 上游的官方海光后端。它不调用 gpustat 的 NVIDIA NVML 查询入口，也不修改已安装的 gpustat。当前显示层依赖 gpustat 1.1.1 系列。

## 功能

- 查看 GPU 利用率、显存用量、温度、功耗和功耗上限。
- 查看进程的用户名、命令名、PID，以及每个进程在每张卡上的显存占用。
- 按 SMI 设备编号筛选，支持彩色终端输出和定时刷新。
- 输出单次 JSON 快照或逐行 JSON 数据流，方便接入脚本。
- 只读取状态，不调整频率、功耗限制、驱动或设备配置。

## 安装

需要 **Linux、Python 3.9 或以上，以及已安装并可用的海光驱动/DTK**。Python 包不会安装 GPU 驱动。

从 PyPI 安装：

```bash
python -m pip install hygonstat
```

也可直接从 GitHub 安装开发版本：

```bash
python -m pip install git+https://github.com/gosgjkaj/hygonstat.git
```

也可以克隆源码后安装：

```bash
git clone https://github.com/gosgjkaj/hygonstat.git
cd hygonstat
python -m pip install .
```

推荐安装到虚拟环境。需要仅为当前用户安装时，可在支持用户安装的 Python 环境中使用 `--user`。

### 为所有用户安装

管理员可在源码目录执行：

```bash
sudo bash scripts/install-system.sh /path/to/python3
```

安装器建立由 root 管理的独立环境，安装项目依赖并运行测试，通过后发布系统命令：

| 内容 | 默认位置 |
| --- | --- |
| 命令入口 | `/usr/local/bin/hygonstat` |
| Python 环境 | `/opt/hygonstat/venv` |
| 源码与测试快照 | `/opt/hygonstat/src` |
| 历史安装与依赖版本记录 | `/opt/hygonstat/releases/` |

该方式不修改现有共享 Python 环境。命令入口使用 Python 隔离模式，避免当前目录、`PYTHONPATH` 和个人 Python 包影响加载；查询仍以调用者身份执行，不提升权限。安装器不修改登录提示或 GPU 访问权限。

再次运行安装器可更新，旧版本会保留。需要回滚时，可依据新版本目录内的 `previous-release.txt`，由管理员将 `/opt/hygonstat/current` 指回上一版本。

个人安装可用 `python -m pip uninstall hygonstat` 卸载。系统安装由管理员移除命令入口和确认不再需要的 `/opt/hygonstat` 安装目录。

## 使用

```bash
hygonstat                 # 查看所有设备
hygonstat -a              # 加上用户名、命令名、PID、功耗和功耗上限
hygonstat -a -i 1         # 每秒刷新，Ctrl-C 退出
hygonstat --id 0,1        # 只查看指定 SMI 编号
hygonstat --json          # 单次 JSON 快照
hygonstat --json -i 1     # 每行一个 JSON 快照
hygonstat --no-processes  # 仅查询设备状态
hygonstat --help          # 查看完整选项
```

常用选项：

| 选项 | 作用 |
| --- | --- |
| `-u`、`-c`、`-p` | 分别显示进程用户名、命令名、PID |
| `-P` | 显示功耗及上限；`-P draw` 仅显示功耗 |
| `-i [秒数]`、`--watch [秒数]` | 持续刷新；省略秒数时为 1 秒 |
| `--color`、`--no-color` | 强制启用或关闭颜色 |
| `--no-header` | 隐藏主机与时间标题 |
| `--smi 路径` | 指定驱动提供的 SMI 可执行文件 |
| `--timeout 秒数` | SMI 命令超时，默认 10 秒 |

`-a` 包含本工具支持的详细指标，不包含 gpustat 的风扇、编解码器或 CPU 进程详情选项。SMI 查询失败时命令会报告错误并以非零状态退出；持续刷新不会把旧快照冒充为最新状态。

## 驱动配置

设备采集需要海光驱动提供的 `hy-smi`，或由海光 DTK 提供的 `rocm-smi` 包装命令。可以先直接运行该命令，确认当前用户能读取设备状态。

SMI 选择顺序为 `--smi`、`HYGONSTAT_SMI`、`PATH`，然后是常用 DTK 安装目录。需要手动指定时：

```bash
hygonstat --smi /path/to/dtk/bin/rocm-smi
HYGONSTAT_SMI=/path/to/dtk/bin/rocm-smi hygonstat
```

进程采集需要海光驱动的原生 SMI 库。默认查找：

```text
/opt/hyhal/lib/librocm_smi64.so
/usr/local/hyhal/lib/librocm_smi64.so
```

其他安装位置可通过环境变量指定：

```bash
HYGONSTAT_SMI_LIBRARY=/path/to/librocm_smi64.so hygonstat -a
```

如果进程接口不可用，设备指标仍可显示，进程数据会标记为未知并附带警告。`--no-processes` 可以完全跳过原生库查询。`--timeout` 只限制 SMI 子进程，不限制原生库调用。

## 指标说明

- **设备编号**沿用 SMI 编号，不是 `/dev/dri/cardN` 编号，也不能假设等于可见设备变量重映射后的 HIP 逻辑编号。JSON 中的 `pci.bus_id` 可用于核对物理设备。
- **温度**使用 SMI 的 `edge` 传感器，可能与其他工具选择的传感器不同。
- **显存单位**为 MiB（1024² 字节）。终端沿用 gpustat 的 `MB` 标签，JSON 明确提供 `memory_unit: "MiB"`。容量以驱动报告为准。
- **进程显存**来自原生库的每进程、每设备接口，并以 PCI 地址核对设备归属；不会把多卡进程的合计显存重复显示到每张卡上。进程显存可包含运行时上下文等开销，板卡总占用也不要求等于进程占用之和。
- **未知值**在终端显示为 `??`，在 JSON 中为 `null`，不表示零。进程列表 `[]` 表示查询成功且没有进程；`null` 表示未查询或无法查询。
- **采样时间**：设备状态与进程列表先后采集，进程启动或结束时可能存在短暂差异。

CLI 的 JSON 输出除 gpustat 数据字段外，还包含 `backend`、`smi_path`、`memory_unit` 和 `warnings`。持续输出模式使用逐行 JSON，每行都是一个完整快照。

## 兼容性

已完成实机验证的组合为 **DTK 26.04、驱动 6.3.32-V1.6.0、Python 3.14**，验证覆盖设备指标、单进程多卡显存归属、JSON 输出和持续刷新。

不同驱动的字段、原生接口和容器内进程可见性可能不同。项目不将“安装成功”视为硬件兼容性的证明；缺失或不可读取的数据会保留为未知。已知限制见 [ISSUES.md](https://github.com/gosgjkaj/hygonstat/blob/main/ISSUES.md)。

## Python API

```python
import hygonstat

stats = hygonstat.new_query(id=[0, 1])
stats.print_formatted(show_user=True, show_cmd=True, show_pid=True)
data = stats.jsonify()
warnings = stats.warnings
```

`new_query()` 返回 gpustat 的 `GPUStatCollection`，并附加 `warnings` 和 `smi_path` 属性。可传入 `smi`、`timeout`、`id` 和 `processes=False`。`stats.jsonify()` 返回 Python 字典，其中时间字段是 `datetime`；CLI 会负责序列化并添加上述 JSON 元数据。

## 开发与测试

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

单元测试不需要 GPU，覆盖真实 SMI 输出的解析、单位转换、未知值、PCI 映射、进程竞态、超时及 CLI 行为。测试样本中的硬件序列号已替换为示例值。

构建并检查发行包：

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
```

欢迎通过 [Issues](https://github.com/gosgjkaj/hygonstat/issues) 报告兼容性问题。请附上 Python、DTK 和驱动版本、报错信息，以及脱敏后的 SMI 输出；不要提交用户名、主机名、序列号或其他不希望公开的信息。

## 相关项目

- [dcutop](https://github.com/gongshl0306/dcutop)：面向海光 DCU 的交互式终端监控工具，提供进程显存、JSON 和 Python API。
- [hysmi-top](https://github.com/mcocdaa/hysmi-top)：通过 sysfs 和 SMI 采集数据的海光 DCU 终端监控工具，提供 nvtop 风格的曲线显示。
- [xpustat](https://github.com/Bekhouche/xpustat)：面向多种厂商加速器的状态工具；其 README 将 Hygon 后端标记为尚未经过硬件验证。

## 来源与许可证

hygonstat 基于 [gpustat](https://github.com/wookayin/gpustat) 的使用体验构建，并将其作为运行时依赖复用。gpustat 由 Jongwook Choi 及贡献者维护，采用 [MIT 许可证](https://github.com/wookayin/gpustat/blob/master/LICENSE)。本项目的海光采集层和命令入口独立实现，未将 gpustat 源码复制进本仓库。

hygonstat 同样采用 [MIT 许可证](https://github.com/gosgjkaj/hygonstat/blob/main/LICENSE)。上游项目的名称、贡献与许可证归其原作者所有。
