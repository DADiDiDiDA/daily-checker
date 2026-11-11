# -*- coding: utf-8 -*-
"""主窗口与应用入口。"""

from __future__ import annotations

import ctypes
import json
import math
import os
import sys
import threading
import time
import winreg

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

import core
import autostart
from backdrop import BACKDROP
from pages import (AddGameDialog, DashboardView, ExcludeDialog, FolderMapDialog, GamesPage,
                   GameRuleDialog, MethodsPages, NotifyPreviewDialog, RuleDialog, ScanDirDialog,
                   SettingsPage, ThresholdDialog)
from theme import build_qss, flat, qc, tint, GLYPH, THEME, glyph_font
from tray import TrayIcon
from widgets import NavButton, Toast


def mode_icon(to_dark: bool) -> QIcon:
    """自绘太阳 / 月亮图标：图标字体缺字时也不会变成怪字符。"""
    color = QColor(THEME.p.text_dim)
    pm = QPixmap(36, 36)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QPointF(18, 18)
    if to_dark:
        outer = QPainterPath()
        outer.addEllipse(QRectF(7, 7, 22, 22))
        cut = QPainterPath()
        cut.addEllipse(QRectF(14, 3, 21, 21))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(outer.subtracted(cut))
    else:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(c, 7.0, 7.0)
        p.setPen(QPen(color, 2.0, Qt.SolidLine, Qt.RoundCap))
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(c.x() + math.cos(a) * 10, c.y() + math.sin(a) * 10),
                       QPointF(c.x() + math.cos(a) * 14, c.y() + math.sin(a) * 14))
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- 亚克力模糊

class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t)]


def _abgr(hex_color: str, alpha: int) -> int:
    c = QColor(hex_color)
    return (alpha << 24) | (c.blue() << 16) | (c.green() << 8) | c.red()


def apply_acrylic(hwnd: int, enabled: bool, tint_hex: str = "#15171C", alpha: int = 0xB4) -> bool:
    """给窗口加亚克力模糊（Win10/11）。返回是否成功。"""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        set_attr = user32.SetWindowCompositionAttribute
        set_attr.argtypes = [ctypes.c_void_p, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
        policy = ACCENT_POLICY()
        policy.AccentState = 4 if enabled else 0        # 4 = ACRYLICBLURBEHIND
        policy.AccentFlags = 0
        policy.GradientColor = _abgr(tint_hex, alpha if enabled else 0)
        policy.AnimationId = 0
        data = WINDOWCOMPOSITIONATTRIBDATA(19, ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
                                          ctypes.sizeof(policy))  # 19 = WCA_ACCENT_POLICY
        set_attr(ctypes.c_void_p(hwnd), ctypes.byref(data))
        return True
    except Exception:
        return False


def set_rounded_corners(hwnd: int) -> None:
    try:
        dwm = ctypes.windll.dwmapi
        pref = ctypes.c_int(2)      # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), ctypes.c_int(33),
                                  ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


# ---------------------------------------------------------------- 后台线程

class CheckWorker(QThread):
    item_ready = Signal(object)
    progress = Signal(int, int, str)
    finished_all = Signal(object)       # dict 结果（含 cancelled 标记）

    def __init__(self, engine: core.Engine, items):
        super().__init__()
        self.engine = engine
        self.items = items

    def run(self):
        try:
            result = self.engine.run_check(
                self.items,
                on_item=lambda it: self.item_ready.emit(it),
                on_progress=lambda d, t, it: self.progress.emit(d, t, it),
            )
            self.finished_all.emit(result)
        except Exception as e:
            self.finished_all.emit({"cancelled": False, "fatal": str(e)})


class TestDingWorker(QThread):
    """「发送测试」放到后台线程：urlopen 最多 10 秒，不能卡住 GUI。"""
    done = Signal(bool, str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            r = self.fn(*self.args)
            ok, info = r if isinstance(r, tuple) else (bool(r), "")
        except Exception as e:
            ok, info = False, str(e)
        self.done.emit(bool(ok), str(info))


class NotifyWorker(QThread):
    """按顺序发送钉钉通知（与原脚本一致，最后一条汇总前停 2 秒）。"""

    step = Signal(str)                  # 正在发送的消息标题
    done = Signal(int, int)             # 成功数 / 总数

    def __init__(self, engine: core.Engine, messages: list):
        super().__init__()
        self.engine = engine
        self.messages = messages
        # 通知与检查各用独立的取消标志：检查被「停止」后残留的 cancel
        # 不该让之后的通知一条都发不出去
        self.cancel = threading.Event()

    def run(self):
        ok = 0
        total = len(self.messages)
        try:
            for i, (title, content) in enumerate(self.messages):
                if self.cancel.is_set():
                    break
                self.step.emit(title)
                good, _ = core.Engine.send_dingtalk(self.engine.cfg, content)
                ok += 1 if good else 0
                # 原脚本：发完倒数第二条后、发最后一条（汇总）前停 2s
                if i == total - 2 and total >= 2:
                    if self.cancel.wait(2):
                        break
        finally:
            self.done.emit(ok, total)


# ---------------------------------------------------------------- 窗口按钮

class WinBtn(QPushButton):
    """自绘的窗口按钮（最小化 / 最大化 / 还原 / 关闭）。"""

    def __init__(self, kind: str, tip: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(38, 28)
        self.setFlat(True)
        self.setFocusPolicy(Qt.NoFocus)
        self._hover = False

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        if self._hover:
            if self.kind == "close":
                painter.fillPath(path, qc(p.danger, 0.92))
            else:
                painter.fillPath(path, qc("#FFFFFF" if p.dark else "#000000", 0.10))
        if self._hover and self.kind == "close":
            col = QColor("#FFFFFF")
        else:
            col = QColor(p.text if self._hover else p.text_dim)
        pen = QPen(col, 1.15)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        cx = r.center().x()
        cy = r.center().y()
        s = 4.2
        if self.kind == "min":
            painter.drawLine(QPointF(cx - s, cy + 0.6), QPointF(cx + s, cy + 0.6))
        elif self.kind == "max":
            painter.drawRect(QRectF(cx - s, cy - s, s * 2, s * 2))
        elif self.kind == "restore":
            painter.drawRect(QRectF(cx - s + 1.8, cy - s - 0.8, s * 2 - 1.8, s * 2 - 1.8))
            painter.drawRect(QRectF(cx - s - 0.8, cy - s + 1.8, s * 2 - 1.8, s * 2 - 1.8))
        elif self.kind == "close":
            painter.drawLine(QPointF(cx - s, cy - s), QPointF(cx + s, cy + s))
            painter.drawLine(QPointF(cx + s, cy - s), QPointF(cx - s, cy + s))
        painter.end()


# ---------------------------------------------------------------- 主窗口

class MainWindow(QWidget):
    def __init__(self, start_hidden: bool = False, cli: dict | None = None):
        super().__init__()
        self.cfg = core.load_config()
        THEME.set_preset(self.cfg.get("ui", {}).get("preset", "acrylic"))
        THEME.set_mode(self.cfg.get("ui", {}).get("theme", "auto"))
        THEME.set_blur(bool(self.cfg.get("ui", {}).get("blur", True)))

        # 启动清理：首页记号次日失效；旧格式手动状态升级；检测数据目录迁移
        core.migrate_manual_states(self.cfg)
        core.migrate_cache_state(self.cfg)
        if core.expire_marks(self.cfg):
            core.save_config(self.cfg)

        self.engine = core.Engine(self.cfg)
        self.games: list[core.Game] = []
        self._default_mb_seen = int((self.cfg.get("check") or {}).get(
            "threshold_mb", core.DEFAULT_LOG_THRESHOLD_MB))
        self.last_result: dict | None = None
        self.check_worker = None
        self.notify_worker = None
        self.tray: TrayIcon | None = None
        self._silent_check = start_hidden
        self._quitting = False
        self._cli = cli or {}
        self._dragging = False
        self._drag_pos = QPoint()
        self._bg = None
        self._bg_timer = QTimer(self)
        self._bg_timer.setSingleShot(True)
        self._bg_timer.timeout.connect(self._rebuild_backdrop)

        self.setWindowTitle(core.APP_NAME)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window | Qt.WindowMinMaxButtonsHint)
        if os.environ.get("AM_TOPMOST") == "1":
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # 最小尺寸按屏幕可用区自适应，避免高 DPI / 小屏笔记本上窗口超出屏幕
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        min_w = min(1020, max(720, int(avail.width() * 0.92))) if avail else 1020
        min_h = min(660, max(500, int(avail.height() * 0.88))) if avail else 660
        self.setMinimumSize(min_w, min_h)
        geo = (self.cfg.get("ui") or {}).get("geometry") or []
        if len(geo) == 4:
            x, y, w, h = (int(v) for v in geo)
            if avail:
                # 拔掉副屏后旧坐标可能整个落在屏幕外，先夹回当前屏幕
                w = max(min_w, min(w, avail.width()))
                h = max(min_h, min(h, avail.height()))
                x = max(avail.left(), min(x, avail.right() - w + 1))
                y = max(avail.top(), min(y, avail.bottom() - h + 1))
            self.setGeometry(x, y, w, h)
        else:
            self.resize(1120, 740)

        # 主题要先于 UI 构建同步，否则页面控件会拿旧调色板画字
        self._sync_system_theme()
        self._build_ui()
        self._apply_theme()
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme)

        self.toast = Toast(self)
        self._init_tray()
        self._init_scheduler()
        shot = os.environ.get("AM_SHOT", "").strip()
        if shot:
            QTimer.singleShot(2600, lambda: self._dev_shot(shot))
        if self._cli.get("check"):
            QTimer.singleShot(500, lambda: self.start_check(silent=True))
        elif not start_hidden:
            QTimer.singleShot(600, lambda: self.start_check(silent=False))
        else:
            QTimer.singleShot(4000, lambda: self.start_check(silent=True))

    # ---------- UI ----------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 侧边栏
        self.side = QWidget(self)
        self.side.setFixedWidth(226)
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(14, 14, 10, 14)
        sl.setSpacing(4)

        brand = QWidget()
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(6, 6, 6, 10)
        bl.setSpacing(10)
        self.logo = QLabel()
        self.logo.setFixedSize(30, 30)
        bl.addWidget(self.logo)
        bcol = QVBoxLayout()
        bcol.setSpacing(0)
        self.brand_name = QLabel(core.APP_NAME)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        self.brand_name.setFont(f)
        self.brand_ver = QLabel(f"v{core.APP_VERSION}")
        f2 = QFont("Microsoft YaHei UI")
        f2.setPixelSize(11)
        self.brand_ver.setFont(f2)
        bcol.addWidget(self.brand_name)
        bcol.addWidget(self.brand_ver)
        bl.addLayout(bcol)
        bl.addStretch(1)
        sl.addWidget(brand)
        for w in [brand] + brand.findChildren(QWidget):
            w.installEventFilter(self)

        self.nav_group = []
        self.nav_home = NavButton("home", "home", "总览")
        self.nav_games = NavButton("games", "apps", "游戏列表")
        self.nav_methods = NavButton("methods", "check", "判定规则", chevron=True)
        self.nav_settings = NavButton("settings", "settings", "设置")

        self.nav_fm = NavButton("fm", "", "文件夹关键词映射", sub=True)
        self.nav_rules = NavButton("rules", "", "规则库", sub=True)
        self.nav_game_rules = NavButton("game_rules", "", "程序检查规则", sub=True)
        self.sub_navs = [self.nav_fm, self.nav_rules, self.nav_game_rules]
        self.sub_holder = QWidget()
        flat(self.sub_holder)
        sub_lay = QVBoxLayout(self.sub_holder)
        sub_lay.setContentsMargins(0, 0, 0, 2)
        sub_lay.setSpacing(0)
        for b in self.sub_navs:
            sub_lay.addWidget(b)

        for btn in (self.nav_home, self.nav_games, self.nav_methods):
            btn.clicked.connect(self._on_nav)
            self.nav_group.append(btn)
            sl.addWidget(btn)
        sl.addWidget(self.sub_holder)
        self.nav_group.extend(self.sub_navs)
        for btn in self.sub_navs:
            btn.clicked.connect(self._on_nav)
        self.nav_settings.clicked.connect(self._on_nav)
        self.nav_group.append(self.nav_settings)
        sl.addWidget(self.nav_settings)
        self._last_methods_key = "fm"
        self._set_methods_expanded(True)

        sl.addStretch(1)

        self.side_status = QLabel("")
        fs = QFont("Microsoft YaHei UI")
        fs.setPixelSize(11)
        self.side_status.setFont(fs)
        self.side_status.setTextFormat(Qt.RichText)
        self.side_status.setWordWrap(True)
        self.side_status.setContentsMargins(10, 0, 6, 4)
        sl.addWidget(self.side_status)

        self.theme_btn = QPushButton("切换明暗")
        self.theme_btn.setObjectName("ghost")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setIconSize(QSize(16, 16))
        self.theme_btn.setFixedHeight(32)
        self.theme_btn.clicked.connect(self._toggle_quick_theme)
        sl.addWidget(self.theme_btn)

        root.addWidget(self.side)

        # 右侧
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        self.header = QWidget()
        self.header.setFixedHeight(46)
        self.header.setMouseTracking(True)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(22, 6, 6, 0)
        hl.setSpacing(2)
        self.page_title = QLabel("总览")
        fpt = QFont("Microsoft YaHei UI")
        fpt.setPixelSize(15)
        fpt.setWeight(QFont.DemiBold)
        self.page_title.setFont(fpt)
        hl.addWidget(self.page_title)
        hl.addStretch(1)

        self.btn_min = WinBtn("min", "最小化")
        self.btn_max = WinBtn("max", "最大化")
        self.btn_close = WinBtn("close", "关闭")
        for b in (self.btn_min, self.btn_max, self.btn_close):
            hl.addWidget(b, 0, Qt.AlignTop)
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_max.clicked.connect(self._toggle_max)
        self.btn_close.clicked.connect(self.close)
        rl.addWidget(self.header)
        for w in [self.header, self.page_title]:
            w.installEventFilter(self)

        self.stack = QStackedWidget()
        self.stack.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.stack, 1)
        root.addWidget(right, 1)

        # 页面
        self.page_home = DashboardView()
        self.page_home.set_config(self.cfg)
        self.page_home.check_requested.connect(self.start_check)
        self.page_home.cancel_requested.connect(self.cancel_tasks)
        self.page_home.notify_requested.connect(self.send_notify)
        self.page_home.goto_games.connect(lambda: self._on_nav("games"))
        self.page_home.fixed_requested.connect(self.toggle_fixed)
        self.page_home.summary_sort_changed.connect(self.set_summary_sort)
        self.page_home.state_requested.connect(self.set_manual_state)

        self.page_games = GamesPage()
        self.page_games.row_open.connect(self.open_game_folder)
        self.page_games.row_log.connect(self.open_game_log)
        self.page_games.row_debug.connect(self.open_game_debug)
        self.page_games.row_threshold.connect(self.edit_threshold)
        self.page_games.row_method.connect(self.edit_method)
        self.page_games.row_exclude.connect(self.toggle_exclude)
        self.page_games.sort_changed.connect(self.on_sort_changed)
        self.page_games.order_changed.connect(self.on_order_changed)

        self.page_methods = MethodsPages(self.cfg)
        mp = self.page_methods
        # 程序检查规则页的常见排序要用带检查结果的运行时对象（无错天数 / 日志大小等）
        mp.games.scan_items_provider = lambda: self.games
        mp.rules.new_rule_requested.connect(self.new_rule)
        mp.rules.edit_rule_requested.connect(self.edit_rule)
        mp.rules.duplicate_rule_requested.connect(self.duplicate_rule)
        mp.rules.delete_rule_requested.connect(self.delete_rule)
        mp.games.add_game_requested.connect(self.add_game)
        mp.games.game_edit_requested.connect(self.edit_game)
        mp.games.game_rule_changed.connect(self.set_game_rule)
        mp.games.game_remove_requested.connect(self.remove_game)
        mp.games.game_threshold_changed.connect(self.set_game_threshold)
        mp.games.game_threshold_enabled_changed.connect(self.set_game_threshold_enabled)
        mp.games.config_changed.connect(self._save_cfg)
        mp.rules.set_default_requested.connect(self.set_default_rule)
        mp.folder.folder_map_manage_requested.connect(self.manage_folder_map)
        mp.folder.log_folders_changed.connect(self.on_log_folders_changed)

        self.page_settings = SettingsPage(self.cfg)
        self._wire_settings(self.page_settings)

        for page in (self.page_home, self.page_games, mp.folder, mp.rules,
                     mp.games, self.page_settings):
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(22, 4, 18, 18)
            wl.addWidget(page)
            self.stack.addWidget(wrap)
        self._page_wraps = [self.stack.widget(i) for i in range(self.stack.count())]

        self.nav_home.set_active(True)
        self._update_brand()

        # 键盘快捷键
        from PySide6.QtGui import QKeySequence, QShortcut
        for key, seq in (("home", "Ctrl+1"), ("games", "Ctrl+2"),
                         ("rules", "Ctrl+3"), ("settings", "Ctrl+4")):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(lambda k=key: self._on_nav(k))
        QShortcut(QKeySequence("F5"), self).activated.connect(self.start_check)

    def _wire_settings(self, page: SettingsPage) -> None:
        page.theme_changed.connect(self._apply_theme)
        page.config_changed.connect(self._save_cfg)
        page.autostart_requested.connect(self._set_autostart)
        page.test_ding_requested.connect(self._test_ding)
        page.exclude_manage_requested.connect(self.manage_excludes)
        page.scan_dir_manage_requested.connect(self.manage_scan_dirs)

    # ---------- 主题 ----------
    def _sync_system_theme(self) -> None:
        try:
            hints = QGuiApplication.styleHints()
            dark = hints.colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            dark = True
        THEME.set_system_dark(dark)

    def _on_system_scheme(self, scheme) -> None:
        THEME.set_system_dark(scheme == Qt.ColorScheme.Dark)
        if THEME.mode == "auto":
            self._apply_theme()

    def _toggle_quick_theme(self) -> None:
        # 三态循环，能回到「跟随系统」；并立即落盘，别等下次退出才生效
        nxt = {"auto": "light", "light": "dark", "dark": "auto"}.get(THEME.mode, "auto")
        THEME.set_mode(nxt)
        self.cfg.setdefault("ui", {})["theme"] = nxt
        core.save_config(self.cfg)
        self._apply_theme()

    def _apply_theme(self) -> None:
        p = THEME.p
        QApplication.instance().setStyleSheet(build_qss(p))
        self.theme_btn.setText("主题：" + {"auto": "跟随系统", "light": "浅色",
                                      "dark": "深色"}.get(THEME.mode, "跟随系统"))
        self.theme_btn.setIcon(mode_icon(not THEME.is_dark))

        tint(self.brand_name, p.text)
        tint(self.brand_ver, p.text_muted)
        tint(self.page_title, p.text)
        tint(self.side_status, p.text_muted)
        for b in (self.btn_min, self.btn_max, self.btn_close):
            b.update()
        for nav in self.nav_group:
            nav.update()
        self._update_logo()
        self.page_home.refresh_theme()
        self.page_games.refresh_theme()
        if hasattr(self, "page_methods"):
            self.page_methods.refresh_theme()
        # 只刷配色，不重建整页（重建会丢掉用户正在输入、还没失焦的 Webhook 等内容）
        if hasattr(self, "page_settings"):
            self.page_settings.refresh_theme()
        self._update_background()
        self.update()


    def _update_logo(self) -> None:
        p = THEME.p
        pm = QPixmap(60, 60)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, 30, 30)
        path = QPainterPath()
        path.addRoundedRect(rect, 9, 9)
        # 与 assets/icon.ico 同款青绿渐变（_test/make_icon_dc.py）
        g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g.setColorAt(0, QColor(60, 202, 168))
        g.setColorAt(1, QColor(23, 138, 108))
        painter.fillPath(path, g)
        f = glyph_font(15)
        painter.setFont(f)
        painter.setPen(QPen(qc("#FFFFFF", 0.96)))
        painter.drawText(rect, Qt.AlignCenter, GLYPH["check"])
        painter.end()
        self.logo.setPixmap(pm)

    # ---------- 背景绘制 ----------
    def _update_background(self) -> None:
        p = THEME.p
        try:
            hwnd = int(self.winId())
            set_rounded_corners(hwnd)
            if os.environ.get("AM_DWM_ACRYLIC") == "1" and THEME.blur_active:
                apply_acrylic(hwnd, True, p.bg, 168 if p.dark else 120)
            else:
                apply_acrylic(hwnd, False)
        except Exception:
            pass
        self._rebuild_backdrop()

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(30, self._update_background)

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        painter.setClipPath(path)

        if self._bg is not None:
            painter.drawPixmap(0, 0, self._bg)
        else:
            painter.fillPath(path, qc(p.bg))

        painter.fillRect(QRectF(0, 0, self.side.width(), self.height()),
                         qc(p.sidebar, 0.05 if p.dark else 0.5))

        painter.setClipping(False)
        painter.setPen(QPen(qc(p.border, 0.11 if p.dark else 0.08), 1))
        painter.drawPath(path)
        painter.end()

    def _rebuild_backdrop(self) -> None:
        try:
            pos = self.mapToGlobal(QPoint(0, 0))
            self._bg = BACKDROP.render(THEME.p, (self.width(), self.height()), pos,
                                       THEME.blur_active)
            self.update()
        except Exception:
            self._bg = None

    def moveEvent(self, e):
        super().moveEvent(e)
        self._bg_timer.start(140)

    # ---------- 窗口行为 ----------
    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self.btn_max.set_kind("max")
        else:
            self.showMaximized()
            self.btn_max.set_kind("restore")

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            gp = ev.globalPosition().toPoint()
            if self.mapFromGlobal(gp).y() < 48:
                self._dragging = True
                self._drag_pos = gp - self.frameGeometry().topLeft()
        elif t == QEvent.MouseMove and self._dragging:
            if not self.isMaximized():
                self.move(ev.globalPosition().toPoint() - self._drag_pos)
        elif t == QEvent.MouseButtonRelease:
            self._dragging = False
        elif t == QEvent.MouseButtonDblClick:
            gp = ev.globalPosition().toPoint()
            if self.mapFromGlobal(gp).y() < 48:
                self._toggle_max()
                return True
        return super().eventFilter(obj, ev)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "toast"):
            self.toast.move((self.width() - self.toast.width()) // 2,
                            self.height() - self.toast.height() - 26)
        if hasattr(self, "_bg_timer"):
            self._bg_timer.start(120)

    # ---------- 导航 ----------
    SUB_KEYS = ("fm", "rules", "game_rules")
    TITLES = {"home": "总览", "games": "游戏列表", "fm": "文件夹关键词映射",
              "rules": "规则库", "game_rules": "程序检查规则",
              "settings": "设置"}
    PAGE_INDEX = {"home": 0, "games": 1, "fm": 2, "rules": 3,
                  "game_rules": 4, "settings": 5}

    def _set_methods_expanded(self, on: bool) -> None:
        self.sub_holder.setVisible(on)
        self.nav_methods.set_expanded(on)

    def _on_nav(self, key: str) -> None:
        if key == "methods":
            # 点一级项：展开 / 收起二级菜单，并回到上次看的子页
            on = not self.sub_holder.isVisible()
            self._set_methods_expanded(on)
            if not on:
                return
            key = self._last_methods_key
        if key in self.SUB_KEYS:
            self._last_methods_key = key
            if not self.sub_holder.isVisible():
                self._set_methods_expanded(True)
        self.stack.setCurrentIndex(self.PAGE_INDEX[key])
        for nav in self.nav_group:
            nav.set_active(nav.key == key)
        # 二级项高亮时一级项不再重复铺底色，避免两块高亮叠在一起
        self.nav_methods.set_active(False)
        self.page_title.setText(self.TITLES[key])

    # ---------- 托盘 / 定时 ----------
    def _dev_shot(self, path: str) -> None:
        try:
            self.showNormal()
            self.repaint()
            QApplication.processEvents()
            self.grab().save(path)
        except Exception:
            pass
        if os.environ.get("AM_SHOT_EXIT") == "1":
            QTimer.singleShot(200, self.quit_app)

    def _init_tray(self) -> None:
        icon_file = resource_path("assets/icon.ico")
        icon = QIcon(icon_file) if os.path.exists(icon_file) else QIcon()
        self.tray = TrayIcon(icon, self)
        self.tray.open_main.connect(self.show_main)
        self.tray.open_settings.connect(lambda: self.show_main("settings"))
        self.tray.check_now.connect(self._tray_check_now)
        self.tray.send_notify.connect(self.send_notify)
        self.tray.cancel_task.connect(self.cancel_tasks)
        self.tray.autostart_toggled.connect(self._set_autostart)
        self.tray.quit_requested.connect(self.quit_app)
        self.tray.refresh_style()
        self.tray.set_autostart(autostart.is_enabled())
        self.tray.set_tooltip("", 0)
        self.tray.show()

    def _init_scheduler(self) -> None:
        self.sched_timer = QTimer(self)
        self.sched_timer.timeout.connect(self._on_schedule_tick)
        self._apply_schedule()

    def _apply_schedule(self) -> None:
        bg = self.cfg.setdefault("background", {})
        if not hasattr(self, "sched_timer"):
            return
        self.sched_timer.stop()
        if bg.get("check_enabled", False):
            minutes = max(5, int(bg.get("interval_min", 720) or 720))
            self.sched_timer.start(minutes * 60_000)

    def _on_schedule_tick(self) -> None:
        if self.check_worker and self.check_worker.isRunning():
            return
        self.start_check(silent=True)

    def show_main(self, page: str | None = None) -> None:
        self._silent_check = False
        if page:
            self._on_nav(page)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _notify(self, title: str, body: str) -> None:
        if self.tray is not None:
            self.tray.notify(title, body)

    def _set_autostart(self, on: bool) -> None:
        ok = autostart.set_enabled(bool(on), silent=True)
        self.cfg.setdefault("background", {})["run_at_startup"] = bool(on and ok)
        self._save_cfg()
        if self.tray is not None:
            self.tray.set_autostart(bool(on and ok))
        if hasattr(self, "page_settings"):
            self.page_settings.refresh_background()
        self.toast.show_text("已设置为开机后台启动" if (on and ok) else
                             ("已取消开机启动" if not on else "设置开机启动失败"))

    def _shutdown(self) -> None:
        """统一退出入口：先取消并等待所有后台线程，再保存配置退出。

        托盘「退出」以前绕过 closeEvent，扫描/通知还在跑就 QApplication.quit()，
        会留下半成品状态甚至 QThread destroyed while running 崩溃。
        """
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        self._quitting = True
        self.engine.cancel.set()
        try:
            for name in ("check_worker", "notify_worker", "_ding_test_worker"):
                w = getattr(self, name, None)
                if w is None:
                    continue
                wc = getattr(w, "cancel", None)
                if wc is not None and hasattr(wc, "set"):
                    wc.set()
                if w.isRunning():
                    if not w.wait(8000):
                        # 通知线程最长可达 42s（4 条消息 + 停顿），仍在跑就销毁会崩溃，
                        # 延长等待，绝不静默放行
                        w.wait(40000)
        finally:
            # 保存失败不能阻断退出，否则下次退出因 _shutting_down 直接 return
            try:
                self._save_cfg()
            except Exception:
                pass
            if self.tray is not None:
                self.tray.hide()
            QApplication.quit()

    def quit_app(self) -> None:
        self._shutdown()

    # ---------- 数据与检查 ----------
    def _attention_count(self) -> int:
        # 和首页同一口径：已排除 / 强制已完成的不计入；
        # 关注数含存疑，否则只有存疑时托盘仍显示「一切正常」
        susp_on = core.log_threshold_check_enabled(self.cfg)
        return sum(1 for i in self.games
                   if core.home_attention_visible(self.cfg, i)
                   and (i.has_error
                        or core.effective_failed(self.cfg, i)
                        or (i.suspicious and susp_on)))

    def _update_brand(self) -> None:
        import html as _html
        n_err = sum(1 for i in self.games
                    if core.home_attention_visible(self.cfg, i) and i.has_error)
        n_fail = sum(1 for i in self.games
                     if core.home_attention_visible(self.cfg, i)
                     and core.effective_failed(self.cfg, i))
        dirs = core.get_scan_dirs(self.cfg)
        if dirs:
            first = os.path.basename(os.path.normpath(dirs[0])) or dirs[0]
            scan = first + (f" 等 {len(dirs)} 个" if len(dirs) > 1 else "")
        else:
            scan = "未设置"
        # 目录名可能含 <>& 等字符，必须转义，否则会被当富文本格式渲染
        scan = _html.escape(scan)
        self.side_status.setText(
            f'<p style="margin:0;line-height:160%">监控目录 {scan}</p>'
            f'<p style="margin:4px 0 0 0;line-height:160%">游戏 {len(self.games)} 款'
            f"　异常 {n_err}　未完成 {n_fail}</p>")

    def _tray_check_now(self) -> None:
        """托盘「立即检查」是用户主动动作，窗口隐藏时也要给一点反馈。"""
        self.start_check(silent=False)
        if not self.isVisible() and self.tray is not None:
            self.tray.notify("正在检查", "正在检查游戏日常，完成后托盘角标会更新")

    def start_check(self, silent: bool = False) -> None:
        if self.check_worker and self.check_worker.isRunning():
            return
        self._silent_check = silent
        self.engine.cancel.clear()
        games = self.engine.collect()
        self.games = games
        self.page_home.set_items(games)
        self.page_games.set_items(games)
        self.page_home.set_scan_state(True, 0, len(games), "正在扫描日志…")
        if self.tray is not None:
            self.tray.set_busy(True, "正在检查日常…")
        self._update_brand()

        self.check_worker = CheckWorker(self.engine, games)
        self.check_worker.item_ready.connect(self._on_item_checked)
        self.check_worker.progress.connect(self._on_check_progress)
        self.check_worker.finished_all.connect(self._on_check_finished)
        self.check_worker.start()

    def _on_item_checked(self, item) -> None:
        self.page_home.update_item(item)
        self.page_games.update_item(item)

    def _on_check_progress(self, done: int, total: int, name: str) -> None:
        self.page_home.set_scan_state(True, done, total, name)

    def _on_check_finished(self, result: dict) -> None:
        self.page_home.set_scan_state(False)
        if result.get("cancelled"):
            self.toast.show_text("检查已停止")
            if self.tray is not None:
                self.tray.set_busy(False)
            return
        if result.get("fatal"):
            self.toast.show_text(f"检查出错：{result['fatal']}", ok=False)
            if self.tray is not None:
                self.tray.set_busy(False)
            return

        self.last_result = result
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.page_games.refresh_order()      # 结果都回来了，按当前排序方式落定
        self.page_home.last_check_time = time.strftime("%H:%M:%S")
        self.page_home.refresh_stats()
        n_att = self._attention_count()
        if self.tray is not None:
            self.tray.set_busy(False)
            self.tray.set_tooltip(stamp, n_att)
        self._update_brand()

        n_fail = len(result["failed"])
        n_err = len(result["folder_errors"])
        n_susp = len(result["suspicious"])
        cli = dict(getattr(self, "_cli_override", None) or self._cli)
        self._cli_override = None
        cancelled = bool(result.get("cancelled"))
        cli_mode = bool(cli.get("check") or cli.get("notify"))
        auto = bool((self.cfg.get("background") or {}).get("auto_notify", True))

        if cli_mode:
            # 命令行模式：只有 --notify 才发送，--check 纯检查不发通知
            should_notify = bool(cli.get("notify")) and not cancelled
        else:
            # 手动检查、定时检查、后台检查一视同仁：按设置自动发送
            should_notify = auto and not cancelled
            if not self._silent_check:
                msg = "检查完成"
                if n_err or n_fail:
                    msg += f"：异常 {n_err}、未完成 {n_fail}"
                if n_susp:
                    msg += f"、存疑 {n_susp}"
                tail = ("　·　正在自动推送通知" if should_notify
                        else ("　·　可点「发送通知」推送" if n_att or n_susp else "：一切正常"))
                self.toast.show_text(msg + tail, ok=not (n_err or n_fail))

        if should_notify:
            chans = core.get_notify_channels(self.cfg)
            if "windows" in chans:
                summary = self.engine.build_all_messages(result)[3][1]
                self._notify("日常检查", summary)
            if "dingtalk" in chans:
                self._send_notify_worker()

        # --quit-after 对所有模式生效；若有通知 worker 在跑，由 _on_notify_done 收尾退出
        if cli.get("quit_after") and not (
                self.notify_worker and self.notify_worker.isRunning()):
            QTimer.singleShot(400, self.quit_app)

    def cancel_tasks(self) -> None:
        self.engine.cancel.set()
        self.toast.show_text("已请求停止，等待当前任务结束…")

    # ---------- 通知 ----------
    def send_notify(self) -> None:
        """手动发送：按设置里开启的通知方式推送（可多选）。"""
        chans = core.get_notify_channels(self.cfg)
        if not chans:
            self.toast.show_text("通知已在设置里关闭", ok=False)
            return
        if self.notify_worker and self.notify_worker.isRunning():
            self.toast.show_text("通知正在发送中", ok=False)
            return
        if not self.last_result:
            self.toast.show_text("还没有检查结果，先「开始检查」", ok=False)
            return
        if "dingtalk" in chans:
            messages = self.engine.build_all_messages(self.last_result)
            if os.environ.get("DC_NO_PREVIEW") != "1":
                dlg = NotifyPreviewDialog(self, messages)
                if not dlg.exec():
                    return          # 预览里取消：一条都不发（包括 Windows 通知）
        if "windows" in chans:
            summary = self.engine.build_all_messages(self.last_result)[3][1]
            self._notify("日常检查", summary)
        if "dingtalk" in chans:
            self._send_notify_worker()
        elif "windows" in chans:
            self.toast.show_text("已发送 Windows 系统通知")

    def _send_notify_worker(self) -> None:
        if not self.last_result:
            return
        if self.notify_worker and self.notify_worker.isRunning():
            return
        messages = self.engine.build_all_messages(self.last_result)
        if self.tray is not None:
            self.tray.set_busy(True, "正在发送通知…")
        self.notify_worker = NotifyWorker(self.engine, messages)
        self.notify_worker.step.connect(
            lambda t: self.toast.show_text(f"正在发送：{t}"))
        self.notify_worker.done.connect(self._on_notify_done)
        self.notify_worker.start()

    def _on_notify_done(self, ok: int, total: int) -> None:
        if self.tray is not None:
            self.tray.set_busy(False)
        good = ok == total
        self.toast.show_text(f"钉钉通知发送完成：{ok}/{total}" + ("" if good else "（部分失败，检查配置）"),
                             ok=good)
        # 只在用户开启了系统通知时补充一条结果回执，别绕过「只留钉钉」的设置
        if "windows" in core.get_notify_channels(self.cfg):
            self._notify("日常检查通知已发送",
                         f"{ok}/{total} 条发送成功" + ("" if good else "，部分失败请检查钉钉配置"))
        if self._cli.get("quit_after"):
            QTimer.singleShot(400, self.quit_app)

    def _test_ding(self) -> None:
        w = getattr(self, "_ding_test_worker", None)
        if w is not None and w.isRunning():
            self.toast.show_text("测试消息正在发送中", ok=False)
            return
        if hasattr(self, "page_settings"):
            self.page_settings.set_test_result(None, "正在发送测试消息…")
        w = TestDingWorker(core.Engine.send_dingtalk, self.cfg,
                           "日常检查器测试消息：如果你看到这条，说明通知配置是通的。")
        w.done.connect(self._on_ding_test_done)
        self._ding_test_worker = w
        w.start()

    def _on_ding_test_done(self, ok: bool, info: str) -> None:
        if hasattr(self, "page_settings"):
            self.page_settings.set_test_result(ok, info)
        self.toast.show_text("测试消息已发送" if ok else f"发送失败：{info}", ok=ok)

    # ---------- 其它交互 ----------
    def open_game_folder(self, key: str) -> None:
        """打开这款游戏用的自动化程序根目录。"""
        it = next((i for i in self.games if i.key == key), None)
        if it:
            core.open_in_explorer(it.program_dir or os.path.normpath(it.folder))

    def open_game_debug(self, key: str) -> None:
        it = next((i for i in self.games if i.key == key), None)
        if not it or not it.debug_dir:
            return
        if not os.path.isdir(it.debug_dir):
            # 不再凭空创建目录：路径配错时应该明确提示，而不是造一个空文件夹掩盖问题
            self.toast.show_text(f"目录不存在：{it.debug_dir}", ok=False)
            return
        core.open_in_explorer(it.debug_dir)

    def open_game_log(self, key: str) -> None:
        """打开自动化程序目录下实际在用的那个日志文件夹。

        folder 现在是程序根目录，日志在其下的 logs / log / debug / .log 里；
        旧配置直接填到日志子目录时，program_dir 会自动退一层，结果一样。
        """
        it = next((i for i in self.games if i.key == key), None)
        if not it:
            return
        root = it.program_dir or os.path.normpath(it.folder)
        target = ""
        for name in core.get_log_folders(self.cfg):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                target = p
                break
        if not target:
            target = it.folder
        if not os.path.isdir(target):
            self.toast.show_text(f"自动化程序日志文件夹不存在：{target}", ok=False)
            return
        core.open_in_explorer(target)

    # ---------- 排序 / 手动顺序 ----------
    def on_sort_changed(self, regular: str, special: str) -> None:
        ck = self.cfg.setdefault("check", {})
        ck["sort_mode"] = regular
        ck["sort_special"] = special
        self._save_cfg()
        if special == "manual":
            self.toast.show_text("手动拖拽模式：按住游戏行拖动即可调整顺序")

    def on_order_changed(self, names: list) -> None:
        self.cfg.setdefault("check", {})["manual_order"] = names
        self._save_cfg()

    def edit_threshold(self, key: str) -> None:
        it = next((i for i in self.games if i.key == key), None)
        if not it:
            return
        dlg = ThresholdDialog(self, it.name, it.threshold_mb)
        if not dlg.exec():
            return
        self.set_game_threshold(it.name, dlg.value(), toast=True)

    def set_game_threshold(self, name: str, mb: int, toast: bool = False) -> None:
        """改一款游戏的日志阈值；等于默认值就删掉覆盖，回到跟随默认。"""
        it = next((i for i in self.games if i.name == name), None)
        ck = self.cfg.setdefault("check", {})
        default = int(ck.get("threshold_mb", core.DEFAULT_LOG_THRESHOLD_MB))
        mb = int(mb)
        core.set_threshold_mb(self.cfg, name, mb)
        self._save_cfg()
        if it is not None:
            it.threshold_mb = mb
            on = core.log_threshold_check_enabled(self.cfg) \
                and core.threshold_check_enabled(self.cfg, name)
            it.suspicious = bool(on and it.is_maawork and it.log_size > mb * 1024 * 1024
                                 and it.daily != "failed" and not it.has_error
                                 and not it.excluded)
            if self.last_result:
                susp = {k: v for k, v in self.last_result["suspicious"].items()
                        if k != name} if on else {}
                if it.suspicious:
                    susp[name] = it.log_size
                self.last_result["suspicious"] = susp
            self._refresh_game_row(it)
            self.page_home.refresh_stats()
        if hasattr(self, "page_methods"):
            self.page_methods._refresh_folder_map()
        if toast:
            tail = "（= 默认值，恢复跟随默认）" if mb == default else ""
            self.toast.show_text(f"「{name}」日志阈值已设为 {mb}MB{tail}")
            # 游戏列表右键改的阈值，让程序检查规则页的步进器立刻同步
            self.page_methods.games.refresh(self.cfg)

    def set_game_threshold_enabled(self, name: str, on: bool) -> None:
        """程序检查规则页的分项阈值子开关。"""
        core.set_threshold_enabled(self.cfg, name, on)
        it = next((i for i in self.games if i.name == name), None)
        if it is not None:
            on_all = core.log_threshold_check_enabled(self.cfg)
            en = on and on_all
            it.suspicious = bool(en and it.is_maawork
                                 and it.log_size > it.threshold_mb * 1024 * 1024
                                 and it.daily != "failed" and not it.has_error
                                 and not it.excluded)
            if self.last_result:
                susp = {k: v for k, v in self.last_result["suspicious"].items()
                        if k != name} if en else {}
                if it.suspicious:
                    susp[name] = it.log_size
                self.last_result["suspicious"] = susp
            self._refresh_game_row(it)
            self.page_home.refresh_stats()
        self._save_cfg()
        self.toast.show_text(
            f"「{name}」日志阈值检查已{'开启' if on else '关闭'}"
            + ("" if on else "（不参与存疑判定）"))

    def set_default_rule(self, rid: str) -> None:
        """规则库设为/取消默认规则；未显式绑定规则的游戏都改用默认规则。"""
        was = core.get_default_rule(self.cfg)
        core.set_default_rule(self.cfg, rid)
        self._save_cfg()
        now = core.get_default_rule(self.cfg)
        self.page_methods.rules.refresh(self.cfg)
        self.page_methods.games.refresh(self.cfg)
        if now:
            rname = next((r["name"] for r in core.get_rules(self.cfg)
                          if r["id"] == now), now)
            self.toast.show_text(f"已把「{rname}」设为默认规则，未设定规则的游戏都改用它")
        elif was:
            self.toast.show_text("已取消默认规则，未设定的游戏回到原来的判断方式")

    # ---------- 判定规则库 ----------
    def edit_method(self, key: str) -> None:
        """右键「设置判断方式」：给这款游戏换一条规则。"""
        it = next((i for i in self.games if i.key == key), None)
        if not it:
            return
        dlg = GameRuleDialog(self, it.name, core.get_rules(self.cfg),
                             core.rule_id_for(self.cfg, it.name)
                             if core.is_game_bound(self.cfg, it.name) else "")
        if dlg.exec():
            self.set_game_rule(it.name, dlg.value())

    def new_rule(self) -> None:
        dlg = RuleDialog(self)
        if not dlg.exec():
            return
        r = core.save_rule(self.cfg, dlg.value())
        self._save_cfg()
        self._after_rule_change()
        self.toast.show_text(f"已新建规则「{r['name']}」，可在游戏下拉里引用")

    def edit_rule(self, rule_id: str) -> None:
        r = core.get_rule(self.cfg, rule_id)
        if not r:
            return
        dlg = RuleDialog(self, r)
        if not dlg.exec():
            return
        saved = core.save_rule(self.cfg, dlg.value())
        self._save_cfg()
        n = len(core.rule_usage(self.cfg, saved["id"]))
        self._after_rule_change(rescan=True)
        self.toast.show_text(f"规则「{saved['name']}」已保存，{n} 款游戏按新规则重新检查")

    def duplicate_rule(self, rule_id: str) -> None:
        r = core.get_rule(self.cfg, rule_id)
        if not r:
            return
        new = dict(r)
        new["id"] = ""
        new["name"] = r["name"] + "（副本）"
        saved = core.save_rule(self.cfg, new)
        self._save_cfg()
        self._after_rule_change()
        self.toast.show_text(f"已复制为「{saved['name']}」，改好后给游戏换上")

    def delete_rule(self, rule_id: str) -> None:
        r = core.get_rule(self.cfg, rule_id)
        if not r:
            return
        users = core.rule_usage(self.cfg, rule_id)
        if users:
            self.toast.show_text(
                f"「{r['name']}」还有 {len(users)} 款游戏在用，先在下面改绑再删", ok=False)
            return
        if not core.delete_rule(self.cfg, rule_id):
            self.toast.show_text("删除失败：规则不存在", ok=False)
            return
        self._save_cfg()
        self._after_rule_change()
        self.toast.show_text(f"已删除规则「{r['name']}」")

    def set_game_rule(self, name: str, rule_id: str) -> None:
        """某款游戏换规则：保存后只重扫这一款。"""
        core.set_game_rule(self.cfg, name, rule_id)
        self._save_cfg()
        it = next((i for i in self.games if i.name == name), None)
        if it:
            self._sync_item_rule(it)
            self._rescan_one(it)
        if hasattr(self, "page_methods"):
            self.page_methods.refresh(self.cfg)
        self.toast.show_text(f"「{name}」改用规则「{core.rule_name_of(self.cfg, name)}」")

    def add_game(self) -> None:
        names = [g.name for g in core.all_games(self.cfg)]
        dlg = AddGameDialog(self, core.get_rules(self.cfg), names)
        if not dlg.exec():
            return
        v = dlg.value()
        if not core.add_game(self.cfg, v["name"], v["folder"], v["ext"],
                             v["success_message"], v["rule_id"]):
            self.toast.show_text("添加失败：游戏名或自动化程序目录为空", ok=False)
            return
        self._save_cfg()
        self._reload_games()
        self.toast.show_text(f"已添加「{v['name']}」并按所选规则检查了一次")

    def remove_game(self, name: str) -> None:
        if not core.remove_game(self.cfg, name):
            self.toast.show_text("内置游戏不能移除", ok=False)
            return
        self._save_cfg()
        self._reload_games()
        self.toast.show_text(f"已移除「{name}」")

    def edit_game(self, name: str) -> None:
        """已有游戏行的「编辑」：改它的自动化程序目录 / 完成提示 / 引用规则。"""
        g = core.game_config(name, self.cfg)
        rid = (core.rule_id_for(self.cfg, name)
               if core.is_game_bound(self.cfg, name) else "")
        editing = {
            "name": name,
            "folder": g.folder if g else "",
            "ext": (g.extension or "") if g else "",
            "success_message": (g.success_message or "") if g else "",
            "rule_id": rid,
        }
        dlg = AddGameDialog(self, core.get_rules(self.cfg),
                            [x.name for x in core.all_games(self.cfg)],
                            editing=editing)
        if not dlg.exec():
            return
        v = dlg.value()
        if not core.add_game(self.cfg, v["name"], v["folder"], v["ext"],
                             v["success_message"], v["rule_id"]):
            self.toast.show_text("保存失败：游戏名或自动化程序目录为空", ok=False)
            return
        if not v["rule_id"]:
            # 编辑时选「暂不绑定」= 清掉旧绑定，回到未设定（跟随默认规则）
            core.set_game_rule(self.cfg, v["name"], "")
        self._save_cfg()
        self._reload_games()
        self.toast.show_text(f"已更新「{v['name']}」并按所选规则检查了一次")

    def _sync_item_rule(self, it) -> None:
        """规则变了：把条目上的关键字 / 扩展名 / 规则说明同步过来。"""
        g = core.game_config(it.name, self.cfg)
        r = core.rule_of(self.cfg, it.name)
        it.target = core.rule_keyword(r, g)
        it.extension = core.rule_ext(r, g)
        it.rule = core.describe_rule(it.target, it.folder, it.extension,
                                     r.get("methods") or [core.DEFAULT_METHOD],
                                     r.get("file_target", ""), True,
                                     core.get_log_folders(self.cfg)
                                     if core.log_folder_check_enabled(self.cfg) else None)

    def _after_rule_change(self, rescan: bool = False) -> None:
        """规则库变动后刷新页面；rescan=True 时把引用它的游戏全部重扫。"""
        if rescan:
            for it in self.games:
                self._sync_item_rule(it)
                if not it.excluded:
                    self.engine.scan_item(it)
                self._refresh_game_row(it)
            self.page_home.refresh_stats()
            self._update_brand()
        if hasattr(self, "page_methods"):
            self.page_methods.refresh(self.cfg)

    def _reload_games(self) -> None:
        """游戏清单增删后重建条目：老游戏保留已有结果，新游戏立刻查一次。"""
        fresh = {g.name: g for g in self.engine.collect()}
        old = {g.name: g for g in getattr(self, "games", [])}
        games = []
        for name, it in fresh.items():
            prev = old.get(name)
            if prev is not None:
                prev.folder = it.folder
                prev.success_message = it.success_message
                prev.program_dir = it.program_dir
                prev.is_maawork = it.is_maawork
                prev.excluded = it.excluded
                self._sync_item_rule(prev)
                games.append(prev)
            else:
                self._sync_item_rule(it)
                games.append(it)
        self.games = games
        self.page_home.set_items(games)
        self.page_games.set_items(games)
        for it in games:
            if it.name not in old and not it.excluded:
                self.engine.scan_item(it)
                self._refresh_game_row(it)
        self.page_home.refresh_stats()
        self._update_brand()
        if hasattr(self, "page_settings"):
            self.page_settings.refresh_check()
        if hasattr(self, "page_methods"):
            self.page_methods.refresh(self.cfg)

    def _rescan_one(self, it) -> None:
        if not it.excluded:
            self.engine.scan_item(it)
            self._refresh_game_row(it)
            self.page_home.refresh_stats()
        if hasattr(self, "page_settings"):
            self.page_settings.refresh_check()

    def toggle_exclude(self, key: str) -> None:
        it = next((i for i in self.games if i.key == key), None)
        if not it:
            return
        excluded = core.get_excluded(self.cfg)
        if it.excluded or it.name in excluded:
            excluded = [x for x in excluded if x != it.name]
            it.excluded = False
            it.detail = ""
            self.toast.show_text(f"「{it.name}」已恢复检查")
        else:
            excluded.append(it.name)
            it.excluded = True
            it.detail = "已排除，不参与检查"
            self.toast.show_text(f"「{it.name}」已排除，不再参与检查")
        self.cfg.setdefault("check", {})["excluded"] = excluded
        self._save_cfg()
        self._refresh_game_row(it)
        self.page_home.refresh_stats()
        self._update_brand()
        if hasattr(self, "page_settings"):
            self.page_settings.refresh_check()

    def manage_excludes(self) -> None:
        """设置页「排除的游戏 · 管理」：勾选名单批量调整。"""
        names = [g.name for g in core.all_games(self.cfg)]
        dlg = ExcludeDialog(self, names, core.get_excluded(self.cfg))
        if not dlg.exec():
            return
        new = dlg.values()
        self.cfg.setdefault("check", {})["excluded"] = new
        self._save_cfg()
        for it in self.games:
            it.excluded = it.name in new
            it.detail = "已排除，不参与检查" if it.excluded else it.detail
            self._refresh_game_row(it)
        self.page_home.refresh_stats()
        self._update_brand()
        if hasattr(self, "page_settings"):
            self.page_settings.refresh_check()
        self.toast.show_text(f"排除名单已更新（{len(new)} 款）")

    def manage_folder_map(self) -> None:
        """判定规则页「文件夹部分映射 · 管理」：编辑完整映射表（含内置 13 条）。"""
        dlg = FolderMapDialog(self, core.get_folder_map(self.cfg))
        if not dlg.exec():
            return
        new = core.set_folder_map(self.cfg, dlg.values())
        self._save_cfg()
        # 映射影响异常监控展示名，立即重扫一遍异常文件夹
        if core.error_check_enabled(self.cfg) and core.get_target_folders(self.cfg):
            for it in self.games:
                it.error_flags.clear()
            self.engine.scan_errors(self.games)
        for it in self.games:
            self._refresh_game_row(it)
        self.page_home.refresh_stats()
        self._update_brand()
        if hasattr(self, "page_methods"):
            self.page_methods.refresh(self.cfg)
        self.toast.show_text(
            f"文件夹映射已保存（共 {len(new)} 条，内置条目可随时恢复）")

    def manage_scan_dirs(self) -> None:
        """设置页「扫描目录 · 管理」：勾选根目录、排除子目录。"""
        dlg = ScanDirDialog(self, self.cfg)
        if not dlg.exec():
            return
        v = dlg.values()
        dirs = core.set_scan_dirs(self.cfg, v["dirs"])
        excl = core.set_scan_excludes(self.cfg, v["excludes"])
        core.set_scan_depth(self.cfg, v["depth"])
        self._save_cfg()
        # 扫描范围变了，异常标记全部作废，重扫一遍
        for it in self.games:
            it.error_flags.clear()
        if core.error_check_enabled(self.cfg) and core.get_target_folders(self.cfg):
            self.engine.scan_errors(self.games)
        for it in self.games:
            self._refresh_game_row(it)
        self.page_home.refresh_stats()
        self._update_brand()
        if hasattr(self, "page_settings"):
            self.page_settings.refresh_check()
        tail = f"，排除 {len(excl)} 项" if excl else ""
        self.toast.show_text(f"扫描目录已保存：{len(dirs)} 个目录{tail}")

    def _refresh_game_row(self, it) -> None:
        home = getattr(self.page_home, "list", None)      # 总览页不一定有明细行
        if home is not None:
            row = home.rows.get(it.key)
            if row:
                row.refresh()
        grow = self.page_games.list.rows.get(it.key)
        if grow:
            grow.set_item(it)
            self.page_games.list.schedule_reorder()

    # ---------- 关注列表：已修复标记 / 排序 / 检查开关 ----------
    def toggle_fixed(self, name: str) -> None:
        """只是加/去掉一个标记，游戏仍留在关注列表里，由排序决定位置。"""
        on = not core.is_fixed(self.cfg, name)
        core.set_fixed(self.cfg, name, on)
        self._save_cfg()
        self.page_home.refresh_stats()
        self.toast.show_text(f"「{name}」已标记为已修复" if on
                             else f"「{name}」已取消已修复标记")

    def set_summary_sort(self, mode: str) -> None:
        self.cfg.setdefault("check", {})["summary_sort"] = mode
        self._save_cfg()
        self.page_home.set_summary_sort(mode)

    def set_manual_state(self, name: str, state: str) -> None:
        """总览右键手动设置状态：done / failed / forced=强制已完成 / 空=清除。

        所有记号都只有当天有效，次日 0 点后自动失效。"""
        core.set_manual_state(self.cfg, name, state or None)
        self._save_cfg()
        self.page_home.refresh_stats()
        # 标记变了，托盘角标与侧栏数字也要立刻跟着（不必等下一轮检查）
        if self.tray is not None:
            self.tray.set_tooltip(None, self._attention_count())
        self._update_brand()
        msg = {"done": f"「{name}」已设为已完成（次日 0 点自动失效）",
               "failed": f"「{name}」已设为未完成（次日 0 点自动失效）",
               "forced": f"「{name}」已强制完成，从关注列表排除（次日 0 点自动失效）"}.get(state)
        if msg is None:
            msg = f"「{name}」已清除手动状态，恢复跟随检查结果"
        self.toast.show_text(msg)

    def on_log_folders_changed(self) -> None:
        self._save_cfg()
        self._after_rule_change(rescan=False)
        self.toast.show_text("日志文件夹判定已保存，下次「开始检查」按新目录判定")

    def _sync_check_options(self) -> None:
        """检查参数开关变了：立刻按新开关重算存疑 / 天数，不用等下一轮扫描。"""
        if not hasattr(self, "page_games"):
            return
        opts = (core.log_threshold_check_enabled(self.cfg),
                core.streak_check_enabled(self.cfg),
                core.log_folder_check_enabled(self.cfg),
                tuple(core.get_log_folders(self.cfg)))
        if opts == getattr(self, "_last_check_opts", None):
            return
        self._last_check_opts = opts
        on = core.log_threshold_check_enabled(self.cfg)
        games = getattr(self, "games", None) or []
        for it in games:
            it.suspicious = bool(
                on and it.is_maawork and it.log_size > it.threshold_mb * 1024 * 1024
                and it.daily != "failed" and not it.has_error and not it.excluded)
            if not core.streak_check_enabled(self.cfg):
                it.streak = 0
            self._refresh_game_row(it)
        lr = getattr(self, "last_result", None)
        if lr:
            lr["suspicious"] = ({i.name: i.log_size for i in games if i.suspicious}
                                if on else {})
        self.page_home.refresh_stats()

    def _save_cfg(self) -> None:
        self.cfg.setdefault("ui", {})["preset"] = THEME.preset
        g = self.normalGeometry()
        self.cfg["ui"]["geometry"] = [g.x(), g.y(), g.width(), g.height()]
        self.cfg["ui"]["theme"] = THEME.mode
        self.cfg["ui"]["blur"] = THEME.blur
        core.save_config(self.cfg)
        self._apply_schedule()
        self._sync_default_threshold()
        self._sync_check_options()

    def _sync_default_threshold(self) -> None:
        """默认阈值改过之后，判定规则页里跟随默认的那些步进器要跟着变。"""
        cur = int((self.cfg.get("check") or {}).get(
            "threshold_mb", core.DEFAULT_LOG_THRESHOLD_MB))
        if cur == getattr(self, "_default_mb_seen", cur):
            return
        self._default_mb_seen = cur
        page = getattr(self, "page_methods", None)
        if page is not None:
            page.refresh(self.cfg)

    def closeEvent(self, e):
        bg = self.cfg.get("background", {})
        if (not self._quitting and bg.get("close_to_tray", True)
                and self.tray is not None):
            e.ignore()
            self.hide()
            return
        self._shutdown()
        super().closeEvent(e)


# ---------------------------------------------------------------- 入口

def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def main() -> int:
    # 一次性迁移旧版误用的开机自启注册表值名（升级用户无需手动处理）
    autostart.migrate_legacy_value()
    flags = {a.lower() for a in sys.argv[1:]}
    if "--help" in flags or "-h" in flags:
        print("用法: DailyChecker.exe [选项]\n"
              "  --check       启动后立即执行一轮检查\n"
              "  --notify      检查完成后发送钉钉通知（配合 --check）\n"
              "  --quit-after  检查/通知完成后自动退出（配合计划任务）\n"
              "  --hidden      后台静默启动，不显示主窗口\n"
              "示例: DailyChecker.exe --check --notify --quit-after --hidden")
        return 0
    start_hidden = any(a in ("--tray", "--silent", "--background", "--hidden", "-t")
                       for a in flags)
    cli = {"check": "--check" in flags,
           "notify": "--notify" in flags,
           "quit_after": "--quit-after" in flags}

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(core.APP_NAME)
    app.setApplicationDisplayName(core.APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(13)
    app.setFont(f)

    # Windows 通知需要显式的 AppUserModelID + 注册表显示名
    try:
        aumid = f"{core.APP_ID}.Tray"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aumid)
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{aumid}",
            0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY)
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, core.APP_NAME)
        ico = resource_path("assets/icon.ico")
        if os.path.exists(ico):
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, os.path.abspath(ico))
        winreg.CloseKey(key)
    except Exception:
        pass

    icon_file = resource_path("assets/icon.ico")
    if os.path.exists(icon_file):
        app.setWindowIcon(QIcon(icon_file))

    # ---------- 单实例 ----------
    ipc_key = core.APP_ID + ".single"
    probe = QLocalSocket()
    probe.connectToServer(ipc_key)
    if probe.waitForConnected(400):
        # 把命令行参数交给已经在跑的实例，否则 --check/--notify/--quit-after
        # 在常驻托盘时会被直接丢掉，计划任务等于什么都没做
        payload = {"show": not start_hidden, "hidden": start_hidden, "cli": cli}
        try:
            probe.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            probe.flush()
            probe.waitForBytesWritten(400)
        except Exception:
            pass
        probe.disconnectFromServer()
        return 0

    QLocalServer.removeServer(ipc_key)
    server = QLocalServer()
    server.listen(ipc_key)

    win = MainWindow(start_hidden=start_hidden, cli=cli)

    def _dispatch(payload: dict) -> None:
        p = payload or {}
        c = dict(p.get("cli") or {})
        c.pop("quit_after", None)       # 二次实例不能让常驻托盘跟着退出
        if c.get("check") or c.get("notify"):
            # 只对这一轮检查生效，不改变常驻实例自己的 CLI 状态
            win._cli_override = c
            QTimer.singleShot(0, lambda: win.start_check(silent=True)
                              if c.get("check") else win.send_notify())
            return
        if p.get("show") or not p.get("hidden"):
            win.show_main()

    def _on_second_instance() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            state = {"buf": b"", "done": False}

            def _read(c=conn, st=state):
                st["buf"] += bytes(c.readAll())
                if st["done"]:
                    return
                try:
                    payload = json.loads(st["buf"].decode("utf-8"))
                except Exception:
                    return                  # 分包没收齐，等下一次 readyRead
                st["done"] = True
                _dispatch(payload)

            def _on_disconnect(c=conn, st=state):
                # 兼容旧版只发 b"show" 的客户端
                if not st["done"] and st["buf"].strip() in (b"show", b""):
                    win.show_main()
                c.deleteLater()

            conn.readyRead.connect(_read)
            conn.disconnected.connect(_on_disconnect)

    server.newConnection.connect(_on_second_instance)

    if not start_hidden:
        win.show()
    return app.exec()


def _excepthook(exc_type, exc, tb):
    """Qt 事件循环里的异常不会冒到 main()，必须挂 excepthook 才能留下日志。"""
    import traceback
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        with open(os.path.join(core.config_dir(), "crash.log"), "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{text}")
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)


if __name__ == "__main__":
    sys.excepthook = _excepthook
    try:
        sys.exit(main())
    except Exception:
        import traceback
        try:
            log_path = os.path.join(core.config_dir(), "crash.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        raise
