# Dell G15 控制中心（Bazzite / Fedora Atomic）

面向 Linux 的 Dell G15 笔记本原生控制中心。基于内核 `sysfs`、`platform_profile` 与 `alienware_wmi` 驱动构建。**无需 DKMS，无需树外模块。**

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://python.org)
[![PyQt6](https://img.shields.io/badge/PyQt6-6.4%2B-green)](https://riverbankcomputing.com/software/pyqt/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)
[![Linux](https://img.shields.io/badge/OS-Bazzite%20%7C%20Fedora-orange)](https://bazzite.gg)

> 本文件是原版 [README.md](README.md)（葡萄牙语）的简体中文版。本仓库在原版基础上做了**界面全汉化**与**暗色/浅色主题**支持。
>
> **本仓库是 [AndersonDinizDev/g15-control-center](https://github.com/AndersonDinizDev/g15-control-center) 的 fork**（上游为 Dell G15 原生控制中心）。原作者的版权与提交历史完整保留，上游新增功能可通过 `git remote add upstream https://github.com/AndersonDinizDev/g15-control-center.git` 后 `git fetch upstream` 合并。

## 功能特性

### 监控
- **温度**：通过 `alienware_wmi` 的 `hwmon` 读取 CPU 与 GPU 温度（读不到时回退到 `dell_smm` / `dell_ddv`）。
- **风扇**：实时读取 `alienware_wmi` 的风扇转速（RPM）。

### 控制
- **功耗模式**（`/sys/firmware/acpi/platform_profile`）：静音、均衡、性能。
- **自定义**：继承当前 profile，并允许手动调节 CPU / GPU 风扇的 boost（`alienware_wmi` 的 `fan{1,2}_boost`）。
- **G-Mode**：通过 `/sys/devices/platform/alienware-wmi/thermal_mode` 开启（与 Alienware Command Center 行为一致）。
- **F9 键（G-Mode）**：通过 `udev/hwdb` 映射（scancode 0x68 → `KEY_PROG1`），由后台服务捕获。
- **配置持久化**：配置存于 `/etc/g15-daemon/config.json`，开机自动恢复。

### 原子发行版兼容性
- 安装到 `/var/opt/g15-controller`（不触碰不可变的 `/usr`）。
- systemd 服务，日志走 `journald`。
- 不依赖 DKMS，内核升级不会失效。

## 本分支新增

### 界面汉化
- 窗口、卡片标题、标签页、按钮、状态标签、托盘菜单、全部弹窗均为简体中文。
- `install.sh` / `uninstall.sh` 的终端提示已汉化。
- 桌面项增加了 `Name[zh_CN]` / `Comment[zh_CN]` / `Keywords[zh_CN]` 等中文本地化字段。
- **注意**：功耗模式的名称是 UI 与后台服务之间的协议字段（存于 `config.json`，双方按字符串匹配），因此这些名称在两端同步改为中文。若你之前用原版跑过、`/etc/g15-daemon/config.json` 里存的是葡语模式名，首次启动会自动回落到「均衡」。

### 暗色 / 浅色主题
- 集中式调色板模块 `src/g15_theme.py`，两套主题；**默认暗色**。
- 标题栏右侧按钮一键切换，选择会持久化（优先写入 `/etc/g15-daemon/config.json`，不可写时回落到 `~/.config/g15-control-center/config.json`）。
- 系统托盘菜单中也可切换主题。
- 温度 / 转速的状态配色（低温、正常、偏热、过热 / 偏低、正常、偏高）随主题自适应。

### 只读降级模式
- 后台服务未运行时，界面不再直接报错退出，而是尝试以**只读方式**直读 `hwmon`：仍可查看 CPU/GPU 温度与风扇转速。
- 此时所有写操作（功耗模式、风扇滑块、G-Mode）会被禁用并给出提示，标题栏状态徽章显示为 `只读模式（驱动名）`。

### 容器 / 虚拟机下的限制

在 distrobox、Docker 等容器中安装时，以下几点**会失败但不影响核心功能**，安装脚本会自动降级为警告并继续：

- `udevadm trigger`：宿主 sysfs 在容器内只读，G-Mode（F9）键映射无法立即生效。**真机重启一次即可**。
- `/sys/firmware/acpi/platform_profile`、`fan*_boost` 写入：容器内 sysfs 只读，功耗模式切换与风扇调速会返回 error（但温度/转速**读取正常**）。
- `/tmp/g15-daemon.sock`：容器的 `/tmp` 通常与宿主共享，若宿主已有 daemon 在跑会冲突。此时 daemon 会明确报「可能有另一个 g15-daemon 正在运行」而不是抛出难懂的 `PermissionError`。

容器内实测结论：安装流程、systemd 服务注册、socket 通信、温度/转速读取、界面汉化与主题切换**均正常**；
只有需要写 sysfs 的控制功能受容器限制。

## 环境要求

### 硬件 / 内核
- 搭载 `alienware_wmi` 驱动的 Dell G15/G16（`lsmod | grep alienware_wmi`）。
  在多数 Fedora 系发行版上，Linux 6.x+ 内核已原生支持。
- 若没有 `alienware_wmi`，风扇控制与 G-Mode **不可用**，程序只能监控传感器。

### 软件
- Bazzite OS、Fedora Silverblue/Kinoite/Workstation（或任何带 systemd + `alienware_wmi` 的发行版）。
- Python 3.9+
- PyQt6 6.4+（安装脚本会在独立 venv 中自动装好）。

## 安装

### 一条命令装好（推荐）

不依赖任何本地目录，自动下载并安装：

```bash
curl -fsSL https://raw.githubusercontent.com/Grant-Felix/ian-awcc-bazzite/main/install.sh | sudo bash
```

或先下载再执行（想看一眼脚本内容时用）：

```bash
curl -fsSL -o install.sh https://raw.githubusercontent.com/Grant-Felix/ian-awcc-bazzite/main/install.sh
sudo bash install.sh
```

脚本会自动把仓库克隆到临时目录，安装完清理掉。

### 从本地克隆安装

```bash
git clone https://github.com/Grant-Felix/ian-awcc-bazzite.git
cd ian-awcc-bazzite
sudo ./install.sh
```

### 重新安装 / 升级

**重复执行上面的任意一条命令即可**，无需先卸载。脚本只会覆盖文件，不会清空
`/etc/g15-daemon`，你已有的设置会保留。

> ⚠️ 跨语言版本升级（原版葡语 → 本汉化版）时，`/etc/g15-daemon/config.json`
> 里存的旧模式名（如 `Balanceado`）会校验失败并回落到「均衡」。
> 如需保留原设置，先备份再改：
> ```bash
> sudo cp /etc/g15-daemon/config.json ~/g15-config-backup.json
> # 把里面的 power_mode 改成 静音 / 均衡 / 性能 / 自定义 之一，再放回
> ```

### 卸载

```bash
sudo ./uninstall.sh
```

或一条命令：

```bash
curl -fsSL https://raw.githubusercontent.com/Grant-Felix/ian-awcc-bazzite/main/uninstall.sh | sudo bash
```

安装脚本会：
1. 检查 `alienware_wmi` 是否存在。
2. 安装到 `/var/opt/g15-controller`，使用独立 venv。
3. 在 systemd 中启用 `g15-daemon` 服务。
4. 通过 udev/hwdb 映射 **G-Mode（F9）** 键。
5. 在应用菜单创建快捷方式。

> **注意**：首次安装后可能需要**重启一次**，让内核把 G-Mode 键的 keymap 重新应用到 `atkbd`。

## 使用

```bash
g15-controller          # 打开界面
sudo systemctl status g15-daemon
journalctl -u g15-daemon -f
```

### 功耗模式
- **静音 / 均衡 / 性能**：应用内核对应的 profile；后台服务会撤销任何手动 boost，把风扇控制权交还 BIOS 曲线。
- **自定义**：保持当前 profile，并叠加滑块设定的手动 boost。`fan{1,2}_boost` 是在曲线之上**叠加**的：EC 仍会响应温度，只是风扇转得稍快一些。
- **G-Mode（F9）**：通过 `thermal_mode=0xab` 强制性能 + 最大 boost。关闭时，后台服务恢复之前的状态（包括已保存的手动 boost）。

### 界面说明
- **监控**标签页：CPU/GPU 温度、CPU/GPU 风扇转速四张卡片，下方是两台风扇的控制卡（手动开关 + 滑块 + 0/25/50/75/100% 预设）。
- **设置**标签页：功耗模式选择、使用说明、随系统自动启动开关。
- 手动控制需先切到「自定义」模式才可拖动滑块。
- 关闭窗口会最小化到系统托盘，双击托盘图标可重新显示。

## 故障排查

### 后台服务起不来
```bash
journalctl -u g15-daemon -f
```

### 滑块没反应
确认 `alienware_wmi` 已加载且暴露了 boost 文件：
```bash
ls /sys/class/hwmon/hwmon*/fan*_boost 2>/dev/null
lsmod | grep alienware_wmi
```
若没有任何输出，说明你的内核没有该驱动，风扇控制将不可用。

界面切换到「只读模式」也说明后台服务没连上 —— 检查 `systemctl is-active g15-daemon`。

### G-Mode 键无响应
```bash
sudo evtest /dev/input/by-path/platform-i8042-serio-0-event-kbd
```
按 F9：应出现 `KEY_PROG1`（code 148）。若出现 `KEY_UNKNOWN`，重启一次让 atkbd 重新加载 keymap。

### socket 权限
socket 位于 `/tmp/g15-daemon.sock`，权限 `0666`。若界面连不上，确认服务在运行（`systemctl is-active g15-daemon`）。

## 卸载

```bash
sudo ./uninstall.sh
```

## 免责声明

本软件直接操作 sysfs/ACPI。使用风险自负。

---
**为 Linux 上的 Dell G15 社区而开发。**
