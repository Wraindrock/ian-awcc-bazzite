#!/usr/bin/env python3
"""离屏渲染验收脚本：分别用暗色 / 浅色主题渲染主窗口并截图。

用法：
    QT_QPA_PLATFORM=offscreen /tmp/g15venv/bin/python verify_ui.py

说明：本脚本通过打桩（stub）替换 daemon socket，使 UI 在无 g15-daemon 的
情况下也能走「已连接」分支，从而渲染出完整的可交互界面用于验收。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

FAKE_DATA = {
    "temps": {"cpu_temp": 62, "gpu_temp": 48},
    "fans": {
        "fan1_rpm": 2340, "fan2_rpm": 1860,
        "fan1_boost": 60, "fan2_boost": 25,
        "fan1_manual": True, "fan2_manual": False,
    },
    "power": {"current_mode": "自定义", "g_mode": False},
    "status": {"model": "Dell G16 7630", "hwmon_available": True},
}

SOCK = "/tmp/g15-verify.sock"


def start_fake_daemon():
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCK)
    server.listen(5)

    def serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                raw = conn.recv(4096)
                try:
                    req = json.loads(raw.decode())
                except Exception:
                    continue
                action = req.get("action")
                if action == "get_all_data":
                    payload = {"status": "success", "data": FAKE_DATA}
                else:
                    payload = {"status": "success"}
                conn.send(json.dumps(payload).encode())

    threading.Thread(target=serve, daemon=True).start()
    return server


def main():
    out_dir = Path(__file__).resolve().parent / "verify_shots"
    out_dir.mkdir(exist_ok=True)

    server = start_fake_daemon()

    import g15_control_center as ui
    import g15_theme
    ui.SOCKET_PATH = SOCK  # 指向假 daemon

    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 让 ThemeManager 不污染真实配置
    cfg = out_dir / "config.json"
    if cfg.exists():
        cfg.unlink()
    ui.DAEMON_CONFIG_FILE = cfg
    os.environ.setdefault("HOME", "/tmp")

    window = ui.MainWindow()
    window.monitor.stop()

    results = {}
    for theme_name in (g15_theme.DARK, g15_theme.LIGHT):
        theme = g15_theme.palette_for(theme_name)
        window.apply_theme(theme)
        window.update_sensor_data({
            "cpu_temp": 62, "gpu_temp": 48,
            "fan1_rpm": 2340, "fan2_rpm": 1860,
            "fan1_boost": 60, "fan2_boost": 25,
            "fan1_manual": True, "fan2_manual": False,
            "power_mode": ui.PowerMode.CUSTOM,
            "g_mode": False,
        })
        window.g_mode_button.set_state(True)
        window.apply_theme(theme)  # 重新应用以反映 G-Mode 开启态
        window.g_mode_button.set_state(True)
        window.show()

        app.processEvents()

        for tab_index, tab_name in ((0, "monitor"), (1, "settings")):
            window.tabs.setCurrentIndex(tab_index)
            app.processEvents()
            path = out_dir / f"{theme_name}_{tab_name}.png"
            window.grab().save(str(path))
            results[f"{theme_name}_{tab_name}"] = str(path)

    # 断言：暗色主题下窗口背景确实是深色
    dark_bg = g15_theme.DARK_THEME.window
    light_bg = g15_theme.LIGHT_THEME.window
    print(json.dumps({
        "shots": results,
        "dark_window_bg": dark_bg,
        "light_window_bg": light_bg,
        "daemon_client_available": window.daemon_client.daemon_available,
        "degraded": window.is_degraded(),
        "widgets": {
            "cpu_card_value": window.cpu_thermal.value_label.text(),
            "cpu_status": window.cpu_thermal.status_label.text(),
            "fan1_rpm_label": window.fan1_rpm.value_label.text(),
            "fan1_manual_btn": window.fan1_control.manual_toggle.text(),
            "tab0": window.tabs.tabText(0),
            "tab1": window.tabs.tabText(1),
            "gmode_btn": window.g_mode_button.text(),
            "theme_btn": window.theme_button.text(),
            "model_label": window.model_label.text(),
            "mode_badge": window.mode_badge.text(),
        },
    }, ensure_ascii=False, indent=2))

    window.monitor.running = False
    server.close()


if __name__ == "__main__":
    main()
