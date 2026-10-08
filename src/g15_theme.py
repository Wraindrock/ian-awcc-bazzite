#!/usr/bin/env python3
"""Dell G15 Control Center — 集中式主题（暗色 / 浅色）与调色板。

本模块把原先散落在各个控件里的硬编码样式表统一成一份调色板，
并提供「暗色 / 浅色」两套主题。默认使用暗色主题。

对外主要接口：
    Theme         —— 单个主题的调色板（不可变 dataclass）。
    DARK / LIGHT  —— 两套内置主题。
    normalize()   —— 把任意用户输入归一化成 "dark" / "light"。
    palette_for() —— 按名字取调色板。
    app_stylesheet()      —— 整个应用的全局 QSS。
    temperature_colors()  —— 温度状态色（按主题区分前景色与底色）。
"""

from __future__ import annotations

from dataclasses import dataclass

DARK = "dark"
LIGHT = "light"
DEFAULT_THEME = DARK

THEME_NAMES = (DARK, LIGHT)


@dataclass(frozen=True)
class Theme:
    """一套主题的全部颜色与派生样式片段。"""

    name: str
    label: str

    # ---- 基础层 ----
    window: str          # 主窗口背景
    surface: str         # 卡片 / 面板背景
    surface_alt: str     # 卡片内部的次级区块背景
    surface_hover: str   # 悬停态背景
    border: str          # 常规描边

    # ---- 文字 ----
    text: str            # 主要文字
    text_muted: str      # 次要文字
    text_faint: str      # 更弱的文字（占位、说明）

    # ---- 品牌 / 强调色 ----
    accent: str
    accent_hover: str
    accent_text: str     # 强调色块上的文字

    # ---- 语义色 ----
    green: str
    yellow: str
    orange: str
    red: str

    # ---- 反色（滑块的白色手柄、勾选标记等） ----
    inverse: str

    # ---- G-Mode 渐变（关闭态 / 开启态） ----
    gmode_off_a: str
    gmode_off_b: str
    gmode_on_a: str
    gmode_on_b: str

    # ---- 托盘图标渐变 ----
    tray_idle_a: str
    tray_idle_b: str
    tray_active_a: str
    tray_active_b: str


DARK_THEME = Theme(
    name=DARK,
    label="暗色",
    window="#1B1E27",
    surface="#242834",
    surface_alt="#2C3140",
    surface_hover="#333A4A",
    border="#3A4152",
    text="#E6E9F0",
    text_muted="#A5AEC3",
    text_faint="#7C869C",
    accent="#4C8DFF",
    accent_hover="#3574E0",
    accent_text="#FFFFFF",
    green="#4CD07D",
    yellow="#E8C547",
    orange="#F0A03C",
    red="#F0616D",
    inverse="#FFFFFF",
    gmode_off_a="#3A4152",
    gmode_off_b="#2C3140",
    gmode_on_a="#FF5B5B",
    gmode_on_b="#C81E1E",
    tray_idle_a="#6E9BFF",
    tray_idle_b="#2F5FCC",
    tray_active_a="#FF6B6B",
    tray_active_b="#C81E1E",
)

LIGHT_THEME = Theme(
    name=LIGHT,
    label="浅色",
    window="#F5F6FA",
    surface="#FFFFFF",
    surface_alt="#F8F9FA",
    surface_hover="#F0F2F6",
    border="#E0E0E0",
    text="#2C3E50",
    text_muted="#666666",
    text_faint="#8A93A5",
    accent="#2196F3",
    accent_hover="#1976D2",
    accent_text="#FFFFFF",
    green="#4CAF50",
    yellow="#FFC107",
    orange="#FF9800",
    red="#F44336",
    inverse="#FFFFFF",
    gmode_off_a="#FFFFFF",
    gmode_off_b="#F0F0F0",
    gmode_on_a="#FF4444",
    gmode_on_b="#CC0000",
    tray_idle_a="#6699FF",
    tray_idle_b="#3366CC",
    tray_active_a="#FF6666",
    tray_active_b="#CC0000",
)


def normalize(name: object) -> str:
    """把任意输入归一化成合法的主题名。"""
    if isinstance(name, str):
        candidate = name.strip().lower()
        if candidate in THEME_NAMES:
            return candidate
    return DEFAULT_THEME


def palette_for(name: object) -> Theme:
    """按名字取调色板，未知名字回落到暗色。"""
    return LIGHT_THEME if normalize(name) == LIGHT else DARK_THEME


def app_stylesheet(theme: Theme) -> str:
    """整个应用的全局样式表（对话框、滚动条、提示等公共部件）。"""
    return f"""
        QWidget {{
            color: {theme.text};
        }}
        QMainWindow {{
            background: {theme.window};
        }}
        QToolTip {{
            background: {theme.surface_alt};
            color: {theme.text};
            border: 1px solid {theme.border};
            border-radius: 4px;
            padding: 4px 8px;
        }}
        QMessageBox {{
            background: {theme.window};
        }}
        QMessageBox QLabel {{
            color: {theme.text};
            font-size: 12px;
        }}
        QMessageBox QPushButton {{
            background: {theme.accent};
            color: {theme.accent_text};
            border: none;
            padding: 8px 24px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 11px;
            min-width: 80px;
        }}
        QMessageBox QPushButton:hover {{
            background: {theme.accent_hover};
        }}
        QScrollBar:vertical {{
            background: {theme.window};
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {theme.border};
            border-radius: 5px;
            min-height: 24px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {theme.accent};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
    """


def card_stylesheet(theme: Theme) -> str:
    """卡片 / 面板容器统一的圆角边框样式。"""
    return f"""
        QFrame {{
            background: {theme.surface};
            border: 2px solid {theme.border};
            border-radius: 12px;
        }}
    """


def temperature_colors(theme: Theme, value: int) -> tuple[str, str, str]:
    """返回温度状态的 (前景色, 文案, 底色)。"""
    if value < 50:
        return theme.accent, "低温", _tint(theme, "accent")
    if value < 70:
        return theme.green, "正常", _tint(theme, "green")
    if value < 85:
        return theme.orange, "偏热", _tint(theme, "orange")
    return theme.red, "过热", _tint(theme, "red")


def rpm_colors(theme: Theme, value: int) -> tuple[str, str, str]:
    """返回转速状态的 (前景色, 文案, 底色)。"""
    if value < 2000:
        return theme.accent, "偏低", _tint(theme, "accent")
    if value < 4000:
        return theme.green, "正常", _tint(theme, "green")
    return theme.orange, "偏高", _tint(theme, "orange")


_TINTS: dict[str, dict[str, str]] = {
    DARK: {
        "accent": "#1E2C46",
        "green": "#1C3328",
        "orange": "#3A2C1A",
        "red": "#3A2028",
    },
    LIGHT: {
        "accent": "#E3F2FD",
        "green": "#E8F5E9",
        "orange": "#FFF3E0",
        "red": "#FFEBEE",
    },
}


def _tint(theme: Theme, key: str) -> str:
    """状态徽章的淡色底。"""
    return _TINTS[theme.name][key]

