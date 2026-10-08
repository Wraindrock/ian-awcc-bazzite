#!/usr/bin/env python3
"""Dell G15 Control Center — PyQt6 desktop UI."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMenu, QMessageBox, QProgressBar, QPushButton, QSlider, QSystemTrayIcon,
    QTabWidget, QVBoxLayout, QWidget,
)

try:  # 作为包导入（pip 安装 / python -m src.xxx）
    from . import g15_theme
except ImportError:  # 作为独立脚本直接运行（安装脚本的启动方式）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import g15_theme


SOCKET_PATH = "/tmp/g15-daemon.sock"
DAEMON_TIMEOUT = 5.0
DATA_CACHE_SECONDS = 1.0
MONITOR_INTERVAL_MS = 1000

DAEMON_CONFIG_FILE = Path("/etc/g15-daemon/config.json")


class PowerInfo(NamedTuple):
    label: str
    profile: str
    color: str


class PowerMode(Enum):
    QUIET = PowerInfo("静音", "quiet", "#4CAF50")
    BALANCED = PowerInfo("均衡", "balanced", "#2196F3")
    PERFORMANCE = PowerInfo("性能", "performance", "#FF9800")
    CUSTOM = PowerInfo("自定义", "inherit", "#9C27B0")


class ThemeManager:
    """主题（默认暗色）的读写与持久化。

    持久化位置优先使用 daemon 的 /etc/g15-daemon/config.json（若可写），
    否则回落到 ~/.config/g15-control-center/config.json。
    """

    def __init__(self):
        self.current = g15_theme.DEFAULT_THEME
        self.config_path = self._pick_config_path()
        self.current = self._load()

    @staticmethod
    def _pick_config_path() -> Path:
        if DAEMON_CONFIG_FILE.exists() and os.access(DAEMON_CONFIG_FILE, os.W_OK):
            return DAEMON_CONFIG_FILE
        return Path.home() / ".config" / "g15-control-center" / "config.json"

    def _load(self) -> str:
        try:
            data = json.loads(self.config_path.read_text())
        except (OSError, json.JSONDecodeError):
            return g15_theme.DEFAULT_THEME
        if not isinstance(data, dict):
            return g15_theme.DEFAULT_THEME
        return g15_theme.normalize(data.get("theme"))

    def save(self, name: str) -> bool:
        name = g15_theme.normalize(name)
        try:
            data = {}
            if self.config_path.exists():
                try:
                    loaded = json.loads(self.config_path.read_text())
                    if isinstance(loaded, dict):
                        data = loaded
                except json.JSONDecodeError:
                    data = {}
            data["theme"] = name
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps(data, indent=2))
            self.current = name
            return True
        except OSError:
            self.current = name
            return False


class SysfsReader:
    """在 daemon 不可用时，直接读取 sysfs 传感器数据。

    只读降级模式：温度 / 转速 / 型号可读，功耗模式与风扇 Boost 控制不可用，
    不涉及任何写操作、不需要 root。
    """

    ALIENWARE_DRIVERS = ("alienware_wmi", "dell_ddv", "dell_smm")
    HWMON_DIR = Path("/sys/class/hwmon")

    def __init__(self):
        self.backend: str | None = None
        self.hwmon_dir: Path | None = None

    @staticmethod
    def _find_hwmon_dir(driver_name: str) -> Path | None:
        for entry in sorted(SysfsReader.HWMON_DIR.glob("hwmon*")):
            try:
                if (entry / "name").read_text().strip() == driver_name:
                    return entry
            except OSError:
                continue
        return None

    @staticmethod
    def _read_indexed(directory: Path, prefix: str, suffix: str) -> dict:
        """读取形如 fan1_input / temp2_input 的文件，返回 {序号: 值}。"""
        values: dict = {}
        for path in sorted(directory.glob(f"{prefix}*{suffix}")):
            idx_text = path.name[len(prefix):-len(suffix)]
            try:
                values[int(idx_text)] = int(path.read_text().strip())
            except (ValueError, OSError):
                continue
        return values

    def probe(self) -> bool:
        """依次尝试各驱动，第一个可用的作为数据源。"""
        for driver in self.ALIENWARE_DRIVERS:
            directory = self._find_hwmon_dir(driver)
            if directory is None:
                continue
            if not self._read_indexed(directory, "temp", "_input"):
                continue
            self.backend = driver
            self.hwmon_dir = directory
            return True
        return False

    def read_all(self) -> dict:
        """返回与 daemon `get_all_data` 相同结构的数据。"""
        if self.hwmon_dir is None:
            return {}
        directory = self.hwmon_dir
        temps = self._read_indexed(directory, "temp", "_input")
        fans = self._read_indexed(directory, "fan", "_input")
        boosts = self._read_indexed(directory, "fan", "_boost")

        return {
            "temps": {
                "cpu_temp": temps.get(1, 0) // 1000,
                "gpu_temp": temps.get(2, 0) // 1000,
            },
            "fans": {
                "fan1_rpm": fans.get(1, 0),
                "fan2_rpm": fans.get(2, 0),
                "fan1_boost": boosts.get(1, 0),
                "fan2_boost": boosts.get(2, 0),
                "fan1_manual": False,
                "fan2_manual": False,
            },
            "power": {"current_mode": "均衡", "g_mode": False},
            "status": {
                "model": self.model_name(),
                "hwmon_available": bool(boosts),
            },
        }

    @staticmethod
    def model_name() -> str:
        try:
            return Path("/sys/class/dmi/id/product_name").read_text().strip()
        except OSError:
            return "未知"


class G15DaemonClient:
    def __init__(self):
        self.socket_path = SOCKET_PATH
        self.daemon_available = self._check_daemon()
        self._cached_data: dict | None = None
        self._last_update: float = 0.0

    def _check_daemon(self) -> bool:
        if not os.path.exists(self.socket_path):
            return False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(self.socket_path)
            return True
        except OSError:
            return False

    def _send_request(self, request_data: dict) -> dict:
        if not self.daemon_available:
            return {"status": "error", "message": "Daemon not available"}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(DAEMON_TIMEOUT)
                s.connect(self.socket_path)
                s.send(json.dumps(request_data).encode("utf-8"))
                response_data = s.recv(4096)
            return json.loads(response_data.decode("utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            self.daemon_available = False
            return {"status": "error", "message": str(e)}

    def _get_all_data(self) -> dict:
        now = time.time()
        if self._cached_data is None or now - self._last_update > DATA_CACHE_SECONDS:
            response = self._send_request({"action": "get_all_data"})
            if response.get("status") == "success":
                self._cached_data = response.get("data", {})
                self._last_update = now
            elif self._cached_data is None:
                self._cached_data = {}
        return self._cached_data or {}

    def invalidate_cache(self):
        self._cached_data = None

    def get_cpu_temp(self) -> int:
        return self._get_all_data().get("temps", {}).get("cpu_temp", 0)

    def get_gpu_temp(self) -> int:
        return self._get_all_data().get("temps", {}).get("gpu_temp", 0)

    def get_fan_rpm(self, fan_id: int) -> int:
        return self._get_all_data().get("fans", {}).get(f"fan{fan_id}_rpm", 0)

    def get_fan_boost(self, fan_id: int) -> int:
        return self._get_all_data().get("fans", {}).get(f"fan{fan_id}_boost", 0)

    def get_fan_manual(self, fan_id: int) -> bool:
        return self._get_all_data().get("fans", {}).get(f"fan{fan_id}_manual", False)

    def get_power_mode(self) -> PowerMode:
        label = self._get_all_data().get("power", {}).get("current_mode", "均衡")
        for mode in PowerMode:
            if mode.value.label == label:
                return mode
        return PowerMode.BALANCED

    def get_g_mode_status(self) -> bool:
        return self._get_all_data().get("power", {}).get("g_mode", False)

    def set_power_mode(self, mode: PowerMode) -> bool:
        response = self._send_request({
            "action": "set_power_mode",
            "mode": mode.value.label,
        })
        return response.get("status") == "success"

    def set_fan_boost(self, fan_id: int, percentage: int) -> bool:
        response = self._send_request({
            "action": "set_fan_boost",
            "fan_id": fan_id,
            "percentage": percentage,
        })
        return response.get("status") == "success"

    def toggle_g_mode(self) -> bool:
        response = self._send_request({"action": "toggle_g_mode"})
        return response.get("status") == "success"


class AutoStartManager:
    AUTOSTART_TEMPLATE = """[Desktop Entry]
Name=Dell G15 控制中心
Comment=Dell G15 硬件监控与风扇控制
Exec={python} "{script}"
Icon=preferences-system
Terminal=false
Type=Application
Categories=System;Settings;
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
"""

    def __init__(self):
        self.autostart_dir = Path.home() / ".config" / "autostart"
        self.desktop_file = self.autostart_dir / "g15-controller.desktop"

    def is_enabled(self) -> bool:
        return self.desktop_file.exists()

    def enable(self) -> bool:
        try:
            self.autostart_dir.mkdir(parents=True, exist_ok=True)
            content = self.AUTOSTART_TEMPLATE.format(
                python=sys.executable,
                script=os.path.abspath(__file__),
            )
            self.desktop_file.write_text(content)
            self.desktop_file.chmod(0o755)
            return True
        except OSError:
            return False

    def disable(self) -> bool:
        try:
            self.desktop_file.unlink(missing_ok=True)
            return True
        except OSError:
            return False


class SensorMonitor(QThread):
    data_updated = pyqtSignal(dict)

    def __init__(self, daemon_client, reader=None):
        super().__init__()
        self.daemon_client = daemon_client
        self.reader = reader
        self.running = True

    def _collect_data(self) -> dict:
        # 降级模式：daemon 不可用时，数据来自本地 sysfs 只读读取
        if not self.daemon_client.daemon_available and self.reader is not None:
            raw = self.reader.read_all()
            if raw:
                return {
                    "cpu_temp": raw["temps"]["cpu_temp"],
                    "gpu_temp": raw["temps"]["gpu_temp"],
                    "fan1_rpm": raw["fans"]["fan1_rpm"],
                    "fan2_rpm": raw["fans"]["fan2_rpm"],
                    "fan1_boost": raw["fans"]["fan1_boost"],
                    "fan2_boost": raw["fans"]["fan2_boost"],
                    "fan1_manual": raw["fans"]["fan1_manual"],
                    "fan2_manual": raw["fans"]["fan2_manual"],
                    "power_mode": PowerMode.BALANCED,
                    "g_mode": raw["power"]["g_mode"],
                }

        client = self.daemon_client
        return {
            "cpu_temp": client.get_cpu_temp(),
            "gpu_temp": client.get_gpu_temp(),
            "fan1_rpm": client.get_fan_rpm(1),
            "fan2_rpm": client.get_fan_rpm(2),
            "fan1_boost": client.get_fan_boost(1),
            "fan2_boost": client.get_fan_boost(2),
            "fan1_manual": client.get_fan_manual(1),
            "fan2_manual": client.get_fan_manual(2),
            "power_mode": client.get_power_mode(),
            "g_mode": client.get_g_mode_status(),
        }

    def update_once(self):
        self.data_updated.emit(self._collect_data())

    def run(self):
        while self.running:
            self.data_updated.emit(self._collect_data())
            self.msleep(MONITOR_INTERVAL_MS)

    def stop(self):
        self.running = False
        self.wait()


class ThermalCard(QFrame):
    def __init__(self, title: str, unit: str = "°C", max_value: int = 100):
        super().__init__()
        self.title = title
        self.unit = unit
        self.max_value = max_value
        self.current_value = 0
        self.theme = g15_theme.palette_for(g15_theme.DEFAULT_THEME)
        self.setup_ui()

    def set_theme(self, theme: g15_theme.Theme):
        self.theme = theme
        self.setStyleSheet(g15_theme.card_stylesheet(theme))
        self.title_label.setStyleSheet(f"""
            font-size: 12px;
            font-weight: 600;
            color: {theme.text_muted};
        """)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {theme.surface_alt};
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {theme.green}, stop:0.5 {theme.yellow}, stop:1 {theme.red});
                border-radius: 3px;
            }}
        """)
        self.update_value(self.current_value)

    def setup_ui(self):
        t = self.theme
        self.setFrameStyle(QFrame.Shape.Box)
        self.setStyleSheet(g15_theme.card_stylesheet(t))

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(15, 15, 15, 15)

        self.title_label = QLabel(self.title)
        self.title_label.setStyleSheet(f"""
            font-size: 12px;
            font-weight: 600;
            color: {t.text_muted};
        """)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.value_label = QLabel(f"--{self.unit}")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_label.setStyleSheet(f"""
            font-size: 36px;
            font-weight: bold;
            color: {t.accent};
            padding: 5px;
        """)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, self.max_value)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {t.surface_alt};
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {t.green}, stop:0.5 {t.yellow}, stop:1 {t.red});
                border-radius: 3px;
            }}
        """)

        self.status_label = QLabel("--")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(f"""
            font-size: 11px;
            font-weight: 500;
            color: {t.text_muted};
            padding: 3px 8px;
            border-radius: 8px;
        """)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)

    def update_value(self, value: int):
        self.current_value = value
        self.value_label.setText(f"{value}{self.unit}")
        self.progress_bar.setValue(value)

        color, status, bg = self.get_status_style(value)
        self.value_label.setStyleSheet(f"""
            font-size: 36px;
            font-weight: bold;
            color: {color};
            padding: 5px;
        """)
        self.status_label.setText(status)
        self.status_label.setStyleSheet(f"""
            font-size: 11px;
            font-weight: 500;
            color: {color};
            padding: 3px 8px;
            background: {bg};
            border-radius: 8px;
        """)

    def get_status_style(self, value):
        if self.unit == "°C":
            return g15_theme.temperature_colors(self.theme, value)
        return g15_theme.rpm_colors(self.theme, value)


class FanControlCard(QFrame):
    boost_changed = pyqtSignal(int, int)

    def __init__(self, fan_id: int, title: str):
        super().__init__()
        self.fan_id = fan_id
        self.title = title
        self.manual_enabled = False
        self.theme = g15_theme.palette_for(g15_theme.DEFAULT_THEME)
        self.setup_ui()

    def set_theme(self, theme: g15_theme.Theme):
        self.theme = theme
        t = theme
        self.setStyleSheet(g15_theme.card_stylesheet(t))
        self.title_label.setStyleSheet(f"""
            font-size: 14px;
            font-weight: 600;
            color: {t.text};
        """)
        self.rpm_label.setStyleSheet(f"""
            font-size: 12px;
            font-weight: 500;
            color: {t.text_muted};
            padding: 3px 8px;
            background: {t.surface_alt};
            border-radius: 6px;
        """)
        self.control_widget.setStyleSheet(f"""
            QWidget {{
                background: {t.surface_alt};
                border-radius: 8px;
            }}
        """)
        self.boost_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 6px;
                background: {t.border};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                width: 16px;
                height: 16px;
                background: {t.inverse};
                border: 2px solid {t.accent};
                border-radius: 8px;
                margin: -5px 0;
            }}
            QSlider::sub-page:horizontal {{
                background: {t.accent};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal:disabled {{
                background: {t.surface};
                border: 2px solid {t.text_faint};
            }}
        """)
        self.boost_label.setStyleSheet(f"""
            font-size: 14px;
            font-weight: 600;
            color: {t.accent};
            background: {t.surface};
            padding: 5px;
            border: 1px solid {t.border};
            border-radius: 6px;
        """)
        for btn in self.preset_buttons:
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 11px;
                    font-weight: 500;
                    background: {t.surface};
                    color: {t.text_muted};
                    border: 1px solid {t.border};
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background: {t.accent};
                    color: {t.accent_text};
                    border: 1px solid {t.accent};
                }}
            """)
        self.update_manual_button_style(self.manual_enabled)

    def setup_ui(self):
        t = self.theme
        self.setFrameStyle(QFrame.Shape.Box)
        self.setStyleSheet(g15_theme.card_stylesheet(t))

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(15, 15, 15, 15)

        header_layout = QHBoxLayout()

        self.title_label = QLabel(self.title)
        self.title_label.setStyleSheet(f"""
            font-size: 14px;
            font-weight: 600;
            color: {t.text};
        """)

        self.rpm_label = QLabel("0 RPM")
        self.rpm_label.setStyleSheet(f"""
            font-size: 12px;
            font-weight: 500;
            color: {t.text_muted};
            padding: 3px 8px;
            background: {t.surface_alt};
            border-radius: 6px;
        """)

        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.rpm_label)

        self.manual_toggle = QPushButton("手动控制：关闭")
        self.manual_toggle.setCheckable(True)
        self.manual_toggle.setFixedHeight(32)
        self.manual_toggle.clicked.connect(self.toggle_manual)
        self.update_manual_button_style(False)

        self.control_widget = QWidget()
        self.control_widget.setStyleSheet(f"""
            QWidget {{
                background: {t.surface_alt};
                border-radius: 8px;
            }}
        """)
        control_layout = QVBoxLayout(self.control_widget)
        control_layout.setContentsMargins(12, 12, 12, 12)
        control_layout.setSpacing(8)

        slider_layout = QHBoxLayout()

        self.boost_slider = QSlider(Qt.Orientation.Horizontal)
        self.boost_slider.setRange(0, 100)
        self.boost_slider.setValue(0)
        self.boost_slider.setEnabled(False)
        self.boost_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 6px;
                background: {t.border};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                width: 16px;
                height: 16px;
                background: {t.inverse};
                border: 2px solid {t.accent};
                border-radius: 8px;
                margin: -5px 0;
            }}
            QSlider::sub-page:horizontal {{
                background: {t.accent};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal:disabled {{
                background: {t.surface};
                border: 2px solid {t.text_faint};
            }}
        """)
        self.boost_slider.valueChanged.connect(self.update_boost_label)
        self.boost_slider.sliderReleased.connect(self.apply_boost)

        self.boost_label = QLabel("0%")
        self.boost_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.boost_label.setFixedWidth(50)
        self.boost_label.setStyleSheet(f"""
            font-size: 14px;
            font-weight: 600;
            color: {t.accent};
            background: {t.surface};
            padding: 5px;
            border: 1px solid {t.border};
            border-radius: 6px;
        """)

        slider_layout.addWidget(self.boost_slider)
        slider_layout.addWidget(self.boost_label)

        preset_layout = QHBoxLayout()
        preset_layout.setSpacing(6)

        self.preset_buttons = []
        for value in [0, 25, 50, 75, 100]:
            btn = QPushButton(f"{value}%")
            self.preset_buttons.append(btn)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda checked, v=value: self.set_preset(v))
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 11px;
                    font-weight: 500;
                    background: {t.surface};
                    color: {t.text_muted};
                    border: 1px solid {t.border};
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background: {t.accent};
                    color: {t.accent_text};
                    border: 1px solid {t.accent};
                }}
            """)
            preset_layout.addWidget(btn)

        control_layout.addLayout(slider_layout)
        control_layout.addLayout(preset_layout)

        layout.addLayout(header_layout)
        layout.addWidget(self.manual_toggle)
        layout.addWidget(self.control_widget)

    def update_manual_button_style(self, enabled):
        t = self.theme
        if enabled:
            self.manual_toggle.setStyleSheet(f"""
                QPushButton {{
                    font-size: 12px;
                    font-weight: 600;
                    background: {t.accent};
                    color: {t.accent_text};
                    border: none;
                    border-radius: 6px;
                }}
                QPushButton:hover {{
                    background: {t.accent_hover};
                }}
            """)
        else:
            self.manual_toggle.setStyleSheet(f"""
                QPushButton {{
                    font-size: 12px;
                    font-weight: 500;
                    background: {t.surface};
                    color: {t.text_muted};
                    border: 2px solid {t.border};
                    border-radius: 6px;
                }}
                QPushButton:hover {{
                    background: {t.surface_hover};
                    border: 2px solid {t.accent};
                    color: {t.accent};
                }}
            """)

    def manual_text(self, enabled: bool) -> str:
        return "手动控制：开启" if enabled else "手动控制：关闭"

    def toggle_manual(self):
        self.manual_enabled = self.manual_toggle.isChecked()
        self.manual_toggle.setText(self.manual_text(self.manual_enabled))
        self.update_manual_button_style(self.manual_enabled)
        self.boost_slider.setEnabled(self.manual_enabled)

        if not self.manual_enabled:
            self.boost_slider.setValue(0)
            self.boost_changed.emit(self.fan_id, 0)

    def update_boost_label(self, value):
        self.boost_label.setText(f"{value}%")

    def apply_boost(self):
        if self.manual_enabled:
            value = self.boost_slider.value()
            self.boost_changed.emit(self.fan_id, value)

    def set_preset(self, value):
        if self.manual_enabled:
            self.boost_slider.setValue(value)
            self.boost_changed.emit(self.fan_id, value)

    def update_rpm(self, rpm: int):
        self.rpm_label.setText(f"{rpm:,} RPM")

    def update_boost(self, boost: int):
        self.boost_slider.setValue(boost)

    def sync_manual_state(self, is_manual: bool, boost: int):
        self.manual_enabled = is_manual
        self.manual_toggle.setChecked(is_manual)
        self.manual_toggle.setText(self.manual_text(is_manual))
        self.update_manual_button_style(is_manual)
        self.boost_slider.setEnabled(is_manual)
        self.boost_slider.setValue(boost)
        self.boost_label.setText(f"{boost}%")


class GModeButton(QPushButton):
    toggled_signal = pyqtSignal(bool)

    def __init__(self):
        super().__init__("G-MODE：关闭")
        self.theme = g15_theme.palette_for(g15_theme.DEFAULT_THEME)
        self.setCheckable(True)
        self.setFixedSize(180, 50)
        self.is_on = False
        self.update_style(False)
        self.clicked.connect(self.on_click)

    def set_theme(self, theme: g15_theme.Theme):
        self.theme = theme
        self.update_display()

    def on_click(self):
        self.is_on = not self.is_on
        self.update_display()
        self.toggled_signal.emit(self.is_on)

    def update_display(self):
        self.setText("G-MODE：开启" if self.is_on else "G-MODE：关闭")
        self.update_style(self.is_on)

    def set_state(self, is_on):
        if self.is_on != is_on:
            self.is_on = is_on
            self.setChecked(is_on)
            self.update_display()

    def update_style(self, is_on):
        t = self.theme
        if is_on:
            self.setStyleSheet(f"""
                QPushButton {{
                    font-size: 16px;
                    font-weight: bold;
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 {t.gmode_on_a}, stop:1 {t.gmode_on_b});
                    color: #FFFFFF;
                    border: none;
                    border-radius: 25px;
                }}
                QPushButton:hover {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 {t.red}, stop:1 {t.gmode_on_a});
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    font-size: 16px;
                    font-weight: bold;
                    background: {t.surface};
                    color: {t.text_muted};
                    border: 3px solid {t.border};
                    border-radius: 25px;
                }}
                QPushButton:hover {{
                    background: {t.surface_hover};
                    border: 3px solid {t.red};
                    color: {t.red};
                }}
            """)


class PowerModeSelector(QFrame):
    mode_changed = pyqtSignal(PowerMode)

    def __init__(self):
        super().__init__()
        self.mode_buttons = {}
        self.current_mode = PowerMode.BALANCED
        self.theme = g15_theme.palette_for(g15_theme.DEFAULT_THEME)
        self.setup_ui()

    def set_theme(self, theme: g15_theme.Theme):
        self.theme = theme
        self.setStyleSheet(g15_theme.card_stylesheet(theme))
        self.selector_title.setStyleSheet(f"""
            font-size: 14px;
            font-weight: 600;
            color: {theme.text};
            padding-bottom: 8px;
        """)
        for mode, btn in self.mode_buttons.items():
            self.update_button_style(btn, mode, mode == self.current_mode)

    def setup_ui(self):
        t = self.theme
        self.setFrameStyle(QFrame.Shape.Box)
        self.setStyleSheet(g15_theme.card_stylesheet(t))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        self.selector_title = QLabel("功耗模式")
        self.selector_title.setStyleSheet(f"""
            font-size: 14px;
            font-weight: 600;
            color: {t.text};
            padding-bottom: 8px;
        """)
        layout.addWidget(self.selector_title)

        self.mode_buttons = {}
        for mode in PowerMode:
            btn = QPushButton(f"  {mode.value.label}")
            btn.setCheckable(True)
            btn.setFixedHeight(38)
            btn.clicked.connect(lambda checked, m=mode: self.select_mode(m))
            self.mode_buttons[mode] = btn
            self.update_button_style(btn, mode, False)
            layout.addWidget(btn)

    def update_button_style(self, btn, mode, selected):
        t = self.theme
        color = mode.value.color
        if selected:
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 13px;
                    font-weight: 600;
                    text-align: left;
                    padding-left: 15px;
                    background: {color};
                    color: #FFFFFF;
                    border: none;
                    border-radius: 6px;
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 13px;
                    font-weight: 500;
                    text-align: left;
                    padding-left: 15px;
                    background: {t.surface};
                    color: {t.text_muted};
                    border: 2px solid {t.border};
                    border-radius: 6px;
                }}
                QPushButton:hover {{
                    background: {t.surface_hover};
                    color: {color};
                    border: 2px solid {color};
                }}
            """)

    def select_mode(self, mode: PowerMode):
        self.current_mode = mode
        for m, btn in self.mode_buttons.items():
            selected = (m == mode)
            btn.setChecked(selected)
            self.update_button_style(btn, m, selected)
        self.mode_changed.emit(mode)

    def set_mode(self, mode: PowerMode):
        if mode in self.mode_buttons:
            for m, btn in self.mode_buttons.items():
                selected = (m == mode)
                btn.setChecked(selected)
                self.update_button_style(btn, m, selected)
            self.current_mode = mode


class SystemTrayIcon(QSystemTrayIcon):
    toggle_g_mode = pyqtSignal()
    show_window = pyqtSignal()
    quit_app = pyqtSignal()
    theme_toggled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.g_mode_active = False
        self.cpu_temp = 0
        self.gpu_temp = 0
        self.theme = g15_theme.palette_for(g15_theme.DEFAULT_THEME)
        self.create_icon()
        self.create_menu()

    def set_theme(self, theme: g15_theme.Theme):
        self.theme = theme
        self.create_icon()
        self.create_menu()
        self.update_status(self.g_mode_active, self.cpu_temp, self.gpu_temp)

    def create_icon(self):
        t = self.theme
        size = 64
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center = size // 2
        radius = 20

        gradient = QRadialGradient(center, center, radius)
        if self.g_mode_active:
            gradient.setColorAt(0, QColor(t.tray_active_a))
            gradient.setColorAt(1, QColor(t.tray_active_b))
        else:
            gradient.setColorAt(0, QColor(t.tray_idle_a))
            gradient.setColorAt(1, QColor(t.tray_idle_b))
        painter.setBrush(QBrush(gradient))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center - radius, center - radius, radius * 2, radius * 2)

        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.setFont(QFont("Sans", 14 if self.g_mode_active else 11, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "G" if self.g_mode_active else "FAN")

        if self.g_mode_active:
            painter.setBrush(QColor("#FFFFFF"))
            painter.setPen(Qt.PenStyle.NoPen)
            dot_size = 6
            positions = [
                (8, 8), (size - 8 - dot_size, 8),
                (8, size - 8 - dot_size), (size - 8 - dot_size, size - 8 - dot_size)
            ]
            for x, y in positions:
                painter.drawEllipse(x, y, dot_size, dot_size)

        painter.end()
        self.setIcon(QIcon(pixmap))

    def create_menu(self):
        menu = QMenu()

        self.g_mode_action = QAction("切换 G-Mode", self)
        self.g_mode_action.triggered.connect(self.toggle_g_mode.emit)
        menu.addAction(self.g_mode_action)

        menu.addSeparator()

        self.temp_action = QAction("CPU：--°C | GPU：--°C", self)
        self.temp_action.setEnabled(False)
        menu.addAction(self.temp_action)

        menu.addSeparator()

        self.theme_action = QAction(self.theme_switch_text(), self)
        self.theme_action.triggered.connect(self.theme_toggled.emit)
        menu.addAction(self.theme_action)

        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self.show_window.emit)
        menu.addAction(show_action)

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app.emit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)

    def theme_switch_text(self) -> str:
        target = g15_theme.LIGHT if self.theme.name == g15_theme.DARK else g15_theme.DARK
        return f"切换到{g15_theme.palette_for(target).label}主题"

    def update_status(self, g_mode: bool, cpu_temp: int, gpu_temp: int):
        self.g_mode_active = g_mode
        self.cpu_temp = cpu_temp
        self.gpu_temp = gpu_temp

        self.create_icon()

        status = "已开启" if g_mode else "未开启"
        self.setToolTip(
            f"Dell G15 控制中心\nG-Mode：{status}\nCPU：{cpu_temp}°C | GPU：{gpu_temp}°C"
        )

        self.temp_action.setText(f"CPU：{cpu_temp}°C | GPU：{gpu_temp}°C")
        self.g_mode_action.setText("关闭 G-Mode" if g_mode else "开启 G-Mode")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        QApplication.setStyle('Fusion')

        self.daemon_client = G15DaemonClient()
        self.reader: SysfsReader | None = None
        if not self.daemon_client.daemon_available:
            # 降级模式：daemon 未运行时，仍可用本地 sysfs 只读监控
            probe = SysfsReader()
            if probe.probe():
                self.reader = probe
                self.daemon_client.daemon_available = True
            else:
                self.show_daemon_required_dialog()
                return

        self.settings = None
        self.custom_message_shown = False
        self.autostart_manager = AutoStartManager()
        self.theme_manager = ThemeManager()
        self.theme = g15_theme.palette_for(self.theme_manager.current)
        self.mode_changing = False
        self.initial_sync_done = False

        self.setup_ui()
        self.setup_monitoring()
        self.setup_tray()
        self.sync_initial_state()

    def is_degraded(self) -> bool:
        """daemon 不可用、仅本地只读监控时为 True。"""
        return self.reader is not None

    def show_daemon_required_dialog(self):
        app = QApplication.instance()

        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Dell G15 控制中心")
        msg.setText("<b>需要 G15 后台服务（g15-daemon）</b>")
        msg.setInformativeText(
            "未检测到后台服务，且本机没有可读的传感器接口。\n\n"
            "请先启动后台服务：\n"
            "<b>sudo systemctl start g15-daemon</b>\n\n"
            "若尚未安装，请执行：<b>sudo ./install.sh</b>"
        )

        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.setDefaultButton(QMessageBox.StandardButton.Ok)
        msg.setStyleSheet(g15_theme.app_stylesheet(self.theme))

        msg.exec()
        app.quit()

    def setup_ui(self):
        t = self.theme
        self.setWindowTitle("Dell G15 控制中心")
        self.setFixedSize(1000, 600)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(15)

        self.title_label = QLabel("Dell G15 控制中心")
        self.title_label.setStyleSheet(f"""
            font-size: 24px;
            font-weight: bold;
            color: {t.text};
        """)

        self.model_label = QLabel(self.model_text())
        self.model_label.setStyleSheet(f"""
            font-size: 12px;
            color: {t.text_muted};
            padding: 4px 10px;
            background: {t.surface};
            border: 1px solid {t.border};
            border-radius: 12px;
        """)

        self.mode_badge = QLabel(self.mode_badge_text())
        self.mode_badge.setStyleSheet(f"""
            font-size: 12px;
            font-weight: 600;
            color: {t.orange if self.is_degraded() else t.text_muted};
            padding: 4px 10px;
            background: {t.surface};
            border: 1px solid {t.border};
            border-radius: 12px;
        """)

        self.theme_button = QPushButton(self.theme_button_text())
        self.theme_button.setFixedHeight(32)
        self.theme_button.clicked.connect(self.toggle_theme)
        self.apply_theme_button_style()

        self.g_mode_button = GModeButton()
        self.g_mode_button.set_theme(t)
        self.g_mode_button.toggled_signal.connect(self.toggle_g_mode)

        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.model_label)
        header_layout.addWidget(self.mode_badge)
        header_layout.addStretch()
        header_layout.addWidget(self.theme_button)
        header_layout.addWidget(self.g_mode_button)

        self.tabs = QTabWidget()
        self.apply_tabs_style()

        monitor_tab = QWidget()
        monitor_layout = QVBoxLayout(monitor_tab)
        monitor_layout.setSpacing(15)

        thermal_section = QFrame()
        thermal_section.setStyleSheet("QFrame { background: transparent; }")
        thermal_layout = QHBoxLayout(thermal_section)
        thermal_layout.setSpacing(15)

        self.cpu_thermal = ThermalCard("CPU 温度", "°C", 100)
        self.gpu_thermal = ThermalCard("GPU 温度", "°C", 100)
        self.fan1_rpm = ThermalCard("CPU 风扇转速", " RPM", 6000)
        self.fan2_rpm = ThermalCard("GPU 风扇转速", " RPM", 6000)

        thermal_layout.addWidget(self.cpu_thermal)
        thermal_layout.addWidget(self.gpu_thermal)
        thermal_layout.addWidget(self.fan1_rpm)
        thermal_layout.addWidget(self.fan2_rpm)

        fan_section = QFrame()
        fan_section.setStyleSheet("QFrame { background: transparent; }")
        fan_layout = QHBoxLayout(fan_section)
        fan_layout.setSpacing(15)

        self.fan1_control = FanControlCard(1, "CPU 风扇控制")
        self.fan2_control = FanControlCard(2, "GPU 风扇控制")

        self.fan1_control.boost_changed.connect(self.on_fan_boost_changed)
        self.fan2_control.boost_changed.connect(self.on_fan_boost_changed)

        fan_layout.addWidget(self.fan1_control)
        fan_layout.addWidget(self.fan2_control)
        fan_layout.addStretch()

        monitor_layout.addWidget(thermal_section)
        monitor_layout.addWidget(fan_section)
        monitor_layout.addStretch()

        settings_tab = QWidget()
        settings_layout = QHBoxLayout(settings_tab)
        settings_layout.setSpacing(15)

        self.power_selector = PowerModeSelector()
        self.power_selector.set_theme(t)
        self.power_selector.mode_changed.connect(self.on_mode_changed)

        self.info_panel = QFrame()
        self.info_panel.setStyleSheet(g15_theme.card_stylesheet(t))
        info_layout = QVBoxLayout(self.info_panel)

        self.info_text = QLabel(self.info_text_content())
        self.info_text.setStyleSheet(f"""
            font-size: 12px;
            color: {t.text_muted};
            line-height: 1.5;
        """)
        self.info_text.setWordWrap(True)

        self.autostart_checkbox = QCheckBox(self.autostart_text(self.autostart_manager.is_enabled()))
        self.autostart_checkbox.setChecked(self.autostart_manager.is_enabled())
        self.autostart_checkbox.toggled.connect(self.on_autostart_toggled)
        self.autostart_checkbox.toggled.connect(self.refresh_autostart_text)
        self.apply_checkbox_style()

        info_layout.addWidget(self.info_text)
        info_layout.addWidget(self.autostart_checkbox)
        info_layout.addStretch()

        settings_layout.addWidget(self.power_selector)
        settings_layout.addWidget(self.info_panel, 1)

        self.tabs.addTab(monitor_tab, "监控")
        self.tabs.addTab(settings_tab, "设置")

        main_layout.addLayout(header_layout)
        main_layout.addWidget(self.tabs)

    # ---------- 文案 ----------
    def model_text(self) -> str:
        model = self.reader.model_name() if self.reader else "Dell G15"
        return f"型号：{model}"

    def mode_badge_text(self) -> str:
        if self.is_degraded():
            driver = self.reader.backend if self.reader else "未知"
            return f"只读模式（{driver}）"
        return "后台服务已连接"

    def theme_button_text(self) -> str:
        target = g15_theme.LIGHT if self.theme.name == g15_theme.DARK else g15_theme.DARK
        return f"切换到{g15_theme.palette_for(target).label}主题"

    def info_text_content(self) -> str:
        degraded_note = (
            "<br><b>提示：</b>当前为只读模式（后台服务未运行），"
            "仅可查看温度与转速，功耗模式与风扇调节不可用。"
            if self.is_degraded() else ""
        )
        return f"""
<b>使用说明：</b><br><br>
• <b>G-Mode：</b>开启最大散热（性能优先）<br>
• <b>功耗模式：</b>选择内核热策略配置<br>
• <b>手动控制：</b>需先切换到「自定义」模式<br>
• <b>系统托盘：</b>双击图标可显示 / 隐藏窗口<br><br>
<b>硬件：</b>Dell G15 / G16（alienware_wmi）{degraded_note}"""

    # ---------- 样式应用 ----------
    def apply_tabs_style(self):
        t = self.theme
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                background: transparent;
                border: none;
            }}
            QTabBar::tab {{
                background: {t.surface};
                color: {t.text_muted};
                padding: 8px 16px;
                margin-right: 4px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                font-weight: 500;
            }}
            QTabBar::tab:selected {{
                color: {t.text};
                border-bottom: 2px solid {t.accent};
            }}
        """)

    def apply_theme_button_style(self):
        t = self.theme
        self.theme_button.setStyleSheet(f"""
            QPushButton {{
                font-size: 12px;
                font-weight: 500;
                background: {t.surface};
                color: {t.text_muted};
                border: 2px solid {t.border};
                border-radius: 6px;
                padding: 0 12px;
            }}
            QPushButton:hover {{
                background: {t.surface_hover};
                border: 2px solid {t.accent};
                color: {t.accent};
            }}
        """)

    def refresh_autostart_text(self, _checked: bool = False):
        self.autostart_checkbox.setText(self.autostart_text())

    def autostart_text(self, checked: bool = None) -> str:
        if checked is None:
            box = getattr(self, "autostart_checkbox", None)
            checked = box.isChecked() if box is not None else False
        mark = "✓" if checked else "　"
        return f"{mark} 随系统自动启动"

    def apply_checkbox_style(self):
        t = self.theme
        self.autostart_checkbox.setStyleSheet(f"""
            QCheckBox {{
                font-size: 13px;
                font-weight: 500;
                color: {t.text};
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
            }}
            QCheckBox::indicator:unchecked {{
                border: 2px solid {t.text_faint};
                border-radius: 3px;
                background: {t.surface};
            }}
            QCheckBox::indicator:checked {{
                border: 2px solid {t.accent};
                border-radius: 3px;
                background: {t.accent};
            }}
            QCheckBox::indicator:checked:disabled {{
                border: 2px solid {t.text_faint};
                background: {t.text_faint};
            }}
        """)

    def toggle_theme(self):
        """在暗色 / 浅色之间切换，并持久化选择。"""
        target = (
            g15_theme.LIGHT if self.theme.name == g15_theme.DARK
            else g15_theme.DARK
        )
        self.theme_manager.save(target)
        self.apply_theme(g15_theme.palette_for(target))

    def apply_theme(self, theme: g15_theme.Theme):
        """把主题应用到所有部件（不重建窗口，保持当前状态）。"""
        self.theme = theme
        app = QApplication.instance()
        app.setStyleSheet(g15_theme.app_stylesheet(theme))

        t = theme
        self.title_label.setStyleSheet(f"""
            font-size: 24px;
            font-weight: bold;
            color: {t.text};
        """)
        self.model_label.setStyleSheet(f"""
            font-size: 12px;
            color: {t.text_muted};
            padding: 4px 10px;
            background: {t.surface};
            border: 1px solid {t.border};
            border-radius: 12px;
        """)
        self.mode_badge.setStyleSheet(f"""
            font-size: 12px;
            font-weight: 600;
            color: {t.orange if self.is_degraded() else t.text_muted};
            padding: 4px 10px;
            background: {t.surface};
            border: 1px solid {t.border};
            border-radius: 12px;
        """)
        self.info_panel.setStyleSheet(g15_theme.card_stylesheet(t))
        self.info_text.setStyleSheet(f"""
            font-size: 12px;
            color: {t.text_muted};
            line-height: 1.5;
        """)

        for card in (self.cpu_thermal, self.gpu_thermal, self.fan1_rpm, self.fan2_rpm):
            card.set_theme(t)
        for card in (self.fan1_control, self.fan2_control):
            card.set_theme(t)

        self.power_selector.set_theme(t)
        self.g_mode_button.set_theme(t)
        self.tabs.setStyleSheet("")
        self.apply_tabs_style()
        self.apply_theme_button_style()
        self.theme_button.setText(self.theme_button_text())
        self.apply_checkbox_style()

        if hasattr(self, 'tray'):
            self.tray.set_theme(t)

    def setup_monitoring(self):
        self.monitor = SensorMonitor(self.daemon_client, self.reader)
        self.monitor.data_updated.connect(self.update_sensor_data)
        self.monitor.start()

    def setup_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = SystemTrayIcon()
            self.tray.set_theme(self.theme)
            self.tray.toggle_g_mode.connect(self.toggle_g_mode)
            self.tray.show_window.connect(self.show_and_raise)
            self.tray.quit_app.connect(self.quit_application)
            self.tray.theme_toggled.connect(self.toggle_theme)
            self.tray.activated.connect(self.on_tray_activated)
            self.tray.show()


    def update_sensor_data(self, data):
        self.cpu_thermal.update_value(data['cpu_temp'])
        self.gpu_thermal.update_value(data['gpu_temp'])
        self.fan1_rpm.update_value(data['fan1_rpm'])
        self.fan2_rpm.update_value(data['fan2_rpm'])

        self.fan1_control.update_rpm(data['fan1_rpm'])
        self.fan2_control.update_rpm(data['fan2_rpm'])

        g_mode_active = data['g_mode']
        degraded = self.is_degraded()

        # 只读模式下没有写权限，相关控件保持禁用
        self.power_selector.setEnabled(not g_mode_active and not degraded)
        self.fan1_control.setEnabled(not g_mode_active and not degraded)
        self.fan2_control.setEnabled(not g_mode_active and not degraded)
        self.g_mode_button.setEnabled(not degraded)
        self.g_mode_button.setToolTip(
            "只读模式下不可用（需要先启动 g15-daemon 后台服务）" if degraded else ""
        )

        if not self.initial_sync_done:
            if data['power_mode'] == PowerMode.CUSTOM:
                self.fan1_control.sync_manual_state(
                    data.get('fan1_manual', False),
                    data['fan1_boost']
                )
                self.fan2_control.sync_manual_state(
                    data.get('fan2_manual', False),
                    data['fan2_boost']
                )
            self.initial_sync_done = True
        else:
            self.fan1_control.update_boost(data['fan1_boost'])
            self.fan2_control.update_boost(data['fan2_boost'])

        if not self.mode_changing:
            self.power_selector.set_mode(data['power_mode'])
        
        self.g_mode_button.set_state(data['g_mode'])

        if hasattr(self, 'tray'):
            self.tray.update_status(data['g_mode'], data['cpu_temp'], data['gpu_temp'])

    def toggle_g_mode(self, state=None):
        self.daemon_client.toggle_g_mode()
        self.daemon_client.invalidate_cache()
        if hasattr(self, 'monitor'):
            self.monitor.update_once()

    def on_mode_changed(self, mode: PowerMode):
        self.mode_changing = True
        success = self.daemon_client.set_power_mode(mode)
        QTimer.singleShot(2000, lambda: setattr(self, 'mode_changing', False))

        if mode == PowerMode.CUSTOM:
            if not self.custom_message_shown:
                self.custom_message_shown = True
                QMessageBox.information(self, "自定义模式",
                    "已切换到自定义模式。\n请在风扇卡片中开启「手动控制」以调节转速。")
        else:
            for card in (self.fan1_control, self.fan2_control):
                card.manual_enabled = False
                card.manual_toggle.setChecked(False)
                card.manual_toggle.setText(card.manual_text(False))
                card.update_manual_button_style(False)
                card.boost_slider.setEnabled(False)
                card.boost_slider.setValue(0)

    def on_fan_boost_changed(self, fan_id: int, boost: int):
        if self.is_degraded():
            return
        if self.daemon_client.get_power_mode() != PowerMode.CUSTOM:
            # 手动调节转速时自动切到「自定义」模式，保持当前底层配置
            self.power_selector.select_mode(PowerMode.CUSTOM)
        
        self.daemon_client.set_fan_boost(fan_id, boost)
        self.daemon_client.invalidate_cache()

    def on_autostart_toggled(self, enabled: bool):
        try:
            if enabled:
                if self.autostart_manager.enable():
                    QMessageBox.information(self, "已启用开机自启",
                        "Dell G15 控制中心将随系统自动启动。\n\n"
                        "注意：若使用后台服务模式，请确认 g15-daemon 服务已安装并启用。")
                else:
                    self.autostart_checkbox.setChecked(False)
                    QMessageBox.warning(self, "错误",
                        "启用开机自启失败，请检查权限。")
            else:
                if self.autostart_manager.disable():
                    QMessageBox.information(self, "已关闭开机自启",
                        "Dell G15 控制中心将不再随系统自动启动。")
                else:
                    self.autostart_checkbox.setChecked(True)
                    QMessageBox.warning(self, "错误",
                        "关闭开机自启失败，请检查权限。")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"配置开机自启时出错：{e}")
            self.autostart_checkbox.setChecked(not enabled)

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_and_raise()

    def quit_application(self):
        if hasattr(self, 'monitor') and self.monitor:
            self.monitor.stop()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if hasattr(self, 'tray') and self.tray.isVisible():
            self.hide()
            self.tray.showMessage(
                "Dell G15 控制中心",
                "已最小化到系统托盘",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
            event.ignore()
        else:
            if hasattr(self, 'monitor') and self.monitor:
                self.monitor.stop()
            event.accept()
    
    def sync_initial_state(self):
        data = self.daemon_client._get_all_data()
        power_mode = self.daemon_client.get_power_mode()

        if power_mode == PowerMode.CUSTOM:
            fans = data.get("fans", {})
            if fans.get("fan1_manual", False):
                self.fan1_control.sync_manual_state(True, fans.get("fan1_boost", 0))
            if fans.get("fan2_manual", False):
                self.fan2_control.sync_manual_state(True, fans.get("fan2_boost", 0))

        self.power_selector.set_mode(power_mode)
        self.g_mode_button.set_state(data.get("power", {}).get("g_mode", False))


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    app.setStyle('Fusion')

    # 中英文都可用的字体回退链，保证中文界面不出现方块字
    font = QFont("Noto Sans CJK SC", 10)
    font.setFamilies([
        "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei",
        "Microsoft YaHei", "Segoe UI", "Sans Serif",
    ])
    app.setFont(font)

    window = MainWindow()

    if not hasattr(window, 'daemon_client') or not window.daemon_client.daemon_available:
        return

    window.apply_theme(window.theme)
    window.show()

    print("Dell G15 Control Center started")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()