#!/bin/bash
# Dell G15 控制中心卸载脚本。

set -euo pipefail

readonly APP_NAME="g15-control-center"
readonly INSTALL_DIR="/var/opt/g15-controller"
readonly BIN_LINK="/usr/local/bin/g15-controller"
readonly SERVICE_FILE="/etc/systemd/system/g15-daemon.service"
readonly DESKTOP_FILE="/usr/local/share/applications/${APP_NAME}.desktop"
readonly HWDB_FILE="/etc/udev/hwdb.d/90-dell-g15-gmode.hwdb"
readonly CONFIG_DIR="/etc/g15-daemon"

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

log() { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
fatal() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

execute() {
    log "执行：$*"
    eval "$*" >/dev/null 2>&1 || true
}

require_root() {
    # 管道模式下 $0 是 "bash"，给用户可复制的等价命令。
    if [[ "${0##*/}" == "bash" || "${0##*/}" == "sh" ]]; then
        fatal "请以 root 身份运行：sudo bash（或先 curl 下载脚本再执行）"
    fi
    [[ $EUID -eq 0 ]] || fatal "请以 root 身份运行：sudo $0"
}

main() {
    if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
        cat <<EOF
Dell G15/G16 控制中心 —— 卸载脚本

用法：
  sudo ./uninstall.sh
  curl -fsSL https://github.com/Grant-Felix/ian-awcc-bazzite/raw/main/uninstall.sh | sudo bash

本脚本会删除：
  ${INSTALL_DIR}
  ${CONFIG_DIR}
  ${BIN_LINK}
  ${SERVICE_FILE}
  ${DESKTOP_FILE}
  ${HWDB_FILE}
EOF
        exit 0
    fi

    require_root

    log "正在停止服务 ..."
    execute "systemctl stop g15-daemon"
    execute "systemctl disable g15-daemon"

    log "正在删除文件 ..."
    execute "rm -f $SERVICE_FILE $BIN_LINK $DESKTOP_FILE $HWDB_FILE /tmp/g15-daemon.sock"
    execute "rm -rf $INSTALL_DIR $CONFIG_DIR"

    log "正在刷新 systemd / udev ..."
    execute "systemctl daemon-reload"
    execute "systemd-hwdb update"
    execute "udevadm trigger --subsystem-match=input --attr-match=name='AT Translated Set 2 keyboard'"

    success "卸载完成。"
}

main "$@"
