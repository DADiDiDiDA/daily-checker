# -*- coding: utf-8 -*-
"""页面：总览仪表盘、设置。"""

from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame,
                               QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QSpacerItem, QStackedWidget,
                               QVBoxLayout, QWidget)

from backdrop import BACKDROP, paint_glass
from theme import (GLYPH, PRESET_HINTS, PRESET_LABELS, PRESET_ORDER, THEME, flat, glyph_font,
                   qc, rgba, scoped, tint)
from widgets import (Card, Chip, GameRow, Glyph, NumberStepper,
                     ProgressLine, SearchBox, SegmentedControl, SettingGroup, SettingRow,
                     StatTile, SuffixMultiSelect, TabButton, ThemedCombo, ToggleSwitch,
                     WrapLabel)

import autostart
import core


def _label(text: str, size: int = 13, color_key: str = "text", bold: bool = False,
           wrap: bool = False) -> QLabel:
    p = THEME.p
    # 需要换行的用 WrapLabel：布局会按实际宽度把高度撑开，避免折行处被裁掉
    lbl = WrapLabel(text) if wrap else QLabel(text)
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(size)
    if bold:
        f.setWeight(QFont.DemiBold)
    lbl.setFont(f)
    if not wrap:
        lbl.setWordWrap(False)
    # 记下用的是哪个调色板键，主题切换时 refresh_theme 会据此重刷文字色
    lbl.setProperty("_fg_key", color_key)
    tint(lbl, getattr(p, color_key))
    return lbl


class SecretEdit(QWidget):
    """密码式输入框 + 「显示/隐藏」切换按钮。

    Webhook 的 access_token 与加签密钥都是敏感凭证，默认打码；点「显示」临时明文。
    控件对外暴露 ``edit``（内部 QLineEdit），并转发 editingFinished / text / setText。
    """

    def __init__(self, text: str = "", placeholder: str = "", width: int = 330, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.edit = QLineEdit(text)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setMinimumWidth(width)
        lay.addWidget(self.edit, 1)
        self._btn = QPushButton("显示")
        self._btn.setObjectName("link")
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setFixedHeight(28)
        self._btn.clicked.connect(self._toggle)
        lay.addWidget(self._btn, 0, Qt.AlignVCenter)

    def _toggle(self) -> None:
        show = self.edit.echoMode() == QLineEdit.Password
        self.edit.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)
        self._btn.setText("隐藏" if show else "显示")

    @property
    def editingFinished(self):
        return self.edit.editingFinished

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:
        self.edit.setText(text)


class GameListView(QWidget):
    """可筛选、可排序、可拖拽的游戏列表。"""

    row_open = Signal(str)
    row_log = Signal(str)
    row_debug = Signal(str)
    row_threshold = Signal(str)
    row_method = Signal(str)
    row_exclude = Signal(str)
    sort_changed = Signal(str, str)   # (常规排序, 特殊排序)
    order_changed = Signal(list)   # 手动拖拽后的新顺序（游戏名列表）

    FILTERS = [("all", "全部"), ("error", "异常"), ("failed", "未完成"),
               ("susp", "存疑"), ("excluded", "已排除"), ("ok", "正常")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.rows: dict[str, GameRow] = {}
        self.filter = "all"
        self.keyword = ""
        # 检查结果是一条条回来的，每来一条就搬一次行会晃眼；
        # 攒 220ms 再重排一次，保证排序是「活」的，又不会跳个不停。
        self._reorder_timer = QTimer(self)
        self._reorder_timer.setSingleShot(True)
        self._reorder_timer.setInterval(220)
        self._reorder_timer.timeout.connect(self._flush_reorder)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.chips: dict[str, Chip] = {}
        for key, text in self.FILTERS:
            chip = Chip(text, 0)
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            self.chips[key] = chip
            bar.addWidget(chip)
        bar.addStretch(1)
        self.cfg = core.load_config()
        self.sort_combo = ThemedCombo([(k, t) for k, t in core.SORT_MODES_BASIC],
                                      core.get_sort_mode(self.cfg), 116)
        self.sort_combo.changed.connect(self._on_sort_changed)
        self.sort_combo.setToolTip("常规排序：默认顺序 / 按名称 / 无错天数 / 日志大小")
        bar.addWidget(self.sort_combo)
        self.special_combo = ThemedCombo([(k, t) for k, t in core.SORT_MODES_SPECIAL],
                                         core.get_sort_special(self.cfg), 108)
        self.special_combo.changed.connect(self._on_sort_changed)
        self.special_combo.setToolTip(
            "特殊排序：启用时覆盖常规排序；「手动拖拽」下可按住行拖动调整顺序")
        bar.addWidget(self.special_combo)
        self.search = SearchBox("搜索游戏…")
        self.search.setFixedWidth(184)
        self.search.textChanged.connect(self._on_search)
        bar.addWidget(self.search)
        outer.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.list_lay = QVBoxLayout(holder)
        self.list_lay.setContentsMargins(0, 0, 6, 4)
        self.list_lay.setSpacing(8)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(holder)
        outer.addWidget(self.scroll, 1)

        self.empty = QLabel("还没有检查结果，点「开始检查」跑一轮")
        self.empty.setAlignment(Qt.AlignCenter)
        tint(self.empty, THEME.p.text_muted)
        self.empty.setVisible(False)
        outer.addWidget(self.empty)

        self.set_filter("all")

    # ---------- 数据 ----------
    def set_items(self, items) -> None:
        self.items = list(items)
        self.cfg = core.load_config()   # 手动状态 / 排序设置可能刚变过，重读一次
        self._sorted_items = core.sort_items(self.items, self._effective_mode(),
                                             core.get_manual_order(self.cfg),
                                             cfg=self.cfg)
        self._apply_manual_drag()
        for row in self.rows.values():
            # 只 hide + deleteLater，绝不能 setParent(None)：
            # 控件脱离父级会变成独立顶层窗口（带标题栏的小窗闪一下），
            # 检查过程中反复重建就会疯狂闪。
            row.hide()
            self.list_lay.removeWidget(row)
            row.deleteLater()
        self.rows.clear()
        for it in self._sorted_items:
            row = GameRow(it)
            row.open_requested.connect(self.row_open)
            row.log_requested.connect(self.row_log)
            row.debug_requested.connect(self.row_debug)
            row.threshold_requested.connect(self.row_threshold)
            row.method_requested.connect(self.row_method)
            row.exclude_requested.connect(self.row_exclude)
            row.dropped_on.connect(self._on_drop)
            self.rows[it.key] = row
            self.list_lay.insertWidget(self.list_lay.count() - 1, row)
        self._update_counts()
        self.apply_filter()

    # ---------- 排序与拖拽 ----------
    def _effective_mode(self) -> str:
        sp = self.special_combo.value()
        return sp if sp != "none" else self.sort_combo.value()

    def _on_sort_changed(self, *_):
        self.sort_changed.emit(self.sort_combo.value(), self.special_combo.value())
        self.reorder()

    def schedule_reorder(self) -> None:
        """结果陆续到达时延迟重排（手动拖拽模式不动，避免打断用户）。"""
        if self._effective_mode() == "manual":
            return
        self._reorder_timer.start()

    def _flush_reorder(self) -> None:
        self._reorder_timer.stop()
        self.reorder()

    def refresh_order(self) -> None:
        """立刻按当前排序方式重排（检查结束 / 排序方式变更时调用）。"""
        self._flush_reorder()

    def reorder(self, mode: str | None = None) -> None:
        """按排序方式重排行控件；mode 为空时用当前两个下拉框的实际生效值。"""
        mode = mode or self._effective_mode()
        if mode == "manual":
            # 保留当前视觉顺序（可能已被拖拽过）
            new = [self.rows[r.item.key].item for r in self._rows_in_order()]
        else:
            self.cfg = core.load_config()
            new = core.sort_items(self.items, mode,
                                  core.get_manual_order(self.cfg), cfg=self.cfg)
        self._apply_manual_drag()        # 拖拽开关跟排序方式走，顺序没变也要切
        if [it.key for it in new] == [it.key for it in getattr(self, "_sorted_items", [])]:
            return                       # 顺序没变就别动控件，省得无谓重绘
        self._sorted_items = new
        self._reinsert_rows()

    def _rows_in_order(self) -> list:
        out = []
        for i in range(self.list_lay.count() - 1):    # 最后一个是 stretch
            w = self.list_lay.itemAt(i).widget()
            if isinstance(w, GameRow):
                out.append(w)
        return out

    def _reinsert_rows(self) -> None:
        for row in self._rows_in_order():
            self.list_lay.removeWidget(row)
        for row in self._sorted_items:
            r = self.rows.get(row.key)
            if r:
                self.list_lay.insertWidget(self.list_lay.count() - 1, r)
        self.apply_filter()

    def _apply_manual_drag(self) -> None:
        on = self._effective_mode() == "manual"
        for row in self.rows.values():
            row.set_drag_enabled(on)

    def _on_drop(self, dragged: str, target: str, before: bool) -> None:
        names = [it.name for it in self._sorted_items]
        if dragged not in names or target not in names or dragged == target:
            return
        items_by_name = {it.name: it for it in self._sorted_items}
        names.remove(dragged)
        pos = names.index(target) + (0 if before else 1)
        names.insert(pos, dragged)
        self._sorted_items = [items_by_name[n] for n in names]
        self._reinsert_rows()
        self.order_changed.emit(names)

    def update_item(self, item) -> None:
        for i, old in enumerate(self.items):
            if old.key == item.key:
                self.items[i] = item
                break
        for i, old in enumerate(getattr(self, "_sorted_items", [])):
            if old.key == item.key:
                self._sorted_items[i] = item
                break
        row = self.rows.get(item.key)
        if row:
            row.set_item(item)
        self._update_counts()
        self.apply_filter()
        self.schedule_reorder()      # 状态变了排序也可能变（未完成优先 / 异常优先）

    def _update_counts(self) -> None:
        counts = {
            "all": len(self.items),
            "error": sum(1 for i in self.items if i.has_error),
            "failed": sum(1 for i in self.items if core.effective_failed(self.cfg, i)),
            "susp": sum(1 for i in self.items if i.suspicious and not i.excluded),
            "excluded": sum(1 for i in self.items if i.excluded),
            "ok": sum(1 for i in self.items if i.scanned and i.daily == "done"
                      and not i.has_error and not i.excluded),
        }
        for k, chip in self.chips.items():
            chip.set_count(counts.get(k, 0))

    def _on_search(self, text: str) -> None:
        self.keyword = text.strip().lower()
        self.apply_filter()

    def set_filter(self, key: str) -> None:
        self.filter = key
        for k, chip in self.chips.items():
            chip.set_active(k == key)
        self.apply_filter()

    def apply_filter(self) -> None:
        visible = 0
        for it in self.items:
            row = self.rows.get(it.key)
            if not row:
                continue
            ok = True
            if self.filter == "error":
                ok = it.has_error
            elif self.filter == "failed":
                ok = core.effective_failed(self.cfg, it)
            elif self.filter == "susp":
                ok = it.suspicious and not it.excluded
            elif self.filter == "excluded":
                ok = it.excluded
            elif self.filter == "ok":
                ok = it.scanned and it.daily == "done" and not it.has_error and not it.excluded
            if ok and self.keyword:
                ok = self.keyword in it.name.lower()
            row.setVisible(ok)
            visible += 1 if ok else 0
        # 还没检查过（items 为空）时也要给提示，否则启动后整页空白
        self.empty.setVisible(visible == 0)
        if visible == 0:
            self.empty.setText("没有符合条件的游戏" if self.items
                               else "还没有检查结果，点「开始检查」跑一轮")
        self.scroll.setVisible(visible > 0)

    def refresh_theme(self) -> None:
        for row in self.rows.values():
            row.refresh()
        for chip in self.chips.values():
            chip.set_active(chip is self.chips.get(self.filter))
            chip._apply()
        self.search.refresh_theme()
        self.sort_combo._restyle()
        self.special_combo._restyle()
        tint(self.empty, THEME.p.text_muted)


class GamesPage(QWidget):
    """独立页：筛选 / 常规+特殊排序 / 游戏列表。"""

    row_open = Signal(str)
    row_log = Signal(str)
    row_debug = Signal(str)
    row_threshold = Signal(str)
    row_method = Signal(str)
    row_exclude = Signal(str)
    sort_changed = Signal(str, str)
    order_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.list = GameListView()
        self.list.row_open.connect(self.row_open)
        self.list.row_log.connect(self.row_log)
        self.list.row_debug.connect(self.row_debug)
        self.list.row_threshold.connect(self.row_threshold)
        self.list.row_method.connect(self.row_method)
        self.list.row_exclude.connect(self.row_exclude)
        self.list.sort_changed.connect(self.sort_changed)
        self.list.order_changed.connect(self.order_changed)
        lay.addWidget(self.list, 1)

    def set_items(self, items) -> None:
        self.list.set_items(items)

    def update_item(self, item) -> None:
        self.list.update_item(item)

    def refresh_order(self) -> None:
        self.list.refresh_order()

    def refresh_theme(self) -> None:
        self.list.refresh_theme()


def _edit(ph: str, text: str = "") -> QLineEdit:
    """统一样式的单行输入框。"""
    p = THEME.p
    e = QLineEdit(text)
    e.setPlaceholderText(ph)
    e.setFixedHeight(32)
    e.setStyleSheet(
        f"QLineEdit{{background:{rgba(p.text, 0.06)};border:1px solid {rgba(p.border, 0.15)};"
        f"border-radius:8px;color:{p.text};font-size:12px;padding:0 10px;}}")
    return e


class RuleRow(QWidget):
    """规则库里的一条规则：名称 + 判定方式 + 引用数 + 规则全文 + 编辑/复制/删除。

    可设为「默认规则」：程序检查规则里没显式绑定规则的游戏都用它。"""

    edit_requested = Signal(str)
    duplicate_requested = Signal(str)
    delete_requested = Signal(str)
    default_requested = Signal(str)     # 设为默认 / 再点一次取消默认

    def __init__(self, rule: dict, users: list, is_default: bool = False, parent=None):
        super().__init__(parent)
        self.rule = rule
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        name = _label(rule["name"], 13, "text", bold=True)
        if rule.get("note"):
            name.setToolTip(rule["note"])
        head.addWidget(name)
        if is_default:
            star = _label("默认规则", 10, "accent")
            star.setToolTip("未显式绑定规则的游戏都引用这条；再点一次「取消默认」恢复")
            head.addWidget(star)
        kinds = "、".join(core.METHOD_SHORT.get(m, m) for m in rule["methods"])
        head.addWidget(_label(kinds, 10, "accent"))
        head.addWidget(_label(f"{len(users)} 款在用" if users else "未被引用", 10,
                              "text_muted" if users else "warning"))
        head.addStretch(1)
        default_btn = QPushButton("取消默认" if is_default else "设为默认")
        default_btn.setObjectName("ghost")
        default_btn.setCursor(Qt.PointingHandCursor)
        default_btn.setFixedHeight(26)
        default_btn.setToolTip(
            "设为默认规则后，程序检查规则里没显式绑定规则的游戏都用这条"
            if not is_default else "取消后回到原来的默认判断方式")
        default_btn.clicked.connect(
            lambda: self.default_requested.emit(self.rule["id"]))
        head.addWidget(default_btn)
        for text, sig, tip in (
                ("编辑", self.edit_requested, "改这条规则，引用它的游戏全部跟着变"),
                ("复制", self.duplicate_requested, "复制一份再改，做成某款游戏专用"),
                ("删除", self.delete_requested, "还有游戏在引用时会拒绝删除")):
            b = QPushButton(text)
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(26)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, s=sig: s.emit(self.rule["id"]))
            head.addWidget(b)
        col.addLayout(head)

        col.addWidget(_label("匹配规则：" + core.rule_text_bare(rule), 10,
                             "text_muted", wrap=True))
        if users:
            lbl = _label("引用游戏：" + "、".join(users), 10, "text_muted", wrap=True)
            col.addWidget(lbl)


class GameRuleRow(QWidget):
    """一款游戏 + 它引用的规则（下拉可改）+ 自己的日志阈值。"""

    rule_changed = Signal(str, str)
    remove_requested = Signal(str)
    edit_requested = Signal(str)
    threshold_changed = Signal(str, int)
    threshold_enabled_changed = Signal(str, bool)

    NAME_W = 112

    def __init__(self, cfg: dict, game, rules: list, current_id: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.game = game
        self._building = True
        self._default_mb = int((cfg.get("check") or {}).get(
            "threshold_mb", core.DEFAULT_LOG_THRESHOLD_MB))

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        l1 = QHBoxLayout()
        l1.setSpacing(8)
        self.name_lbl = _label(game.name, 13, "text", bold=True)
        self.name_lbl.setFixedWidth(self.NAME_W)
        self.name_lbl.setToolTip(f"自动化程序目录：{game.folder}")
        l1.addWidget(self.name_lbl)

        items = [(r["id"], r["name"]) for r in rules]
        dr = core.get_default_rule(cfg)
        dr_name = next((r["name"] for r in rules if r["id"] == dr), "")
        items.append(("", f"未设定（用默认规则：{dr_name}）" if dr_name
                      else "未设定（沿用默认判断方式）"))
        self.combo = ThemedCombo(items, current_id, 280)
        self.combo.changed.connect(self._on_change)
        l1.addWidget(self.combo)
        l1.addStretch(1)

        # ---- 日志阈值：分项子开关 + 数值，改完即时生效 ----
        self.thr_on = ToggleSwitch(core.threshold_check_enabled(cfg, game.name))
        self.thr_on.setToolTip(
            "关闭后这款游戏不参与日志体积「存疑」判定（其它游戏不受影响；"
            "总开关在页顶部）")
        self.thr_on.toggled.connect(self._on_thr_enabled)
        l1.addWidget(self.thr_on)
        self.thr_lbl = _label("日志阈值", 10, "text_muted")
        self.thr_lbl.setToolTip(
            f"自动化程序 debug 日志文件夹顶层 .log 合计超过该值标记为「存疑」；\n"
            f"默认 {self._default_mb}MB，调成默认值就回到跟随默认")
        l1.addWidget(self.thr_lbl)
        self.thr = NumberStepper(core.get_threshold_mb(cfg, game.name), 1, 500, 1,
                                 " MB", 104)
        self.thr.valueChanged.connect(self._on_threshold)
        l1.addWidget(self.thr)
        self._thr_on = self.thr_on.isChecked()
        self.thr.setEnabled(self._thr_on)
        self.thr_lbl.setEnabled(self._thr_on)

        self.custom = not any(g.name == game.name for g in core.DAILY_GAMES)
        edit_btn = QPushButton("编辑")
        edit_btn.setObjectName("ghost")
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.setFixedHeight(28)
        edit_btn.setToolTip("修改这款游戏的自动化程序目录 / 完成提示 / 引用规则")
        edit_btn.clicked.connect(lambda: self.edit_requested.emit(self.game.name))
        l1.addWidget(edit_btn)
        if self.custom:
            b = QPushButton("移除")
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(28)
            b.setToolTip("从游戏清单里移除这款自定义游戏")
            b.clicked.connect(lambda: self.remove_requested.emit(self.game.name))
            l1.addWidget(b)
        col.addLayout(l1)

        self.rule_lbl = _label("", 10, "text_muted", wrap=True)
        self.rule_lbl.setContentsMargins(self.NAME_W + 8, 0, 0, 0)
        col.addWidget(self.rule_lbl)
        self._update_text()
        self._building = False

    def _update_text(self) -> None:
        r = core.rule_of(self.cfg, self.game.name)
        self.rule_lbl.setText("匹配规则：" + core.rule_text(
            self.cfg, self.game, r, with_folder=False))
        self.rule_lbl.setToolTip(f"自动化程序目录：{self.game.folder}\n"
                                 + core.rule_text(self.cfg, self.game, r))

    def _on_change(self, rid: str) -> None:
        if self._building:
            return
        self.rule_changed.emit(self.game.name, rid)

    def _on_threshold(self, mb: int) -> None:
        if self._building:
            return
        self.threshold_changed.emit(self.game.name, int(mb))

    def _on_thr_enabled(self, on: bool) -> None:
        self.thr.setEnabled(on)
        self.thr_lbl.setEnabled(on)
        if self._building:
            return
        self.threshold_enabled_changed.emit(self.game.name, bool(on))

    def refresh_theme(self) -> None:
        self.combo._restyle()


def _page_scroll() -> QScrollArea:
    """独立子页统一的滚动外壳。"""
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.NoFrame)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    # 只让视口自身透明；不要用无选择器的 setStyleSheet，那会把
    # "background: transparent" 级联给所有子控件，把按钮底色一起抹掉
    sc.viewport().setStyleSheet("QScrollArea > QWidget { background: transparent; }")
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(0, 0, 6, 8)
    lay.setSpacing(12)
    sc.setWidget(holder)
    sc.lay = lay
    return sc


def _clear_layout(lay: QVBoxLayout) -> None:
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.deleteLater()


def _pill(text: str, color_key: str = "text") -> QLabel:
    """只读的小胶囊（内置映射条目用）。"""
    p = THEME.p
    lbl = _label(text, 11, color_key)
    scoped(lbl, f"background: {rgba(p.text, 0.055)}; border-radius: 8px;"
                f"padding: 4px 10px;")
    lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return lbl


class RulesView(QWidget):
    """判定规则 · 规则库（独立子页）。"""

    new_rule_requested = Signal()
    edit_rule_requested = Signal(str)
    duplicate_rule_requested = Signal(str)
    delete_rule_requested = Signal(str)
    set_default_requested = Signal(str)   # 设为/取消默认规则

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = _page_scroll()
        body = scroll.lay
        outer.addWidget(scroll)

        card = Card(padding=16)
        card.lay.setContentsMargins(16, 4, 16, 16)   # 顶部留白收窄，让「规则库」标题上移
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = _label("规则库", 15, "text", bold=True)
        head.addWidget(self.title)
        self.count = _label("", 11, "text_muted")
        head.addWidget(self.count)
        head.addStretch(1)
        new_rule_btn = QPushButton("新建规则…")
        new_rule_btn.setObjectName("primary")
        new_rule_btn.setCursor(Qt.PointingHandCursor)
        new_rule_btn.setFixedHeight(30)
        new_rule_btn.setToolTip("建一条新规则，之后可以给任何游戏引用")
        new_rule_btn.clicked.connect(self.new_rule_requested.emit)
        head.addWidget(new_rule_btn)
        card.lay.addLayout(head)
        card.lay.addWidget(_label(
            "规则只描述「怎么算日常完成」；一条规则可以被多款游戏共用，改一次全部生效。",
            11, "text_muted", wrap=True))
        body.addWidget(card)

        holder = QWidget()
        self.rows_lay = QVBoxLayout(holder)
        self.rows_lay.setContentsMargins(2, 0, 2, 0)
        self.rows_lay.setSpacing(12)
        body.addWidget(holder)
        body.addStretch(1)

        self.refresh(cfg)

    def refresh(self, cfg: dict | None = None) -> None:
        if cfg is not None:
            self.cfg = cfg
        rules = core.get_rules(self.cfg)
        default_id = core.get_default_rule(self.cfg)
        _clear_layout(self.rows_lay)
        for r in rules:
            row = RuleRow(r, core.rule_usage(self.cfg, r["id"]),
                          is_default=(r["id"] == default_id))
            row.edit_requested.connect(self.edit_rule_requested)
            row.duplicate_requested.connect(self.duplicate_rule_requested)
            row.delete_requested.connect(self.delete_rule_requested)
            row.default_requested.connect(self.set_default_requested)
            self.rows_lay.addWidget(row)
        self.count.setText(f"{len(rules)} 条" + (" · 已设默认规则" if default_id else ""))


class GameRulesView(QWidget):
    """判定规则 · 程序检查规则（独立子页）。"""

    add_game_requested = Signal()
    game_rule_changed = Signal(str, str)
    game_remove_requested = Signal(str)
    game_threshold_changed = Signal(str, int)
    game_threshold_enabled_changed = Signal(str, bool)
    game_edit_requested = Signal(str)  # 编辑已有游戏（改自动化程序目录等）
    config_changed = Signal()          # 日志阈值总开关 / 默认阈值变了

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = _page_scroll()
        body = scroll.lay
        outer.addWidget(scroll)

        card = Card(padding=16)
        card.lay.setContentsMargins(16, 4, 16, 16)   # 黑字标题上移（同规则库页）
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = _label("程序检查规则", 15, "text", bold=True)
        head.addWidget(self.title)
        self.count = _label("", 11, "text_muted")
        head.addWidget(self.count)
        head.addStretch(1)
        add_game_btn = QPushButton("添加游戏…")
        add_game_btn.setObjectName("soft")
        add_game_btn.setCursor(Qt.PointingHandCursor)
        add_game_btn.setFixedHeight(30)
        add_game_btn.setToolTip("添加一款新游戏：填自动化程序目录，再从规则库里选一条规则")
        add_game_btn.clicked.connect(self.add_game_requested.emit)
        head.addWidget(add_game_btn)
        card.lay.addLayout(head)
        card.lay.addWidget(_label(
            "每行是一款游戏和它引用的规则；改下拉即时换规则，并只重新检查这一款。"
            "没在下拉里选过规则的「未设定」游戏排最前，先用规则库里标的默认规则；"
            "选一条规则（选默认规则也算）后就固定用它、不再置顶。"
            "下方开关控制日志阈值检查是否启用；超过阈值的自动化程序日志会列入关注列表，"
            "每行可以单独改自己的上限。",
            11, "text_muted", wrap=True))

        # 日志阈值总开关 + 默认阈值：和下面每行的阈值放同一页（从设置页搬来）
        opt = QWidget()
        flat(opt)
        ol = QHBoxLayout(opt)
        ol.setContentsMargins(0, 4, 0, 0)
        ol.setSpacing(10)
        ol.addStretch(1)
        self.log_thr_toggle = ToggleSwitch(core.log_threshold_check_enabled(self.cfg))
        self.log_thr_toggle.setToolTip(
            "开启时超过阈值的自动化程序日志会列入关注列表；"
            "关闭后不再按日志体积判定「存疑」，已有阈值设置保留")
        self.log_thr_toggle.toggled.connect(self._save_check_opts)
        ol.addWidget(self.log_thr_toggle)
        ol.addWidget(_label("启用日志阈值检查", 11, "text"))
        ol.addSpacing(16)
        ol.addWidget(_label("默认日志阈值", 11, "text_muted"))
        self.thr_spin = NumberStepper(
            int((self.cfg.get("check") or {}).get(
                "threshold_mb", core.DEFAULT_LOG_THRESHOLD_MB)),
            1, 500, 1, " MB", 108)
        self.thr_spin.valueChanged.connect(self._save_check_opts)
        self.thr_spin.setToolTip("调成默认值后，各游戏行会回到「跟随默认」")
        ol.addWidget(self.thr_spin)
        card.lay.addWidget(opt)

        body.addWidget(card)

        holder = QWidget()
        self.rows_lay = QVBoxLayout(holder)
        self.rows_lay.setContentsMargins(2, 0, 2, 0)
        self.rows_lay.setSpacing(10)
        body.addWidget(holder)
        body.addStretch(1)

        self.refresh(cfg)

    def _sync_opts(self) -> None:
        """外部改了配置（例如右键改默认阈值）后同步顶部控件，不要触发保存信号。"""
        self.log_thr_toggle.blockSignals(True)
        self.log_thr_toggle.setChecked(core.log_threshold_check_enabled(self.cfg))
        self.log_thr_toggle.blockSignals(False)
        self.thr_spin.blockSignals(True)
        self.thr_spin.setValue(int((self.cfg.get("check") or {}).get(
            "threshold_mb", core.DEFAULT_LOG_THRESHOLD_MB)))
        self.thr_spin.blockSignals(False)
        self.thr_spin.setEnabled(self.log_thr_toggle.isChecked())

    def _save_check_opts(self, *_):
        ck = self.cfg.setdefault("check", {})
        ck["log_threshold_check"] = self.log_thr_toggle.isChecked()
        ck["threshold_mb"] = int(self.thr_spin.value())
        self.thr_spin.setEnabled(self.log_thr_toggle.isChecked())
        self.config_changed.emit()

    def refresh(self, cfg: dict | None = None) -> None:
        if cfg is not None:
            self.cfg = cfg
        self._sync_opts()
        rules = core.get_rules(self.cfg)
        games = core.all_games(self.cfg)
        # 未设定（没在下拉里选过规则）的排最前，提示用户去设；
        # 这些游戏先用规则库里标的默认规则，选过（选默认规则也算）就固定用它
        unbound = [g for g in games if not core.is_game_bound(self.cfg, g.name)]
        bound = [g for g in games if core.is_game_bound(self.cfg, g.name)]
        _clear_layout(self.rows_lay)
        for g in unbound + bound:
            # 未设定的游戏下拉选「未设定」项，不要预选默认规则——
            # 否则用户再选一次默认规则时不会触发 changed，永远留在置顶区
            cur = core.rule_id_for(self.cfg, g.name) if g in bound else ""
            row = GameRuleRow(self.cfg, g, rules, cur)
            row.rule_changed.connect(self.game_rule_changed)
            row.remove_requested.connect(self.game_remove_requested)
            row.threshold_changed.connect(self.game_threshold_changed)
            row.threshold_enabled_changed.connect(self.game_threshold_enabled_changed)
            row.edit_requested.connect(self.game_edit_requested)
            self.rows_lay.addWidget(row)
        n_custom = sum(1 for g in games if g not in core.DAILY_GAMES)
        self.count.setText(
            f"{len(games)} 款"
            + (f"（含 {n_custom} 款自定义）" if n_custom else "")
            + (f" · {len(unbound)} 款未设定" if unbound else ""))


class FolderMapView(QWidget):
    """判定规则 · 文件夹关键词映射（独立子页）。"""

    folder_map_manage_requested = Signal()
    log_folders_changed = Signal()

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = _page_scroll()
        body = scroll.lay
        outer.addWidget(scroll)

        btn = QPushButton("管理…")
        btn.setObjectName("ghost")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(30)
        btn.setToolTip("编辑 / 删除任意一条映射（含内置 20 条），也能一键恢复内置")
        btn.clicked.connect(self.folder_map_manage_requested.emit)
        self.row = SettingRow("folder", "文件夹部分映射", "", btn)
        body.addWidget(self.row)

        # 日志文件夹判定：只在程序根目录下这几个文件夹里查关键字 / 目标文件
        lf_box = QWidget()
        flat(lf_box)
        lfl = QVBoxLayout(lf_box)
        # 与卡片标题文字左对齐（图标 26px + 间距 13px 之后）
        lfl.setContentsMargins(39, 0, 0, 0)
        lfl.setSpacing(8)
        lf_line = QHBoxLayout()
        lf_line.setSpacing(10)
        self.lf_toggle = ToggleSwitch(core.log_folder_check_enabled(cfg))
        self.lf_toggle.toggled.connect(self._save_log_folders)
        self.lf_edit = QLineEdit("，".join(core.get_log_folders(cfg)))
        self.lf_edit.setPlaceholderText("logs，log，debug，.log")
        self.lf_edit.setMinimumHeight(30)
        self.lf_edit.editingFinished.connect(self._save_log_folders)
        lf_line.addWidget(self.lf_toggle)
        lf_line.addWidget(self.lf_edit, 1)
        lfl.addLayout(lf_line)
        lfl.addWidget(_label(
            "相对每款游戏的「自动化程序目录」，只填文件夹名（逗号分隔）；"
            "每个文件夹只读顶层文件，不递归子目录。规则库不再单独指定目录，统一跟随这里。",
            11, "text_muted", wrap=True))
        self.lf_row = SettingRow(
            "folder", "日志文件夹判定",
            "启用后只在这些自动化程序日志文件夹里找关键字 / 检查目标文件，"
            "不扫描程序的其它文件夹",
            None, lf_box)
        body.addWidget(self.lf_row)

        self.map_title = _label("", 13, "text", bold=True)
        body.addWidget(self.map_title)
        map_box = QWidget()
        flat(map_box)
        self.map_grid = QGridLayout(map_box)
        self.map_grid.setContentsMargins(2, 0, 2, 0)
        self.map_grid.setHorizontalSpacing(8)
        self.map_grid.setVerticalSpacing(8)
        body.addWidget(map_box)
        body.addStretch(1)

        self.refresh(cfg)

    def _fill(self, grid: QGridLayout, items: list) -> None:
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        for i, text in enumerate(items):
            b = QPushButton(text)
            b.setObjectName("soft")
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(28)
            b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            b.setStyleSheet(
                f"QPushButton{{text-align:left;padding:4px 10px;border-radius:8px;"
                f"background:{rgba(THEME.p.text, 0.055)};color:{THEME.p.text};font-size:11px;}}"
                f"QPushButton:hover{{background:{rgba(THEME.p.text, 0.1)};}}")
            b.clicked.connect(lambda _=False: self.folder_map_manage_requested.emit())
            grid.addWidget(b, i // 2, i % 2, Qt.AlignLeft)

    def _save_log_folders(self, *_):
        ck = self.cfg.setdefault("check", {})
        ck["log_folder_check"] = self.lf_toggle.isChecked()
        core.set_log_folders(self.cfg, self.lf_edit.text())
        self.lf_edit.setText("，".join(core.get_log_folders(self.cfg)))
        self.lf_edit.setEnabled(self.lf_toggle.isChecked())
        self.log_folders_changed.emit()

    def refresh(self, cfg: dict | None = None) -> None:
        if cfg is not None:
            self.cfg = cfg
        self.lf_toggle.blockSignals(True)
        self.lf_toggle.setChecked(core.log_folder_check_enabled(self.cfg))
        self.lf_toggle.blockSignals(False)
        self.lf_edit.setText("，".join(core.get_log_folders(self.cfg)))
        self.lf_edit.setEnabled(self.lf_toggle.isChecked())
        effective = core.get_folder_map(self.cfg)

        self.row.set_desc(
            f"程序文件夹名里只要「包含」关键词就算映射成功（不要求完全相等）；"
            f"共 {len(effective)} 条，点任意一条即可修改关键词 / 游戏名或删除（含内置）。")

        self.map_title.setText(f"映射 · {len(effective)} 条（点击可修改）")
        self._fill(self.map_grid, [f"{k} → {v}" for k, v in effective.items()])


class MethodsPages:
    """判定规则的三个子页（侧栏二级菜单用）。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.folder = FolderMapView(cfg)
        self.rules = RulesView(cfg)
        self.games = GameRulesView(cfg)

    def refresh(self, cfg: dict | None = None) -> None:
        if cfg is not None:
            self.cfg = cfg
        self.rules.refresh(self.cfg)
        self.games.refresh(self.cfg)
        self.folder.refresh(self.cfg)

    def refresh_theme(self) -> None:
        self.refresh()

    def _refresh_folder_map(self) -> None:
        self.folder.refresh(self.cfg)


class DashboardView(QWidget):
    """总览仪表盘：统计卡片 + 需要关注的游戏摘要（列表在独立页）。"""

    check_requested = Signal()
    cancel_requested = Signal()
    notify_requested = Signal()
    goto_games = Signal()
    fixed_requested = Signal(str)          # 切换某个游戏的「已修复」标记
    state_requested = Signal(str, str)     # 右键手动设置状态：名称 + done/failed/空=清除
    summary_sort_changed = Signal(str)     # 关注列表排序方式

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # ---------- 顶部总览 ----------
        self.hero = Card(padding=18)
        h = QVBoxLayout()
        h.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(3)
        self.title = _label("准备就绪", 20, "text", bold=True)
        self.subtitle = _label("点「开始检查」扫描所有游戏的日常与异常", 12, "text_muted")
        left.addWidget(self.title)
        left.addWidget(self.subtitle)
        top.addLayout(left, 1)

        self.notify_btn = QPushButton("发送通知")
        self.notify_btn.setObjectName("soft")
        self.notify_btn.setCursor(Qt.PointingHandCursor)
        self.notify_btn.setEnabled(False)
        self.notify_btn.clicked.connect(self.notify_requested.emit)

        self.check_btn = QPushButton("开始检查")
        self.check_btn.setObjectName("primary")
        self.check_btn.setCursor(Qt.PointingHandCursor)
        self.check_btn.clicked.connect(self._on_check_clicked)

        top.addWidget(self.notify_btn)
        top.addWidget(self.check_btn)
        h.addLayout(top)

        self.progress = ProgressLine()
        self.progress.setVisible(False)
        h.addWidget(self.progress)

        self.scan_hint = _label("", 11, "text_muted")
        self.scan_hint.setVisible(False)
        h.addWidget(self.scan_hint)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.tile_error = StatTile("—", "异常", "danger")
        self.tile_failed = StatTile("—", "日常未完成", "warning")
        self.tile_susp = StatTile("—", "存疑", "warning")
        self.tile_time = StatTile("—", "上次检查", "text_dim")
        for t in (self.tile_error, self.tile_failed, self.tile_susp, self.tile_time):
            t.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            stats.addWidget(t)
        h.addLayout(stats)
        self.hero.lay.addLayout(h)
        lay.addWidget(self.hero)

        # ---------- 需要关注的游戏摘要 ----------
        self.summary_card = Card(padding=16)
        self.summary_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        sl = QVBoxLayout()
        sl.setSpacing(7)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.summary_title = _label("关注列表", 13, "text", bold=True)
        head.addWidget(self.summary_title)
        head.addStretch(1)
        self.summary_sort = ThemedCombo(core.SUMMARY_SORTS,
                                        core.DEFAULT_SUMMARY_SORT, 132)
        self.summary_sort.setToolTip(
            "关注列表排序：未完成优先 = 未完成 → 异常 → 其它 → 已修复（倒数第二）→ 存疑（最后）")
        self.summary_sort.changed.connect(self.summary_sort_changed.emit)
        head.addWidget(self.summary_sort)
        sl.addLayout(head)
        self.summary_lay = QGridLayout()
        self.summary_lay.setSpacing(6)
        sl.addLayout(self.summary_lay)
        self.summary_all = QPushButton("在游戏列表查看全部 →")
        self.summary_all.setObjectName("ghost")
        self.summary_all.setCursor(Qt.PointingHandCursor)
        self.summary_all.setFixedHeight(32)
        self.summary_all.clicked.connect(self.goto_games.emit)
        sl.addWidget(self.summary_all)
        self.summary_card.lay.addLayout(sl)
        lay.addWidget(self.summary_card, 1)

        self._running = False
        self.last_check_time = ""
        self._items: list = []
        self._cfg: dict = {}
        self._summary_sort = core.DEFAULT_SUMMARY_SORT
        self._badge_seq = 0

    # ---------- 配置 ----------
    def set_config(self, cfg: dict) -> None:
        """主窗口把配置注入总览页（标记、排序、开关都要读它）。"""
        self._cfg = cfg
        self._summary_sort = core.get_summary_sort(cfg)
        self.summary_sort.set_value(self._summary_sort)
        self.refresh_stats()

    def set_summary_sort(self, mode: str) -> None:
        if mode in dict(core.SUMMARY_SORTS):
            self._summary_sort = mode
            self.summary_sort.set_value(mode)
            self._rebuild_summary()

    # ---------- 交互 ----------
    def _on_check_clicked(self) -> None:
        if self._running:
            self.cancel_requested.emit()
        else:
            self.check_requested.emit()

    def set_scan_state(self, running: bool, done: int = 0, total: int = 0,
                       current: str = "") -> None:
        self._running = running
        self.progress.setVisible(running)
        self.scan_hint.setVisible(running)
        self.check_btn.setText("停止检查" if running else "开始检查")
        self.check_btn.setObjectName("danger" if running else "primary")
        self.check_btn.style().unpolish(self.check_btn)
        self.check_btn.style().polish(self.check_btn)
        self.notify_btn.setEnabled(not running)
        if running:
            self.progress.set_indeterminate(total == 0)
            if total:
                self.progress.set_value(done / max(1, total))
            self.scan_hint.setText(f"正在检查 {done}/{total}　{current}")
            self.title.setText("正在检查…")
            self.subtitle.setText("扫描日志关键字、debug 目录与异常文件夹")
        else:
            self.progress.set_indeterminate(False)
            self.scan_hint.setVisible(False)
            self.refresh_stats()          # 停止/结束后恢复标题，别停留在「正在检查…」

    def set_items(self, items) -> None:
        self._items = list(items)
        self.refresh_stats()

    def update_item(self, item) -> None:
        for i, old in enumerate(self._items):
            if old.key == item.key:
                self._items[i] = item
                break
        self.refresh_stats()

    def _suspect_of(self, i) -> bool:
        """关掉日志阈值检查后，旧的存疑结果不再显示。"""
        return bool(i.suspicious and core.log_threshold_check_enabled(self._cfg))

    def refresh_stats(self) -> None:
        # 已排除 / 强制已完成的不参与首页统计：卡片、标题、关注列表同一口径，
        # 否则会出现「顶部 N 个需要关注，下面却暂无需要关注的游戏」。
        visible = [i for i in self._items
                   if core.home_attention_visible(self._cfg, i)]
        err = sum(1 for i in visible if i.has_error)
        failed = sum(1 for i in visible
                     if core.effective_failed(self._cfg, i))
        susp = sum(1 for i in visible if self._suspect_of(i))
        self.tile_error.set_values(str(err))
        self.tile_failed.set_values(str(failed))
        self.tile_susp.set_values(str(susp))
        self.tile_time.set_values(self.last_check_time or "—")
        if not self._running:
            if not self._items:
                self.title.setText("尚未检查")
                self.subtitle.setText("点击「开始检查」扫描游戏日常")
            elif err or failed or susp:
                self.title.setText(f"{err + failed + susp} 个游戏需要关注")
                self.subtitle.setText("异常、未完成与存疑的日常会推送到钉钉")
            else:
                self.title.setText("一切正常")
                self.subtitle.setText(f"共检查 {len(self._items)} 个游戏，全部完成")
        self._rebuild_summary()

    def _rebuild_summary(self) -> None:
        """摘要卡片：异常 / 未完成 / 存疑的游戏，彩色徽章小卡两列网格。

        格子、名称、徽章一律给唯一 objectName。以前它们全叫 #scoped，
        格子上的半透明背景会顺着样式表级联到子控件，游戏名后面出现深浅不一的
        色块（不同游戏文字长度不同，看起来就是「背景不统一」）。现在改成
        不透明实色 + 唯一选择器，每一行底色完全一致。
        """
        while self.summary_lay.count():
            item = self.summary_lay.takeAt(0)
            w = item.widget()
            if w:
                w.hide()               # 先隐藏再销毁；保持父子关系，避免变成顶层小窗
                w.deleteLater()
        attn = [i for i in self._items
                if core.home_attention_visible(self._cfg, i)
                and (i.has_error
                     or core.effective_failed(self._cfg, i)
                     or self._suspect_of(i))]
        # 默认：未完成 → 异常 → 其它 → 已修复（倒数第二）→ 存疑（最后）；可切换其它方式
        attn = core.sort_summary_items(attn, self._cfg, self._summary_sort)
        p = THEME.p
        self.summary_lay.setColumnStretch(0, 1)        # 两列等宽，左右对齐
        self.summary_lay.setColumnStretch(1, 1)
        n_fixed = sum(1 for i in attn if core.is_fixed(self._cfg, i))
        title = f"关注列表 · {len(attn)}" if attn else "关注列表"
        self.summary_title.setText(title + (f"（已修复 {n_fixed}）" if n_fixed else ""))
        if not attn:
            txt = "暂无需要关注的游戏" if any(i.scanned for i in self._items) \
                else "还没有检查结果，点「开始检查」跑一轮"
            empty = _label(txt, 12, "text_muted")
            self.summary_lay.addWidget(empty, 0, 0, 1, 2)
            self._summary_tail(1)
            return

        # 统一的不透明底色：由卡片表面色与文字色按固定比例混合，所有行一模一样
        base, fg = p.surface(), QColor(p.text)
        mix = QColor(round(base.red() * 0.95 + fg.red() * 0.05),
                     round(base.green() * 0.95 + fg.green() * 0.05),
                     round(base.blue() * 0.95 + fg.blue() * 0.05))
        cell_bg, cell_border = mix.name(), rgba(p.text, 0.07)

        def _badge(text: str, color: str, tag: str) -> QLabel:
            b = QLabel(text)
            b.setObjectName(tag)
            f = QFont("Microsoft YaHei UI")
            f.setPixelSize(10)
            f.setWeight(QFont.DemiBold)
            b.setFont(f)
            scoped(b,
                   f"background: {rgba(color, 0.16)}; color: {color};"
                   f"border-radius: 8px; padding: 2px 8px;")
            return b

        for idx, it in enumerate(attn[:40]):
            cell = QFrame()
            cell.setObjectName(f"summaryCell_{idx}")
            cell.setAttribute(Qt.WA_StyledBackground, True)
            cell.setFixedHeight(34)                 # 固定行高，两列才对得齐
            cell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            scoped(cell,
                   f"background: {cell_bg}; border: 1px solid {cell_border};"
                   f"border-radius: 10px;")
            h = QHBoxLayout(cell)
            h.setContentsMargins(10, 4, 6, 4)
            h.setSpacing(5)
            name = _label(it.name, 12, "text", bold=True)
            name.setObjectName(f"summaryName_{idx}")
            tint(name, p.text)
            name.setToolTip(it.name)
            h.addWidget(name)
            h.addStretch(1)
            fixed = core.is_fixed(self._cfg, it)
            manual = core.manual_state_of(self._cfg, it)
            seq = 0

            def add_badge(text: str, color: str, tip: str = "") -> None:
                nonlocal seq
                b = _badge(text, color, f"summaryBadge_{idx}_{seq}")
                seq += 1
                if tip:
                    b.setToolTip(tip)
                h.addWidget(b)

            if it.has_error:
                flag = "、".join(sorted(it.error_flags)) or "异常"
                short = flag if len(flag) <= 14 else flag[:13] + "…"
                add_badge(short, p.danger, flag)
            if core.effective_failed(self._cfg, it):
                add_badge("未完成", p.warning,
                          "手动设置，次日 0 点自动失效" if manual == "failed" else "")
            if self._suspect_of(it):
                add_badge("存疑", p.accent)
            if manual == "done" and it.has_error:
                add_badge("手动已完成", p.success,
                          "右键可清除；次日 0 点自动失效")
            if fixed:
                add_badge("已修复", p.success, "次日 0 点自动失效")

            btn = QPushButton("×" if fixed else "✓")
            btn.setObjectName("ghost")
            btn.setFixedSize(24, 22)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip("取消「已修复」标记" if fixed else "标记为已修复")
            btn.setAccessibleName(btn.toolTip() or "")
            bf = QFont("Microsoft YaHei UI")
            bf.setPixelSize(12)
            btn.setFont(bf)
            # 带选择器的内联样式：只压掉主题里的大 padding，避免小按钮里的字被裁
            btn.setStyleSheet("QPushButton#ghost{padding:0px;min-height:0px;}")
            btn.clicked.connect(lambda _=False, n=it.name: self.fixed_requested.emit(n))
            h.addWidget(btn)
            # 右键菜单：设为已完成 / 未完成 / 已修复 / 清除手动状态
            cell.setContextMenuPolicy(Qt.CustomContextMenu)
            cell.customContextMenuRequested.connect(
                lambda pos, w=cell, n=it.name, fx=fixed, mn=manual:
                    self._cell_menu(w, pos, n, fx, mn))
            self.summary_lay.addWidget(cell, idx // 2, idx % 2)
        rows = (min(len(attn), 40) + 1) // 2
        if len(attn) > 40:
            more = _label(f"…还有 {len(attn) - 40} 款", 11, "text_muted")
            self.summary_lay.addWidget(more, rows, 0, 1, 2)
            rows += 1
        self._summary_tail(rows)

    def _cell_menu(self, cell, pos, name: str, fixed: bool, manual: str) -> None:
        """关注列表格子右键菜单：手动设置完成状态 / 强制已完成 / 已修复 / 清除覆盖。

        所有记号都只有当天有效，次日（0 点后）自动清除。"""
        menu = QMenu(cell)
        a_done = menu.addAction("设为已完成")
        a_done.setToolTip("按已完成看待；次日 0 点自动失效")
        a_failed = menu.addAction("设为未完成")
        a_failed.setToolTip("按未完成看待；次日 0 点自动失效")
        a_force = menu.addAction("强制已完成（今日不再关注）")
        a_force.setToolTip(
            "直接判定为已完成，并从首页关注列表排除；次日 0 点自动失效")
        a_clear = menu.addAction("清除手动状态（跟随检查结果）") if manual else None
        menu.addSeparator()
        a_fixed = menu.addAction("取消已修复" if fixed else "标记已修复")
        a_fixed.setToolTip("已修复只保留到今天，次日 0 点自动失效")
        chosen = menu.exec(cell.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is a_done:
            self.state_requested.emit(name, "done")
        elif chosen is a_failed:
            self.state_requested.emit(name, "failed")
        elif chosen is a_force:
            self.state_requested.emit(name, "forced")
        elif a_clear is not None and chosen is a_clear:
            self.state_requested.emit(name, "")
        elif chosen is a_fixed:
            self.fixed_requested.emit(name)

    def _summary_tail(self, row: int) -> None:
        """末尾垫一个可拉伸空白：多出来的高度落在它身上，卡片里的行不会被拉高。"""
        self.summary_lay.addItem(
            QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding), row, 0, 1, 2)

    def refresh_theme(self) -> None:
        tint(self.title, THEME.p.text)
        tint(self.subtitle, THEME.p.text_muted)
        tint(self.scan_hint, THEME.p.text_muted)
        tint(self.summary_title, THEME.p.text)
        self.summary_sort.refresh_theme()
        self._rebuild_summary()
        for t in (self.tile_error, self.tile_failed, self.tile_susp, self.tile_time):
            t.refresh()
        self.hero.update()
        self.summary_card.update()


# ---------------------------------------------------------------- 对话框

class _DialogPanel(QFrame):
    """对话框的圆角底板（玻璃质感，跟随主题）。"""

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = max(10, min(p.radius_card, 20))
        path = paint_glass(painter, rect, radius, self.mapToGlobal(QPoint(0, 0)), p,
                           THEME.blur_active, 0.18 if p.dark else 0.24)
        painter.setPen(QPen(qc(p.border, 0.16), 1))
        painter.drawPath(path)
        painter.end()


class ThemedDialog(QDialog):
    """无边框 + 跟随皮肤 + 可拖动的对话框。"""

    def __init__(self, parent=None, title: str = "", width: int = 470):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setMinimumWidth(width)
        self._drag_pos = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        self.panel = _DialogPanel(self)
        outer.addWidget(self.panel)
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.panel.setGraphicsEffect(shadow)

        self.body = QVBoxLayout(self.panel)
        self.body.setContentsMargins(20, 16, 20, 18)
        self.body.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title_lbl = _label(title, 14, "text", bold=True)
        head.addWidget(self.title_lbl, 1)
        close = QPushButton()
        close.setFixedSize(24, 24)
        close.setCursor(Qt.PointingHandCursor)
        close.setText(GLYPH["close"])
        f = glyph_font(10)
        close.setFont(f)
        tp = THEME.p
        close.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:6px;"
            f"color:{tp.text_muted};}}"
            f"QPushButton:hover{{background:{rgba(tp.danger, 0.85)};color:#FFFFFF;}}")
        close.clicked.connect(self.reject)
        head.addWidget(close)
        self.body.addLayout(head)

    def exec(self) -> int:
        BACKDROP.snapshot(self.screen())
        return super().exec()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)

    def add_footer(self, extra: QPushButton | None = None, ok_text: str = "保存") -> QPushButton:
        row = QHBoxLayout()
        row.setSpacing(8)
        if extra is not None:
            extra.setCursor(Qt.PointingHandCursor)
            extra.setFixedHeight(32)
            row.addWidget(extra)
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("ghost")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setFixedHeight(32)
        cancel.clicked.connect(self.reject)
        ok = QPushButton(ok_text)
        ok.setObjectName("primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setFixedHeight(32)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        self.body.addLayout(row)
        return ok


class ThresholdDialog(ThemedDialog):
    """单个游戏的 debug 日志体积阈值。"""

    def __init__(self, parent, name: str, current_mb: int):
        super().__init__(parent, "设置日志阈值", 440)
        self.body.addWidget(_label(f"「{name}」debug 目录日志阈值", 13, "text", bold=True))
        self.body.addWidget(_label(
            "顶层 .log 合计体积超过该值时标记为「存疑」，在存疑提醒与日常汇总里体现。",
            11, "text_muted", wrap=True))
        self.spin = NumberStepper(current_mb, 1, 500, 1, " MB", 120)
        self.body.addWidget(self.spin)
        self.add_footer()

    def value(self) -> int:
        return self.spin.value()


class RuleDialog(ThemedDialog):
    """新建 / 编辑一条判定规则。"""

    def __init__(self, parent, rule: dict | None = None):
        editing = bool(rule and rule.get("id"))
        super().__init__(parent, "编辑规则" if editing else "新建判定规则", 580)
        r = dict(rule or {})
        self._id = r.get("id", "")
        self.body.addWidget(_label(
            "规则只描述「怎么从自动化程序日志里判断日常完成」；"
            "看哪些日志文件夹由「判定规则 · 日志文件夹判定」统一设置，"
            "同一条规则可以给多款游戏共用。", 11, "text_muted", wrap=True))

        self.name_edit = _edit("规则名，如「MAA 系 · 任务已全部完成！」", r.get("name", ""))
        self._field("规则名", self.name_edit)

        self.body.addWidget(_label("判定方式（可多选，任一满足即算完成）", 11, "text"))
        grid = QGridLayout()
        grid.setSpacing(6)
        self._btns: list[tuple[str, QPushButton]] = []
        cur = r.get("methods") or [core.DEFAULT_METHOD]
        for i, (key, label, desc) in enumerate(core.METHODS):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setCheckable(True)
            b.setChecked(key in cur)
            b.setFixedHeight(34)
            b.setToolTip(desc)
            b.setStyleSheet(
                f"QPushButton{{text-align:left;padding:4px 12px;border-radius:9px;"
                f"background:{rgba(THEME.p.text, 0.06)};color:{THEME.p.text};font-size:12px;}}"
                f"QPushButton:checked{{background:{rgba(THEME.p.accent, 0.18)};"
                f"color:{THEME.p.accent};font-weight:600;"
                f"border:1px solid {rgba(THEME.p.accent, 0.5)};}}")
            b.toggled.connect(self._sync)
            grid.addWidget(b, i // 3, i % 3)
            self._btns.append((key, b))
        self.body.addLayout(grid)

        self.kw_edit = _edit("要比对的关键字，如：任务已全部完成！", r.get("keyword", ""))
        self.kw_box = self._field("关键字", self.kw_edit)
        self.ft_edit = _edit("目标文件名，如 today_finish.flag（相对自动化程序日志文件夹）",
                            r.get("file_target", ""))
        self.ft_box = self._field("文件名", self.ft_edit)
        self.ext_sel = SuffixMultiSelect(r.get("ext", ""))
        self.ext_sel.changed.connect(self._sync)
        self._field("扫描文件后缀", self.ext_sel)
        self.note_edit = _edit("备注（可空）：这条规则从哪来、要注意什么", r.get("note", ""))
        self._field("备注", self.note_edit)

        self.preview = _label("", 10, "text_muted", wrap=True)
        self.body.addWidget(self.preview)
        self.warn = _label("", 10, "danger", wrap=True)
        self.body.addWidget(self.warn)

        for e in (self.kw_edit, self.ft_edit):
            e.textChanged.connect(self._sync)
        self._sync()
        self.add_footer(ok_text="保存规则")

    def _field(self, name: str, w: QWidget) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        lbl = _label(name, 11, "text")
        lbl.setFixedWidth(92)
        row.addWidget(lbl)
        row.addWidget(w, 1)
        self.body.addWidget(box)
        return box

    def methods(self) -> list:
        return [k for k, b in self._btns if b.isChecked()] or [core.DEFAULT_METHOD]

    def value(self) -> dict:
        return {"id": self._id,
                "name": self.name_edit.text().strip(),
                "methods": self.methods(),
                "keyword": self.kw_edit.text().strip(),
                "file_target": self.ft_edit.text().strip(),
                "ext": self.ext_sel.value(),
                "note": self.note_edit.text().strip()}

    def _sync(self, *_):
        ms = self.methods()
        need_kw = any(m in ("keyword", "keyword_today") for m in ms)
        need_ft = any(m in core.FILE_METHODS for m in ms)
        self.kw_box.setVisible(need_kw)
        self.ft_box.setVisible(need_ft)
        preview = core.rule_text_bare(self.value())
        self.preview.setText("保存后：" + preview)
        self.preview.setToolTip(preview)

    def accept(self) -> None:
        v = self.value()
        if not v["name"]:
            self.warn.setText("先给规则起个名字，方便在游戏下拉里认出来")
            return
        if any(m in ("keyword", "keyword_today") for m in v["methods"]) and not v["keyword"]:
            self.warn.setText("选了关键字方式，得填要比对的关键字")
            return
        if any(m in core.FILE_METHODS for m in v["methods"]) and not v["file_target"]:
            self.warn.setText("选了文件方式，得填目标文件名")
            return
        self.warn.setText("")
        super().accept()


class GameRuleDialog(ThemedDialog):
    """单个游戏：选一条规则。"""

    def __init__(self, parent, name: str, rules: list, current: str = ""):
        super().__init__(parent, "设置判定规则", 520)
        self.body.addWidget(_label(f"「{name}」用哪条规则判断日常完成？", 13, "text", bold=True))
        self._rules = {r["id"]: r for r in rules}
        items = [(r["id"], r["name"]) for r in rules]
        items.append(("", "未设定（跟随默认规则）"))
        self.combo = ThemedCombo(items, current, 420)
        self.combo.changed.connect(self._sync)
        row = QHBoxLayout()
        row.addWidget(self.combo)
        row.addStretch(1)
        self.body.addLayout(row)
        self.preview = _label("", 10, "text_muted", wrap=True)
        self.body.addWidget(self.preview)
        self._sync(current)
        self.add_footer(ok_text="应用")

    def _sync(self, rid: str) -> None:
        r = self._rules.get(rid)
        text = core.rule_text_bare(r) if r else "用游戏自带的完成关键字，按默认判断方式检查"
        self.preview.setText("匹配规则：" + text)
        self.preview.setToolTip(text)

    def value(self) -> str:
        return self.combo.value()


class AddGameDialog(ThemedDialog):
    """添加一款自定义游戏：自动化程序目录 + 引用的规则。"""

    def __init__(self, parent, rules: list, names: list, editing: dict | None = None):
        editing = dict(editing or {})
        super().__init__(parent, "编辑游戏" if editing else "添加游戏", 580)
        self._names = set(names)
        self._editing_name = editing.get("name", "")
        self.body.addWidget(_label(
            "填这个游戏用的「自动化程序目录」（根目录即可）；程序会到它下面的 "
            "logs / log / debug / .log 日志文件夹里找完成关键字。"
            "检查的是自动化程序写出来的日志，不是游戏自己的日志。"
            "扫描哪些扩展名由所引用规则里的「扫描文件后缀」决定，不再单独设置。",
            11, "text_muted", wrap=True))

        self.name_edit = _edit("游戏名", editing.get("name", ""))
        self._field("游戏名", self.name_edit)

        self.folder_edit = _edit(r"自动化程序目录，如 D:\Automate\XXX",
                                editing.get("folder", ""))
        browse = QPushButton("浏览…")
        browse.setObjectName("soft")
        browse.setCursor(Qt.PointingHandCursor)
        browse.setFixedHeight(32)
        browse.clicked.connect(self._pick_folder)
        box = QWidget()
        fl = QHBoxLayout(box)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(8)
        fl.addWidget(self.folder_edit, 1)
        fl.addWidget(browse)
        self._field("扫描自动化程序目录", box)

        self.msg_edit = _edit("完成提示语，留空 = 「<游戏名>日常已完成！」",
                              editing.get("success_message", ""))
        self._field("完成提示", self.msg_edit)

        self._rules = {r["id"]: r for r in rules}
        items = [(r["id"], r["name"]) for r in rules]
        items.append(("", "暂不绑定（未设定，先用默认规则）"))
        self.rule_combo = ThemedCombo(items, editing.get("rule_id",
                                                         rules[0]["id"] if rules else ""), 420)
        self.rule_combo.changed.connect(self._sync)
        self._field("引用规则", self.rule_combo)
        self.preview = _label("", 10, "text_muted", wrap=True)
        self.body.addWidget(self.preview)
        self.warn = _label("", 10, "danger", wrap=True)
        self.body.addWidget(self.warn)
        self._sync(self.rule_combo.value())
        self.add_footer(ok_text="保存")

    def _field(self, name: str, w: QWidget) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        lbl = _label(name, 11, "text")
        lbl.setFixedWidth(116)
        row.addWidget(lbl)
        row.addWidget(w, 1)
        self.body.addWidget(box)
        return box

    def _pick_folder(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        start = self.folder_edit.text().strip() or "D:\\"
        path = QFileDialog.getExistingDirectory(self, "选择自动化程序目录", start)
        if path:
            self.folder_edit.setText(path.replace("/", "\\"))

    def _sync(self, rid: str) -> None:
        r = self._rules.get(rid)
        text = core.rule_text_bare(r) if r else "沿用默认判断方式 + 游戏自带关键字"
        self.preview.setText("判断规则：" + text)
        self.preview.setToolTip(text)

    def value(self) -> dict:
        # v1.7.4：游戏级「扩展名」字段已删，扫描后缀统一由引用规则的「扫描文件后缀」决定；
        # 这里回传空串仅为了兼容 core.add_game 的签名（不再写进新配置）。
        return {"name": self.name_edit.text().strip(),
                "folder": self.folder_edit.text().strip(),
                "ext": "",
                "success_message": self.msg_edit.text().strip(),
                "rule_id": self.rule_combo.value()}

    def accept(self) -> None:
        v = self.value()
        if not v["name"]:
            self.warn.setText("填一下游戏名")
            return
        if v["name"] in self._names and v["name"] != self._editing_name:
            self.warn.setText(f"「{v['name']}」已经在清单里了，换个名字或直接改它的规则")
            return
        if not v["folder"]:
            self.warn.setText("填一下自动化程序目录（可以点浏览选）")
            return
        self.warn.setText("")
        super().accept()


class _GameListDialog(ThemedDialog):
    """每行一个游戏的批量编辑对话框基类（滚动列表）。"""

    ROW_H = 40

    def _make_scroll(self, rows: list, max_h: int = 320) -> None:
        box = QWidget()
        flat(box)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for w in rows:
            lay.addWidget(w)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        flat(scroll)
        flat(scroll.viewport())
        scroll.setWidget(box)
        n = len(rows)
        if n <= 8:
            scroll.setFixedHeight(self.ROW_H * n + 6 * (n - 1))
        else:
            scroll.setFixedHeight(max_h)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.body.addWidget(scroll)


class FolderMapDialog(_GameListDialog):
    """编辑文件夹关键词 → 游戏名映射（内置条目也可改 / 删）。"""

    def __init__(self, parent, current: dict):
        super().__init__(parent, "文件夹部分映射", 560)
        self.body.addWidget(_label(
            "程序文件夹名里只要包含左侧关键词就算映射成功（不要求完全相等），"
            "用于异常监控与无错天数统计。内置 20 条在这里同样可以直接改或删。",
            11, "text_muted", wrap=True))
        self.rows_lay = QVBoxLayout()
        self.rows_lay.setContentsMargins(0, 0, 0, 0)
        self.rows_lay.setSpacing(6)
        self._entries: list = []
        holder = QWidget()
        flat(holder)
        holder.setLayout(self.rows_lay)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        flat(scroll)
        flat(scroll.viewport())
        scroll.setWidget(holder)
        scroll.setFixedHeight(240)
        self.body.addWidget(scroll)
        for k, v in sorted(current.items()):
            self._add_row(k, v)
        tools = QHBoxLayout()
        tools.setContentsMargins(0, 8, 0, 0)
        tools.setSpacing(8)
        add_btn = QPushButton("添加一条映射")
        add_btn.setObjectName("soft")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setFixedHeight(32)
        add_btn.clicked.connect(lambda: self._add_row("", ""))
        tools.addWidget(add_btn)
        reset_btn = QPushButton("恢复内置 20 条")
        reset_btn.setObjectName("ghost")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setFixedHeight(32)
        reset_btn.setToolTip("丢掉所有自定义改动，把列表还原成内置默认映射")
        reset_btn.clicked.connect(self._reset_builtin)
        tools.addWidget(reset_btn)
        tools.addStretch(1)
        self.body.addLayout(tools)
        self.add_footer()

    def _reset_builtin(self) -> None:
        for entry in list(self._entries):
            self._remove_row(entry)
        for k, v in sorted(core.FOLDER_NAME_MAP.items()):
            self._add_row(k, v)

    def _style_edit(self, e: QLineEdit, ph: str) -> None:
        p = THEME.p
        e.setPlaceholderText(ph)
        e.setFixedHeight(32)
        e.setStyleSheet(
            f"QLineEdit{{background:{rgba(p.text, 0.06)};border:1px solid {rgba(p.border, 0.15)};"
            f"border-radius:8px;color:{p.text};font-size:12px;padding:0 10px;}}")

    def _add_row(self, k: str, v: str) -> None:
        row = QWidget()
        flat(row)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(7)
        e1 = QLineEdit(k)
        self._style_edit(e1, "关键词，如 FGO")
        e2 = QLineEdit(v)
        self._style_edit(e2, "游戏名，如 FGO")
        del_btn = QPushButton("删除")
        del_btn.setObjectName("ghost")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setFixedHeight(32)
        entry = [row, e1, e2]
        del_btn.clicked.connect(lambda: self._remove_row(entry))
        rl.addWidget(e1, 3)
        rl.addWidget(e2, 2)
        rl.addWidget(del_btn)
        self._entries.append(entry)
        self.rows_lay.addWidget(row)

    def _remove_row(self, entry: list) -> None:
        if entry in self._entries:
            self._entries.remove(entry)
            entry[0].hide()
            entry[0].deleteLater()

    def values(self) -> dict:
        out = {}
        for _, e1, e2 in self._entries:
            k, v = e1.text().strip(), e2.text().strip()
            if k and v:
                out[k] = v
        return out


class _ElideLabel(QLabel):
    """单行路径标签：按可用宽度中间省略，完整路径放 tooltip。"""

    def __init__(self, text: str = "", size: int = 12, color_key: str = "text"):
        super().__init__()
        self._full = text
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(size)
        self.setFont(f)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tint(self, getattr(THEME.p, color_key))
        self.set_path(text)

    def set_path(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._apply()

    def _apply(self) -> None:
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, Qt.ElideMiddle, max(40, self.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply()


class ScanDirDialog(ThemedDialog):
    """设置 → 扫描目录：勾选参与扫描的根目录、排除子目录、设扫描深度，
    并可预览当前配置会扫到哪些游戏目录 / 异常文件夹。"""

    def __init__(self, parent, cfg: dict):
        ck = cfg.get("check") or {}
        super().__init__(parent, "扫描目录", 660)
        self._cfg = cfg
        self._dirs = [(str(d), True) for d in (ck.get("scan_dirs") or [])] or \
                     [(str(d), True) for d in core.DEFAULT_SCAN_DIRS]
        self._excl = [str(x) for x in (ck.get("scan_excludes") or [])]

        self.body.addWidget(_label(
            "程序在这些目录里找 on_error / screencap 异常文件夹和各游戏目录。"
            "可以加多个（比如别的盘），勾选决定参不参与；不想被扫的子目录放到下面的排除列表。",
            11, "text_muted", wrap=True))

        self.body.addWidget(_label("扫描根目录", 12, "text", bold=True))
        self.dirs_lay = QVBoxLayout()
        self.dirs_lay.setContentsMargins(0, 0, 0, 0)
        self.dirs_lay.setSpacing(6)
        self._dirs_scroll = self._list_scroll(self.dirs_lay, 3)
        self.body.addWidget(self._dirs_scroll)

        add_line = QHBoxLayout()
        add_line.setSpacing(8)
        add_dir = QPushButton("添加目录…")
        add_dir.setObjectName("soft")
        add_dir.setCursor(Qt.PointingHandCursor)
        add_dir.setFixedHeight(32)
        add_dir.clicked.connect(self._add_dir)
        restore = QPushButton("恢复默认")
        restore.setObjectName("ghost")
        restore.setCursor(Qt.PointingHandCursor)
        restore.setFixedHeight(32)
        restore.setToolTip(f"恢复成 {core.DEFAULT_SCAN_DIRS[0]}")
        restore.clicked.connect(self._restore)
        self.dir_hint = _label("", 10, "text_muted")
        add_line.addWidget(add_dir)
        add_line.addWidget(restore)
        add_line.addWidget(self.dir_hint, 1)
        self.body.addLayout(add_line)

        self.body.addWidget(_label("排除的子目录", 12, "text", bold=True))
        self.body.addWidget(_label(
            "选中的子目录会整棵跳过（不扫异常文件夹，也不当游戏目录）；"
            "只写文件夹名（如 MFAAKes）就排除所有同名的。", 10, "text_muted", wrap=True))
        self.excl_lay = QVBoxLayout()
        self.excl_lay.setContentsMargins(0, 0, 0, 0)
        self.excl_lay.setSpacing(6)
        self._excl_scroll = self._list_scroll(self.excl_lay, 2)
        self.body.addWidget(self._excl_scroll)

        add_excl = QPushButton("排除一个子目录…")
        add_excl.setObjectName("soft")
        add_excl.setCursor(Qt.PointingHandCursor)
        add_excl.setFixedHeight(32)
        add_excl.clicked.connect(self._add_excl)
        self.body.addWidget(add_excl, 0, Qt.AlignLeft)

        # ---- 扫描深度 + 预览 ----
        self.body.addWidget(_label("扫描深度", 12, "text", bold=True))
        dep_row = QHBoxLayout()
        dep_row.setSpacing(10)
        self.depth_spin = NumberStepper(core.get_scan_depth(self._cfg), 0, 16, 1,
                                        "", 120)
        self.depth_spin.setToolTip(
            "进入根目录下几层找异常文件夹 / 游戏目录；0 = 不限。\n"
            "目录树很大时调小能明显加快检查")
        dep_row.addWidget(self.depth_spin)
        dep_row.addWidget(_label("层（0 = 不限）", 10, "text_muted"))
        dep_row.addSpacing(12)
        scan_btn = QPushButton("扫描预览")
        scan_btn.setObjectName("soft")
        scan_btn.setCursor(Qt.PointingHandCursor)
        scan_btn.setFixedHeight(30)
        scan_btn.setToolTip("按上面的目录 / 排除 / 深度做一次只读扫描，看会找到什么")
        scan_btn.clicked.connect(self._preview)
        dep_row.addWidget(scan_btn)
        dep_row.addStretch(1)
        self.body.addLayout(dep_row)

        self.preview_out = QPlainTextEdit()
        self.preview_out.setReadOnly(True)
        self.preview_out.setVisible(False)
        self.preview_out.setMaximumHeight(150)
        self.body.addWidget(self.preview_out)

        self._rebuild_dirs()
        self._rebuild_excl()
        self.add_footer()

    def _preview(self) -> None:
        ck = dict(self._cfg.get("check") or {})
        ck["scan_dirs"] = [d for d, on in self._dirs if on]
        ck["scan_excludes"] = list(self._excl)
        ck["scan_depth"] = self.depth_spin.value()
        res = core.preview_scan({"check": ck})
        lines = []
        games = res["games"]
        lines.append(f"游戏目录（{len(games)}）：" + ("、".join(games) if games else "无"))
        errs = res["errors"]
        if errs:
            lines.append("异常文件夹：")
            for t, paths in errs.items():
                lines.append(f"  {core.TARGET_DESC_MAP.get(t, t)} {t}（{len(paths)}）：")
                lines.extend(f"    · {p}" for p in paths[:12])
                if len(paths) > 12:
                    lines.append(f"    … 还有 {len(paths) - 12} 个")
        else:
            lines.append("异常文件夹：未发现")
        self.preview_out.setPlainText("\n".join(lines))
        self.preview_out.setVisible(True)

    # ---------- 列表外壳 ----------
    def _list_scroll(self, lay: QVBoxLayout, rows: int) -> QScrollArea:
        holder = QWidget()
        flat(holder)
        holder.setLayout(lay)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.viewport().setStyleSheet(
            "QScrollArea > QWidget { background: transparent; }")
        sc.setWidget(holder)
        sc.setFixedHeight(max(1, rows) * 40 + 6)
        return sc

    @staticmethod
    def _fit(sc: QScrollArea, n: int) -> None:
        """列表高度跟着条目数走，最多 3 行，避免一大片空白。"""
        rows = min(3, max(1, n))
        sc.setFixedHeight(rows * 40 + 6 if n else 44)

    @staticmethod
    def _clear(lay: QVBoxLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
            elif item.layout() is not None:
                ScanDirDialog._clear(item.layout())

    def _row_frame(self) -> QFrame:
        p = THEME.p
        row = QFrame()
        row.setFixedHeight(40)
        scoped(row, f"background: {rgba(p.text, 0.05)}; border: none;"
                    f"border-radius: 10px;")
        return row

    # ---------- 根目录 ----------
    def _rebuild_dirs(self) -> None:
        self._clear(self.dirs_lay)
        p = THEME.p
        if not self._dirs:
            self.dirs_lay.addWidget(_label("还没有扫描目录，点下面「添加目录…」", 12, "warning"))
        for i, (path, on) in enumerate(self._dirs):
            row = self._row_frame()
            h = QHBoxLayout(row)
            h.setContentsMargins(12, 0, 8, 0)
            h.setSpacing(10)
            cb = QCheckBox()
            cb.setChecked(on)
            cb.setCursor(Qt.PointingHandCursor)
            cb.setToolTip("勾选即参与扫描")
            cb.toggled.connect(lambda v, idx=i: self._toggle_dir(idx, v))
            h.addWidget(cb)
            miss = not os.path.isdir(path)
            lbl = _ElideLabel(path, 12, "danger" if miss else "text")
            if miss:
                lbl.setToolTip(f"{path}（目录不存在，检查一下盘符或路径）")
            h.addWidget(lbl, 1)
            if miss:
                h.addWidget(Chip("不存在", p.warning))
            b = QPushButton("删除")
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(28)
            b.clicked.connect(lambda _=False, idx=i: self._remove_dir(idx))
            h.addWidget(b)
            self.dirs_lay.addWidget(row)
        self.dirs_lay.addStretch(1)
        active = sum(1 for _, on in self._dirs if on)
        self.dir_hint.setText(f"共 {len(self._dirs)} 个，{active} 个参与扫描"
                              if self._dirs else "")
        self._fit(self._dirs_scroll, len(self._dirs))

    def _toggle_dir(self, idx: int, on: bool) -> None:
        if 0 <= idx < len(self._dirs):
            self._dirs[idx] = (self._dirs[idx][0], on)
        active = sum(1 for _, o in self._dirs if o)
        self.dir_hint.setText(f"共 {len(self._dirs)} 个，{active} 个参与扫描")

    def _remove_dir(self, idx: int) -> None:
        if 0 <= idx < len(self._dirs):
            self._dirs.pop(idx)
        self._rebuild_dirs()

    def _add_dir(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        start = self._dirs[0][0] if self._dirs else "D:\\"
        path = QFileDialog.getExistingDirectory(self, "选择要扫描的目录", start)
        if not path:
            return
        path = os.path.normpath(path)
        if path in [x for x, _ in self._dirs]:
            self.dir_hint.setText("这个目录已经在列表里了")
            return
        self._dirs.append((path, True))
        self._rebuild_dirs()

    def _restore(self) -> None:
        self._dirs = [(core.DEFAULT_SCAN_DIRS[0], True)]
        self._excl = []
        self._rebuild_dirs()
        self._rebuild_excl()

    # ---------- 排除项 ----------
    def _rebuild_excl(self) -> None:
        self._clear(self.excl_lay)
        if not self._excl:
            self.excl_lay.addWidget(_label("暂无排除，所有子目录都会被扫描", 11, "text_muted"))
        for i, path in enumerate(self._excl):
            row = self._row_frame()
            h = QHBoxLayout(row)
            h.setContentsMargins(12, 0, 8, 0)
            h.setSpacing(10)
            h.addWidget(Glyph("delete", 14, THEME.p.text_dim))
            h.addWidget(_ElideLabel(path, 12, "text"), 1)
            b = QPushButton("移出")
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(28)
            b.setToolTip("从排除列表移除，重新参与扫描")
            b.clicked.connect(lambda _=False, idx=i: self._remove_excl(idx))
            h.addWidget(b)
            self.excl_lay.addWidget(row)
        self.excl_lay.addStretch(1)
        self._fit(self._excl_scroll, len(self._excl))

    def _remove_excl(self, idx: int) -> None:
        if 0 <= idx < len(self._excl):
            self._excl.pop(idx)
        self._rebuild_excl()

    def _add_excl(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        root = next((x for x, on in self._dirs if on and os.path.isdir(x)), "D:\\")
        path = QFileDialog.getExistingDirectory(self, "选择要排除的子目录", root)
        if not path:
            return
        path = os.path.normpath(path)
        if path in self._excl:
            return
        self._excl.append(path)
        self._rebuild_excl()

    # ---------- 结果 ----------
    def values(self) -> dict:
        return {"dirs": [p for p, on in self._dirs if on],
                "excludes": list(self._excl),
                "depth": int(self.depth_spin.value())}


class ExcludeDialog(ThemedDialog):
    """管理排除名单：勾选的游戏不再参与本程序的检查。"""

    COLS = 4

    def __init__(self, parent, names: list, excluded: list):
        super().__init__(parent, "排除检查的游戏", 560)
        self.body.addWidget(_label("勾选的游戏不再参与日常检查、异常监控与无错天数统计。",
                                   11, "text_muted", wrap=True))
        self.chips: dict[str, Chip] = {}
        box = QWidget()
        flat(box)
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(7)
        for i, nm in enumerate(names):
            c = Chip(nm)
            c.set_active(nm in excluded)
            c.clicked.connect(lambda _=False, cc=c: cc.set_active(not cc.is_active()))
            self.chips[nm] = c
            grid.addWidget(c, i // self.COLS, i % self.COLS, Qt.AlignLeft)
        grid.setColumnStretch(self.COLS, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        flat(scroll)
        flat(scroll.viewport())
        scroll.setWidget(box)
        scroll.setFixedHeight(min(250, 39 * ((len(names) + self.COLS - 1) // self.COLS) + 6))
        self.body.addWidget(scroll)
        self.add_footer()

    def values(self) -> list:
        return [nm for nm, c in self.chips.items() if c.is_active()]


class NotifyPreviewDialog(ThemedDialog):
    """发送前预览四条钉钉通知内容。"""

    def __init__(self, parent, messages: list):
        super().__init__(parent, "钉钉通知预览", 560)
        tp = THEME.p
        self.body.addWidget(_label("即将按顺序发送以下 4 条消息：", 11, "text_muted"))
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setFixedHeight(300)
        self.edit.setStyleSheet(
            f"QPlainTextEdit{{background:{rgba(tp.text, 0.05 if tp.dark else 0.04)};"
            f"border:1px solid {rgba(tp.border, 0.14)};border-radius:10px;color:{tp.text};"
            f"font-family:'Consolas','Microsoft YaHei UI';font-size:12px;padding:8px 10px;}}")
        self.edit.setPlainText("\n\n".join(f"—— {t} ——\n{c}" for t, c in messages))
        self.body.addWidget(self.edit)
        self.add_footer(ok_text="发送")


# ---------------------------------------------------------------- 设置页

class SettingsPage(QWidget):
    """设置页：顶部分页 + 「分组标题 / 设置行」列表。"""

    theme_changed = Signal()
    config_changed = Signal()
    autostart_requested = Signal(bool)
    test_ding_requested = Signal()
    exclude_manage_requested = Signal()
    scan_dir_manage_requested = Signal()

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._seg_controls: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        bar = QHBoxLayout()
        bar.setContentsMargins(2, 0, 0, 0)
        bar.setSpacing(20)
        self.tab_general = TabButton("一般设置")
        self.tab_about = TabButton("关于")
        self.tab_general.clicked.connect(lambda: self.switch_tab(0))
        self.tab_about.clicked.connect(lambda: self.switch_tab(1))
        bar.addWidget(self.tab_general)
        bar.addWidget(self.tab_about)
        bar.addStretch(1)
        outer.addLayout(bar)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.page_general = self._scroll_page()
        self.page_about = self._scroll_page()
        self.stack.addWidget(self.page_general)
        self.stack.addWidget(self.page_about)

        g = self.page_general.lay
        self._build_appearance(g)
        self._build_background(g)
        self._build_dingtalk(g)
        self._build_check(g)
        g.addStretch(1)

        a = self.page_about.lay
        self._build_about(a)
        a.addStretch(1)

        try:
            idx = int((self.cfg.get("ui") or {}).get("settings_tab", 0))
        except Exception:
            idx = 0
        self.switch_tab(idx if idx in (0, 1) else 0)

    # ---------- 结构 ----------
    def _scroll_page(self) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 4, 10, 22)
        lay.setSpacing(8)
        sc.setWidget(holder)
        sc.lay = lay
        return sc

    def switch_tab(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        self.tab_general.set_checked(idx == 0)
        self.tab_about.set_checked(idx == 1)
        self.cfg.setdefault("ui", {})["settings_tab"] = idx

    def _group(self, lay: QVBoxLayout, text: str) -> None:
        lay.addWidget(SettingGroup(text))

    def _row(self, lay: QVBoxLayout, icon: str, title: str, desc: str = "",
             control=None, content=None) -> SettingRow:
        row = SettingRow(icon, title, desc, control, content)
        lay.addWidget(row)
        return row

    def _segmented(self, items, current: str, on_pick) -> SegmentedControl:
        ctl = SegmentedControl(items, current)
        ctl.changed.connect(on_pick)
        self._seg_controls.append(ctl)
        return ctl

    def _style_segmented(self) -> None:
        for ctl in self._seg_controls:
            ctl.restyle()

    # ---------- 外观 ----------
    def _build_appearance(self, lay: QVBoxLayout) -> None:
        self._group(lay, "用户界面")
        skin = self._segmented([(k, PRESET_LABELS[k]) for k in PRESET_ORDER],
                               THEME.preset, self._on_preset)
        for key in PRESET_ORDER:
            skin.set_tooltip(key, PRESET_HINTS[key])
        self._row(lay, "settings", "皮肤样式", "亚克力玻璃 / 莫奈 Material You / Miuix 澎湃 OS", skin)

        mode = self._segmented([("auto", "跟随系统"), ("light", "浅色"), ("dark", "深色")],
                               THEME.mode, self._on_mode)
        self._row(lay, "bright", "明暗模式", "默认跟随系统配色，也可以手动锁定", mode)

        self.blur_toggle = ToggleSwitch(bool(self.cfg.get("ui", {}).get("blur", True)))
        self.blur_toggle.toggled.connect(self._on_blur)
        self._row(lay, "funnel", "毛玻璃模糊", "关闭后背景不再模糊，低配机器更流畅", self.blur_toggle)
        self._style_segmented()

    def _on_preset(self, key: str) -> None:
        THEME.set_preset(key)
        self.cfg.setdefault("ui", {})["preset"] = key
        self.config_changed.emit()
        self.theme_changed.emit()

    def _on_mode(self, key: str) -> None:
        THEME.set_mode(key)
        self.cfg.setdefault("ui", {})["theme"] = key
        self.config_changed.emit()
        self.theme_changed.emit()

    def _on_blur(self, on: bool) -> None:
        THEME.set_blur(on)
        self.cfg.setdefault("ui", {})["blur"] = on
        self.config_changed.emit()
        self.theme_changed.emit()

    # ---------- 后台与定时 ----------
    def _build_background(self, lay: QVBoxLayout) -> None:
        bg = self.cfg.setdefault("background", {})
        self._group(lay, "后台与定时")

        self.tray_toggle = ToggleSwitch(bool(bg.get("close_to_tray", True)))
        self.tray_toggle.toggled.connect(self._save_background)
        self._row(lay, "apps", "关闭窗口时最小化到托盘",
                  "程序继续在后台运行，托盘右键「退出」才真正结束", self.tray_toggle)

        self.startup_toggle = ToggleSwitch(autostart.is_enabled())
        self.startup_toggle.toggled.connect(lambda on: self.autostart_requested.emit(on))
        self._row(lay, "rocket", "开机自动启动",
                  "开机后在后台静默运行，不弹出主界面；移动文件夹后重新打开本开关即可",
                  self.startup_toggle)

        mins_opts = [(15, "15 分钟"), (30, "30 分钟"), (60, "1 小时"), (180, "3 小时"),
                     (360, "6 小时"), (720, "12 小时"), (1440, "24 小时")]
        cur = int(bg.get("interval_min", 720) or 720)
        # 旧配置可能是 45 / 90 这类非标准值：把它作为选项加进去，
        # 否则下拉显示「1 小时」而定时器实际按 45 分钟跑，用户完全看不出来
        if cur not in [m for m, _ in mins_opts]:
            mins_opts.append((cur, f"{cur} 分钟"))
            mins_opts.sort(key=lambda kv: kv[0])
        self.interval_combo = ThemedCombo([(str(m), t) for m, t in mins_opts], str(cur), 118)
        self.interval_combo.changed.connect(self._save_background)

        box = QWidget()
        flat(box)
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)
        self.sched_toggle = ToggleSwitch(bool(bg.get("check_enabled", False)))
        self.sched_toggle.toggled.connect(self._save_background)
        bl.addWidget(self.sched_toggle)
        bl.addWidget(self.interval_combo)
        self.sched_row = self._row(lay, "clock", "定时自动检查",
                                   "按间隔自动跑一轮检查（含异常监控与状态累计）", box)

        # 「检查后自动发送」行已移到下方「通知」分组，随通知方式条件显示

        self._refresh_bg_hint()
        self.interval_combo.setEnabled(self.sched_toggle.isChecked())

    def _refresh_bg_hint(self) -> None:
        bg = self.cfg.get("background", {})
        if not bg.get("check_enabled", False):
            self.sched_row.set_desc("定时检查已关闭，只能在主界面或托盘手动检查")
            return
        mins = int(bg.get("interval_min", 720) or 720)
        span = f"{mins} 分钟" if mins < 60 else f"{mins // 60} 小时"
        self.sched_row.set_desc(f"每 {span}自动检查一次；关闭主窗口不会退出程序")

    def _save_background(self, *_):
        bg = self.cfg.setdefault("background", {})
        bg["close_to_tray"] = self.tray_toggle.isChecked()
        bg["check_enabled"] = self.sched_toggle.isChecked()
        try:
            bg["interval_min"] = int(self.interval_combo.value() or 720)
        except Exception:
            bg["interval_min"] = 720
        if hasattr(self, "auto_toggle"):
            bg["auto_notify"] = self.auto_toggle.isChecked()
        self.interval_combo.setEnabled(bg["check_enabled"])
        self._refresh_bg_hint()
        self.config_changed.emit()

    def refresh_background(self) -> None:
        self.startup_toggle.blockSignals(True)
        self.startup_toggle.setChecked(autostart.is_enabled())
        self.startup_toggle.blockSignals(False)

    # ---------- 通知 ----------
    def _build_dingtalk(self, lay: QVBoxLayout) -> None:
        ding = self.cfg.setdefault("dingtalk", {})
        bg = self.cfg.setdefault("background", {})
        self._group(lay, "通知")

        chans = core.get_notify_channels(self.cfg)
        # 可多选：钉钉机器人 / Windows 系统通知，全不选即不通知
        holder = QWidget()
        flat(holder)
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(18)
        self.ding_toggle = ToggleSwitch("dingtalk" in chans)
        self.win_toggle = ToggleSwitch("windows" in chans)
        self.ding_toggle.toggled.connect(self._save_ding)
        self.win_toggle.toggled.connect(self._save_ding)
        for lbl, tgl in (("钉钉机器人", self.ding_toggle),
                         ("Windows 系统通知", self.win_toggle)):
            cell = QWidget()
            flat(cell)
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(7)
            cl.addWidget(_label(lbl, 12, "text"))
            cl.addWidget(tgl)
            hl.addWidget(cell)
        hl.addStretch(1)
        self._row(lay, "notify", "通知方式",
                  "检查结果推送到哪里，可同时开启多种；全关即不通知",
                  holder)

        # 以下行按通知方式条件显示
        self.auto_toggle = ToggleSwitch(bool(bg.get("auto_notify", True)))
        self.auto_toggle.toggled.connect(self._save_ding)
        self.auto_row = self._row(
            lay, "clock", "检查后自动发送",
            "定时或手动检查结束后，自动把结果发送到所有开启的通知方式；关闭后只能手动发送",
            self.auto_toggle)

        self.webhook_edit = SecretEdit(ding.get("webhook", ""),
                                       "https://oapi.dingtalk.com/robot/send?access_token=…")
        self.webhook_edit.edit.editingFinished.connect(self._save_ding)
        self.webhook_row = self._row(lay, "link", "Webhook 端点",
                                     "在钉钉群机器人设置里获取 access_token 链接",
                                     self.webhook_edit)

        self.secret_edit = SecretEdit(ding.get("secret", ""), "SEC…（加签密钥）")
        self.secret_edit.edit.editingFinished.connect(self._save_ding)
        self.secret_row = self._row(lay, "save", "加签密钥",
                                    "安全设置里选择「加签」后得到的 SEC 开头字符串",
                                    self.secret_edit)

        self.test_lbl = _label("", 11, "text_muted", wrap=True)
        test_btn = QPushButton("发送测试")
        test_btn.setObjectName("soft")
        test_btn.setCursor(Qt.PointingHandCursor)
        test_btn.setFixedHeight(30)
        test_btn.clicked.connect(self.test_ding_requested.emit)
        self.test_row = self._row(lay, "rocket", "连通性测试",
                                  "发一条测试消息验证配置", test_btn, self.test_lbl)

        self._ding_rows = (self.webhook_row, self.secret_row, self.test_row)
        self._notify_rows = (self.auto_row,) + self._ding_rows
        self._update_ding_visible()

    def _update_ding_visible(self) -> None:
        chans = core.get_notify_channels(self.cfg)
        ding = "dingtalk" in chans
        for row in self._ding_rows:
            row.setVisible(ding)
        self.auto_row.setVisible(bool(chans))

    def set_test_result(self, ok: bool, info: str) -> None:
        if ok is None:                       # 发送中
            self.test_lbl.setText(info)
            return
        self.test_lbl.setText(("✅ 测试消息已发送，去群里看看" if ok
                               else f"❌ 发送失败：{info}") if info else "")

    def _save_ding(self, *_):
        ding = self.cfg.setdefault("dingtalk", {})
        chans = [c for c, on in (("dingtalk", self.ding_toggle.isChecked()),
                                 ("windows", self.win_toggle.isChecked())) if on]
        ding["channels"] = chans
        ding["enabled"] = "dingtalk" in chans
        ding["channel"] = chans[0] if chans else "none"
        ding["webhook"] = self.webhook_edit.text().strip()
        ding["secret"] = self.secret_edit.text().strip()
        self.cfg.setdefault("background", {})["auto_notify"] = self.auto_toggle.isChecked()
        self._update_ding_visible()
        self.config_changed.emit()

    # ---------- 检查参数 ----------
    def _build_check(self, lay: QVBoxLayout) -> None:
        ck = self.cfg.setdefault("check", {})
        self._group(lay, "检查参数")

        self.cache_edit = QLineEdit(ck.get("cache_dir") or core.default_cache_root())
        self.cache_edit.setMinimumWidth(330)
        self.cache_edit.editingFinished.connect(self._save_cache)
        self.cache_row = self._row(
            lay, "folder", "检测数据目录",
            "无错天数状态与 DailyCheck.txt 报告存放处；默认在程序根目录",
            self.cache_edit)

        self.streak_toggle = ToggleSwitch(core.streak_check_enabled(self.cfg))
        self.streak_toggle.toggled.connect(self._save_check)
        self._row(lay, "check", "启用无错天数",
                  "关闭后不再累加 / 清零连续无错天数，通知里也不再列出天数；"
                  "状态文件保留，重新开启可接着算",
                  self.streak_toggle)

        self.err_toggle = ToggleSwitch(core.error_check_enabled(self.cfg))
        self.err_toggle.toggled.connect(self._save_check)
        self._row(lay, "check", "检测 on_error / screencap 文件夹",
                  "开启后扫描设置的扫描目录树（在「扫描目录」里配置），发现异常文件夹即标记对应游戏并清零无错天数；"
                  "关闭后跳过文件夹异常检测（日常失败仍会清零）",
                  self.err_toggle)

        # 「启用日志阈值检查」与「默认日志阈值」已搬到「判定规则 · 程序检查规则」页，
        # 和每行自己的阈值放在一起；设置页不再重复。
        scan_btn = self._small_btn("管理…", self.scan_dir_manage_requested.emit)
        self.scan_row = self._row(lay, "folder", "扫描目录",
                                  self._scan_desc(), scan_btn)
        self.scan_row.setToolTip("可以加多个根目录（比如别的盘），也可以排除其中的子目录")

        excluded = core.get_excluded(self.cfg)
        excl_btn = self._small_btn("管理…", self.exclude_manage_requested.emit)
        self.excl_row = self._row(
            lay, "delete", "排除的游戏",
            "、".join(excluded) if excluded else "暂无排除，全部游戏都参与检查",
            excl_btn)

    def _scan_desc(self) -> str:
        dirs = core.get_scan_dirs(self.cfg)
        excl = core.get_scan_excludes(self.cfg)
        if not dirs:
            return "还没有扫描目录；异常监控会跳过，点右侧「管理…」添加"
        names = "、".join(os.path.basename(os.path.normpath(d)) or d for d in dirs)
        tail = f"；已排除 {len(excl)} 项" if excl else ""
        return f"共 {len(dirs)} 个目录：{names}{tail}"

    def refresh_check(self) -> None:
        """右键改了排除名单等之后，刷新设置页显示。"""
        self.err_toggle.blockSignals(True)
        self.err_toggle.setChecked(core.error_check_enabled(self.cfg))
        self.err_toggle.blockSignals(False)
        self.streak_toggle.blockSignals(True)
        self.streak_toggle.setChecked(core.streak_check_enabled(self.cfg))
        self.streak_toggle.blockSignals(False)
        excluded = core.get_excluded(self.cfg)
        self.excl_row.set_desc("、".join(excluded) if excluded
                               else "暂无排除，全部游戏都参与检查")
        if hasattr(self, "scan_row"):
            self.scan_row.set_desc(self._scan_desc())
        ck = self.cfg.get("check") or {}
        self.cache_edit.setText(ck.get("cache_dir") or core.default_cache_root())

    def _save_check(self, *_):
        ck = self.cfg.setdefault("check", {})
        ck["error_check"] = self.err_toggle.isChecked()
        ck["streak_check"] = self.streak_toggle.isChecked()
        self.config_changed.emit()

    def _save_cache(self, *_):
        ck = self.cfg.setdefault("check", {})
        text = self.cache_edit.text().strip()
        ck["cache_dir"] = text if text and os.path.isabs(text) else ""
        self.cache_edit.setText(ck["cache_dir"] or core.default_cache_root())
        self.config_changed.emit()

    # ---------- 关于 ----------
    def _build_about(self, lay: QVBoxLayout) -> None:
        self._group(lay, "关于")
        self._row(lay, "info", f"{core.APP_NAME}", f"版本 v{core.APP_VERSION}")
        self._row(lay, "folder", "配置目录", core.cache_dir(),
                  self._small_btn("打开", lambda: core.open_in_explorer(core.cache_dir())))
        self._row(lay, "check", "检查范围",
                  f"日常检测 {len(core.all_games(self.cfg))} 款游戏 · "
                  f"异常监控 {len(core.get_scan_dirs(self.cfg))} 个目录")
        self._row(lay, "apps", "快捷键", "Ctrl+1/2/3/4 切换页面　F5 开始检查")

    def _small_btn(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("ghost")
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(30)
        b.clicked.connect(slot)
        return b

    def _open_cache(self) -> None:
        target = os.path.normpath((self.cfg.get("check") or {}).get("cache_dir")
                                  or core.default_cache_root())
        os.makedirs(target, exist_ok=True)
        core.open_in_explorer(target)

    # ---------- 主题刷新 ----------
    def refresh_theme(self) -> None:
        self._style_segmented()
        p = THEME.p
        for card in self.findChildren(Card):
            card.update()
        for tab in (self.tab_general, self.tab_about):
            tab._apply()
        # 下拉框 / 筛选胶囊内部用了内联样式，theme 切换后必须手动重刷，否则残留旧配色
        for combo in self.findChildren(ThemedCombo):
            combo.refresh_theme()
        for chip in self.findChildren(Chip):
            chip.refresh_theme()
        # 卡片标题/说明/图标、分组标题也是内联 tint 着色，必须重刷，
        # 否则从深色切到浅色后会出现「浅色卡片 + 残留的深色浅字」看不清
        for row in self.findChildren(SettingRow):
            row.refresh_theme()
        for grp in self.findChildren(SettingGroup):
            grp.refresh_theme()
        # 通用兜底：_label() 生成的静态标签也带内联 tint，按记录的颜色键统一重刷
        for w in self.findChildren(QWidget):
            key = w.property("_fg_key")
            if key:
                tint(w, getattr(p, key, p.text))
