#!/bin/bash
# Dell G15 控制中心安装脚本（Bazzite / Fedora Atomic）。

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly APP_NAME="g15-control-center"
readonly REPO_SLUG="Grant-Felix/ian-awcc-bazzite"
readonly REPO_URL="https://github.com/${REPO_SLUG}"
readonly REPO_GIT="https://github.com/${REPO_SLUG}.git"
readonly UPSTREAM_URL="https://github.com/AndersonDinizDev/g15-control-center"
readonly INSTALL_DIR="/var/opt/g15-controller"
readonly BIN_LINK="/usr/local/bin/g15-controller"
readonly SERVICE_FILE="/etc/systemd/system/g15-daemon.service"
readonly DESKTOP_FILE="/usr/local/share/applications/${APP_NAME}.desktop"
readonly HWDB_FILE="/etc/udev/hwdb.d/90-dell-g15-gmode.hwdb"

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly CYAN='\033[0;36m'
readonly NC='\033[0m'

log() { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warning() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fatal() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

execute() {
    local cmd="$*"
    log "执行：$cmd"
    eval "$cmd" >/dev/null 2>&1 || fatal "执行失败：$cmd"
}

# 非致命步骤：失败只告警，不中断安装。
# 用于容器/虚拟机等环境下可能受限、但不影响核心功能的操作。
try_execute() {
    local cmd="$*"
    log "执行：$cmd"
    if ! eval "$cmd" >/dev/null 2>&1; then
        warning "该步骤失败（不影响核心功能）：$cmd"
        return 1
    fi
    return 0
}

require_root() {
    [[ $EUID -eq 0 ]] || fatal "请以 root 身份运行：sudo $0"
}

require_alienware_wmi() {
    if [[ ! -d /sys/devices/platform/alienware-wmi ]] && \
       ! grep -q alienware_wmi /proc/modules 2>/dev/null; then
        warning "未找到 alienware_wmi 驱动 —— 风扇控制将不可用。"
        warning "请加载模块（modprobe alienware_wmi）或升级内核。"
    fi
}

check_hardware() {
    local model
    model=$(cat /sys/class/dmi/id/product_name 2>/dev/null || echo "")
    if [[ "$model" == *"G15"* || "$model" == *"G16"* ]]; then
        success "已检测到 Dell $model"
    else
        warning "未检测到 Dell G15/G16 机型（当前机型：${model:-未知}）。"
        warning "本项目依赖 alienware_wmi 驱动，其他机型可能无法控制风扇。"
        read -p "仍要继续吗？[y/N] " -n 1 -r; echo
        [[ $REPLY =~ ^[Yy]$ ]] || fatal "安装已取消。"
    fi
}

show_source_info() {
    log "仓库：${REPO_URL}"
    log "上游：${UPSTREAM_URL}（fork 来源，MIT License）"
}

usage() {
    cat <<EOF
Dell G15/G16 控制中心 —— 安装脚本

用法：
  sudo ./install.sh              从当前目录安装
  sudo ./install.sh --help       显示本帮助

在线安装（无需先克隆）：
  git clone ${REPO_GIT}
  cd ian-awcc-bazzite && sudo ./install.sh

本脚本会：
  1. 检查 alienware_wmi 驱动与机型
  2. 安装到 ${INSTALL_DIR}（独立 venv）
  3. 启用 systemd 服务 g15-daemon
  4. 通过 udev/hwdb 映射 G-Mode（F9）键
  5. 在应用菜单创建快捷方式

卸载：sudo ./uninstall.sh
EOF
}

install_application() {
    log "正在安装到 $INSTALL_DIR ..."
    if systemctl is-active --quiet g15-daemon 2>/dev/null; then
        execute "systemctl stop g15-daemon"
    fi
    execute "mkdir -p $INSTALL_DIR /usr/local/bin /usr/local/share/applications"
    execute "cp -r $SCRIPT_DIR/src/ $INSTALL_DIR/"
    execute "cp $SCRIPT_DIR/requirements.txt $INSTALL_DIR/"
    execute "cp $SCRIPT_DIR/system/g15-control-center.svg $INSTALL_DIR/icon.svg"

    log "正在创建虚拟环境 venv ..."
    execute "python3 -m venv $INSTALL_DIR/venv"
    execute "$INSTALL_DIR/venv/bin/pip install --upgrade pip"
    execute "$INSTALL_DIR/venv/bin/pip install -r $INSTALL_DIR/requirements.txt"

    execute "chown -R root:root $INSTALL_DIR"
    execute "chmod -R 755 $INSTALL_DIR"
    execute "chmod +x $INSTALL_DIR/src/g15_daemon.py $INSTALL_DIR/src/g15_control_center.py"
}

install_systemd_service() {
    execute "cp $SCRIPT_DIR/system/g15-daemon.service $SERVICE_FILE"
    execute "systemctl daemon-reload"
    execute "systemctl enable g15-daemon.service"
}

install_desktop_entry() {
    sed "s|/opt/g15-controller|/var/opt/g15-controller|g" \
        "$SCRIPT_DIR/system/g15-control-center.desktop" > "$DESKTOP_FILE"
    execute "chmod 644 $DESKTOP_FILE"
}

install_gmode_key() {
    execute "cp $SCRIPT_DIR/system/90-dell-g15-gmode.hwdb $HWDB_FILE"
    try_execute "systemd-hwdb update" || true
    try_execute "udevadm trigger --subsystem-match=input --attr-match=name='AT Translated Set 2 keyboard'" || {
        warning "G-Mode（F9）键映射未能立即生效。"
        warning "这常见于容器/虚拟机环境。真机上重启一次即可生效。"
    }
}

create_launcher() {
    cat > "$BIN_LINK" <<EOF
#!/bin/bash
$INSTALL_DIR/venv/bin/python $INSTALL_DIR/src/g15_control_center.py "\$@"
EOF
    execute "chmod +x $BIN_LINK"
}

start_services() {
    try_execute "systemctl start g15-daemon.service" || {
        warning "后台服务未能启动，请检查：journalctl -u g15-daemon"
        return 0
    }
    if systemctl is-active --quiet g15-daemon; then
        success "后台服务已启动。"
    else
        warning "后台服务未启动，请检查：journalctl -u g15-daemon"
    fi
}

main() {
    if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
        usage
        exit 0
    fi
    require_root
    show_source_info
    require_alienware_wmi
    check_hardware
    install_application
    install_systemd_service
    install_desktop_entry
    install_gmode_key
    create_launcher
    start_services
    success "安装完成。可使用命令 '${YELLOW}g15-controller${NC}' 或从应用菜单启动。"
    log "提示：若界面标题栏显示「只读模式」，请检查 journalctl -u g15-daemon。"
}

main "$@"
