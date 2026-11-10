# -*- coding: utf-8 -*-
"""自定义控件：玻璃卡片、开关、徽章、筛选胶囊、程序行、标题栏等。"""

from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QEventLoop, QPoint, QPointF, QPropertyAnimation, QRectF,
                            QSize, Qt, QTimer, Property, Signal, QObject)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath,
                           QPen, QDrag)
from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

import core

from backdrop import BACKDROP, paint_glass
from theme import (GLYPH, THEME, badge_fg, card_color, glyph_font, qc, rgba, scoped, tint)


class _MenuAction(QObject):
    """自绘菜单的动作项（对齐 QMenu.addAction 的常用接口）。"""

    triggered = Signal()
    toggled = Signal(bool)

    def __init__(self, text: str = "", separator: bool = False):
        super().__init__()
        self.text = text
        self.separator = separator
        self.enabled = True
        self.visible = True
        self.checkable = False
        self.checked = False

    def setEnabled(self, on: bool) -> None:
        self.enabled = on

    def setVisible(self, on: bool) -> None:
        self.visible = on

    def setText(self, text: str) -> None:
        self.text = text

    def setCheckable(self, on: bool) -> None:
        self.checkable = on

    def setChecked(self, on: bool) -> None:
        if self.checked == on:
            return
        self.checked = on
        if self.checkable:
            self.toggled.emit(on)


class ThemedMenu(QWidget):
    """全自绘的玻璃菜单：底板模糊背后真实画面，悬停高亮自己跟踪、移开即清除。

    不用原生 QMenu —— 半透明 + 无边框模式下它会留下悬停残影，
    且和自绘玻璃叠不出正确效果。
    """

    ITEM_H = 30
    MARGIN = 7
    SEP_V = 5  # 分隔线上下留白

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._actions: list[_MenuAction] = []
        self._hover = -1
        self._rects: list[tuple[_MenuAction, QRectF]] = []
        self._result: _MenuAction | None = None
        self._loop: QEventLoop | None = None

    # ---------- 构建菜单（QMenu 风格） ----------
    def addAction(self, text: str) -> _MenuAction:
        act = _MenuAction(text)
        self._actions.append(act)
        return act

    def addSeparator(self) -> _MenuAction:
        act = _MenuAction(separator=True)
        self._actions.append(act)
        return act

    def _live_actions(self) -> list[_MenuAction]:
        return [a for a in self._actions if a.visible and not a.separator]

    # ---------- 布局 ----------
    def _font(self) -> QFont:
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(12)
        return f

    def _compute_size(self) -> None:
        fm = QFontMetrics(self._font())
        widest = 0
        for a in self._actions:
            if a.visible and not a.separator:
                widest = max(widest, fm.horizontalAdvance(a.text))
        self._w = max(168, widest + self.MARGIN * 2 + 12 + 24)
        n_items = len(self._live_actions())
        n_sep = sum(1 for a in self._actions if a.visible and a.separator)
        self._h = self.MARGIN * 2 + n_items * self.ITEM_H + (n_sep * 2 * self.SEP_V + n_sep)
        self.setFixedSize(self._w, self._h)

    def _layout_items(self) -> None:
        self._rects = []
        y = float(self.MARGIN)
        for a in self._actions:
            if not a.visible:
                continue
            if a.separator:
                y += self.SEP_V
                self._rects.append((a, QRectF(self.MARGIN + 4, y + 0.5, self._w - self.MARGIN * 2 - 8, 1)))
                y += 1 + self.SEP_V
            else:
                self._rects.append((a, QRectF(self.MARGIN, y, self._w - self.MARGIN * 2, self.ITEM_H)))
                y += self.ITEM_H

    def _action_at(self, pos: QPoint) -> _MenuAction | None:
        for a, r in self._rects:
            if a.separator or not a.visible:
                continue
            if r.contains(QPointF(pos)):
                return a
        return None

    def _index_at(self, pos: QPoint) -> int:
        for i, (a, r) in enumerate(self._rects):
            if a.separator or not a.visible:
                continue
            if r.contains(QPointF(pos)):
                return i
        return -1

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing | QPainter.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = max(8, min(p.radius_btn + 2, 14))
        path = paint_glass(painter, rect, radius, self.mapToGlobal(QPoint(0, 0)), p,
                           THEME.blur_active)
        painter.setPen(QPen(qc(p.border, 0.20), 1))
        painter.drawPath(path)

        self._layout_items()
        painter.setFont(self._font())
        for i, (a, r) in enumerate(self._rects):
            if a.separator:
                painter.setPen(QPen(qc(p.border, 0.13), 1))
                painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.top()))
                continue
            hovered = i == self._hover and a.enabled
            if hovered:
                painter.setPen(Qt.NoPen)
                painter.setBrush(qc(p.accent, 0.16))
                painter.drawRoundedRect(r, 7, 7)
            painter.setPen(qc(p.accent if hovered else (p.text if a.enabled else p.text_muted)))
            tx = r.left() + 12
            if a.checkable:
                if a.checked:
                    painter.drawText(QRectF(r.left(), r.top(), 12, r.height()),
                                     Qt.AlignCenter, "✓")
                tx = r.left() + 24
            painter.drawText(QRectF(tx, r.top(), r.right() - tx, r.height()),
                             Qt.AlignVCenter | Qt.AlignLeft, a.text)
        painter.end()

    # ---------- 交互 ----------
    def mouseMoveEvent(self, e):
        idx = self._index_at(e.position().toPoint())
        if idx != self._hover:
            self._hover = idx
            self.update()          # 全量重绘，旧高亮立即消失
        self.setCursor(Qt.PointingHandCursor if self._hover >= 0 else Qt.ArrowCursor)

    def leaveEvent(self, e):
        if self._hover != -1:
            self._hover = -1
            self.update()

    def mousePressEvent(self, e):
        act = self._action_at(e.position().toPoint())
        if act is None:
            # Popup 会抓取鼠标：点击菜单外的按下事件也会投递到这里（坐标在边界外），
            # 必须交给基类处理，它负责把点在弹层外面的情况关闭（否则菜单永远不消失）
            super().mousePressEvent(e)
            return
        if not act.enabled:
            return                 # 禁用项：不关闭、不触发
        if act.checkable:
            act.setChecked(not act.checked)
        self._result = act
        act.triggered.emit()
        self.close()

    def hideEvent(self, e):
        if self._loop is not None:
            self._loop.quit()
        super().hideEvent(e)

    # ---------- 弹出 ----------
    def exec(self, global_pos) -> _MenuAction | None:
        """弹出并阻塞，返回被点中的动作（未选择返回 None），同 QMenu.exec。"""
        self._result = None
        self._place(global_pos)
        BACKDROP.snapshot(self.screen())   # 先取样，才模糊「背后真实内容」
        self.show()
        self._loop = QEventLoop()
        self._loop.exec()
        self._loop = None
        return self._result

    def popup_at(self, global_pos) -> None:
        """直接弹出，不阻塞（托盘菜单用，走动作信号）。"""
        self._place(global_pos)
        BACKDROP.snapshot(self.screen())
        self.show()

    def _place(self, global_pos) -> None:
        self._compute_size()
        self._hover = -1
        pos = QPointF(global_pos).toPoint()
        screen = QApplication.screenAt(pos + QPoint(8, 8)) or QApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            pos.setX(max(g.left(), min(pos.x(), g.right() - self._w - 4)))
            pos.setY(max(g.top(), min(pos.y(), g.bottom() - self._h - 4)))
        self.move(pos)


class Glyph(QLabel):
    """Segoe Fluent 图标。"""

    def __init__(self, name: str, size: int = 16, color: str | None = None, parent=None):
        super().__init__(parent)
        self._size = size
        self._color = color
        self.setText(GLYPH.get(name, name))
        f = glyph_font(size)
        self.setFont(f)
        self.setFixedSize(size + 6, size + 6)
        self.setAlignment(Qt.AlignCenter)
        tint(self, color or THEME.p.text_dim)

    def set_color(self, color: str) -> None:
        self._color = color
        tint(self, color)


class WrapLabel(QLabel):
    """会自动换行并按宽度撑高的 QLabel。

    Qt 里 QLabel 虽然实现了 heightForWidth，但布局经常只按单行高度分配空间，
    窗口一窄、文字折成两行，第二行就被上下裁掉（只剩半截字）。这里在每次改宽度时
    把 minimumHeight 同步成 heightForWidth，布局就压不扁它了。
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        sp = self.sizePolicy()
        sp.setHeightForWidth(True)
        sp.setVerticalPolicy(QSizePolicy.Minimum)
        self.setSizePolicy(sp)

    def _sync_height(self) -> None:
        w = self.width()
        if w <= 0:
            return
        h = self.heightForWidth(w)
        if h > 0 and self.minimumHeight() != h:
            self.setMinimumHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_height()

    def setText(self, text: str) -> None:
        super().setText(text)
        self._sync_height()


class Card(QFrame):
    """圆角面板。hover=True 时鼠标悬停会轻微提亮。"""

    clicked = Signal()

    def __init__(self, parent=None, padding: int = 16, hover: bool = False, radius: int | None = None):
        super().__init__(parent)
        self.padding = padding
        self._hover = False
        self._hoverable = hover
        self._radius = radius
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setMouseTracking(True)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding, padding, padding)
        self.lay.setSpacing(10)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    def radius(self) -> int:
        return self._radius if self._radius is not None else THEME.p.radius_card

    def enterEvent(self, e):
        if self._hoverable:
            self._hover = True
            self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self._hoverable:
            self._hover = False
            self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._hoverable and e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = self.radius()

        base = card_color(p, hover=self._hover)

        path = QPainterPath()
        path.addRoundedRect(rect, rad, rad)
        painter.fillPath(path, base)

        # 深色玻璃加一层顶部高光，避免平
        if p.dark:
            g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            g.setColorAt(0.0, QColor(255, 255, 255, 16))
            g.setColorAt(0.35, QColor(255, 255, 255, 4))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(path, g)

        painter.setPen(QPen(qc(p.border, p.border_alpha), 1))
        painter.drawPath(path)
        painter.end()


class NavButton(QWidget):
    """侧边栏导航项。sub=True 为二级子项；chevron=True 时右侧画展开箭头。"""

    clicked = Signal(str)

    def __init__(self, key: str, icon: str, text: str, parent=None,
                 sub: bool = False, chevron: bool = False):
        super().__init__(parent)
        self.key = key
        self.icon_name = icon
        self.text = text
        self.active = False
        self.sub = sub
        self.chevron = chevron
        self.expanded = False
        self._hover = False
        self.setFixedHeight(34 if sub else 42)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(text)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self.key)
            return
        super().keyPressEvent(e)

    def set_active(self, on: bool) -> None:
        if self.active != on:
            self.active = on
            self.update()

    def set_expanded(self, on: bool) -> None:
        if self.expanded != on:
            self.expanded = on
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.key)

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        left = 24 if self.sub else 8
        rect = QRectF(self.rect()).adjusted(left, 2, -8, -2)

        if self.active:
            accent = qc(p.accent, 0.16 if p.dark else 0.13)
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(rect, p.radius_btn + 2, p.radius_btn + 2)
            if not self.sub:
                bar = QRectF(rect.left() - 8, rect.center().y() - 9, 3, 18)
                painter.setBrush(qc(p.accent, 0.95))
                painter.drawRoundedRect(bar, 1.5, 1.5)
        elif self._hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(qc(p.text, 0.055))
            painter.drawRoundedRect(rect, p.radius_btn + 2, p.radius_btn + 2)

        color = p.text if self.active else (p.text if self._hover else p.text_dim)

        if self.sub:
            # 二级项：左侧一个小圆点，不再放图标，避免和一级项抢视线
            painter.setPen(Qt.NoPen)
            painter.setBrush(qc(p.accent if self.active else p.text_muted, 0.9))
            cy = rect.center().y()
            painter.drawEllipse(QRectF(rect.left() + 12, cy - 2, 4, 4))
        else:
            f = glyph_font(17)
            painter.setFont(f)
            painter.setPen(QPen(qc(color if self.active else p.text_muted)))
            painter.drawText(QRectF(rect.left() + 12, rect.top(), 24, rect.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, GLYPH.get(self.icon_name, ""))

        f2 = QFont("Microsoft YaHei UI")
        f2.setPixelSize(13 if self.sub else 14)
        f2.setWeight(QFont.DemiBold if self.active else QFont.Normal)
        painter.setFont(f2)
        painter.setPen(QPen(qc(color)))
        text_left = 26 if self.sub else 44
        painter.drawText(rect.adjusted(text_left, 0, -18 if self.chevron else -8, 0),
                         Qt.AlignLeft | Qt.AlignVCenter, self.text)

        if self.chevron:
            cx = rect.right() - 10
            cy = rect.center().y()
            path = QPainterPath()
            if self.expanded:
                path.moveTo(cx - 4, cy - 1.5)
                path.lineTo(cx, cy + 2.5)
                path.lineTo(cx + 4, cy - 1.5)
            else:
                path.moveTo(cx - 2, cy - 4)
                path.lineTo(cx + 2, cy)
                path.lineTo(cx - 2, cy + 4)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(qc(p.text_muted), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)
        painter.end()


class ToggleSwitch(QWidget):
    """iOS / 澎湃风格开关。"""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._pos = 1.0 if checked else 0.0
        self.setFixedSize(44, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.setChecked(not self._checked, emit=True)
            return
        super().keyPressEvent(e)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool, emit: bool = False) -> None:
        if self._checked == on:
            return
        self._checked = on
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()
        if emit:
            self.toggled.emit(on)

    def get_knob(self) -> float:
        return self._pos

    def set_knob(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, get_knob, set_knob)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setChecked(not self._checked, emit=True)

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())
        off = qc(p.text, 0.16 if p.dark else 0.14)
        on = qc(p.accent, 0.95)
        painter.setPen(Qt.NoPen)
        painter.setBrush(on if self._checked else off)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        d = rect.height() - 6
        x = 3 + self._pos * (rect.width() - d - 6)
        painter.setBrush(qc("#FFFFFF", 0.98) if self._checked else qc(p.text, 0.75))
        painter.drawEllipse(QRectF(x, 3, d, d))
        painter.end()


class Badge(QWidget):
    """小标签：来源、状态。

    自绘而不是靠 QLabel 的 padding —— 布局挤压时 QLabel 的底色会被裁掉，
    出现「背景没包住文字」；这里按字体度量算宽度，底色必定精确包裹。
    """

    H = 20
    PAD_X = 8

    def __init__(self, text: str = "", color: str = "dim", parent=None):
        super().__init__(parent)
        self._text = ""
        self._color = color
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(11)
        f.setWeight(QFont.DemiBold)
        self.setFont(f)
        self.setFixedHeight(self.H)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._sync(text)

    # ---------- 属性 ----------
    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        self._sync(text or "")

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def _resolved(self) -> str:
        p = THEME.p
        return {"dim": p.text_muted, "accent": p.accent, "success": p.success,
                "warning": p.warning, "danger": p.danger, "text": p.text_dim}.get(self._color,
                                                                                  self._color)

    def _sync(self, text: str) -> None:
        self._text = text
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(text) + self.PAD_X * 2 if text else 0
        self.setFixedWidth(w)
        self.setVisible(bool(text))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.width(), self.H)

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        if not self._text:
            return
        col = QColor(self._resolved())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 6, 6)
        bg = QColor(col)
        bg.setAlphaF(0.16)
        painter.fillPath(path, bg)
        painter.setFont(self.font())
        painter.setPen(qc(badge_fg(col.name(), THEME.p.dark), 0.95))
        painter.drawText(self.rect(), Qt.AlignCenter, self._text)
        painter.end()


class Chip(QPushButton):
    """筛选胶囊，带计数。"""

    def __init__(self, text: str, count: int | None = None, parent=None):
        super().__init__(parent)
        self._text = text
        self._count = count
        self._active = False
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(False)
        self.setFixedHeight(32)          # 与搜索框、下拉框统一高度
        self._apply()

    def set_active(self, on: bool) -> None:
        self._active = on
        self._apply()

    def is_active(self) -> bool:
        return self._active

    def restyle(self) -> None:
        """主题 / 调色板变化后按当前配色重新应用样式。"""
        self._apply()

    def refresh_theme(self) -> None:
        """主题切换后重刷配色（与 ThemedCombo/SearchBox 保持同名协议）。"""
        self._apply()

    def set_count(self, n: int) -> None:
        self._count = n
        self._apply()

    def _apply(self) -> None:
        p = THEME.p
        label = self._text
        if self._count is not None:
            label += f"  {self._count}"
        self.setText(label)
        # 圆角超过高度一半时 Qt 会画成直角，这里统一夹住
        r = max(8, min(p.radius_chip, 16))
        if self._active:
            scoped(self,
                   f"background: {rgba(p.accent, 0.95)}; color: {p.accent_on};"
                   f"border: none; border-radius: {r}px; padding: 0 14px;"
                   f"font-size: 12px; font-weight: 600;")
        else:
            scoped(self,
                   f"background: {rgba(p.text, 0.08)}; color: {rgba(p.text, 0.85)};"
                   f"border: none; border-radius: {r}px; padding: 0 14px;"
                   f"font-size: 12px; font-weight: 500;")
            self.setCursor(Qt.PointingHandCursor)


class SearchBox(QLineEdit):
    """搜索框：图标 + 自绘占位文字（保证占位文字和图标同一深浅、同一中线）。"""

    def __init__(self, placeholder: str = "搜索程序…", parent=None):
        super().__init__(parent)
        self.setObjectName("search")
        self._ph = placeholder
        self.setPlaceholderText("")
        self.setFixedHeight(32)
        self.setFont(self._font())
        self._icon = Glyph("search", 14, THEME.p.text_muted, self)
        self._icon.setFixedSize(16, 16)
        self._icon.move(12, 8)
        self._icon.setAttribute(Qt.WA_TransparentForMouseEvents)

    @staticmethod
    def _font() -> QFont:
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(13)
        return f

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.text() or not self._ph:
            return
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setFont(self.font())
        painter.setPen(QPen(qc(p.text_muted, 0.95)))
        painter.drawText(self.rect().adjusted(34, 0, -12, 0),
                         Qt.AlignLeft | Qt.AlignVCenter, self._ph)
        painter.end()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._icon.setFixedSize(16, 16)
        self._icon.move(12, (self.height() - 16) // 2)

    def refresh_theme(self) -> None:
        self._icon.set_color(THEME.p.text_muted)
        self.update()


class StatTile(QWidget):
    """数字 + 说明的小块。"""

    def __init__(self, value: str, label: str, color_key: str = "text", parent=None):
        super().__init__(parent)
        self.value_text = value
        self.label = label
        self.color_key = color_key
        self.setMinimumHeight(62)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self.v = QLabel(value)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(22)
        f.setWeight(QFont.DemiBold)
        self.v.setFont(f)
        self.l = QLabel(label)
        f2 = QFont("Microsoft YaHei UI")
        f2.setPixelSize(11)
        self.l.setFont(f2)
        lay.addWidget(self.v)
        lay.addWidget(self.l)
        self.refresh()

    def set_values(self, value: str, label: str | None = None) -> None:
        self.value_text = value
        if label is not None:
            self.label = label
        self.refresh()

    def refresh(self) -> None:
        p = THEME.p
        col = getattr(p, self.color_key, p.text)
        self.v.setText(self.value_text)
        tint(self.v, col)
        self.l.setText(self.label)
        tint(self.l, p.text_dim)

    def paintEvent(self, e):
        p = THEME.p
        col = getattr(p, self.color_key, p.text)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                            p.radius_card - 4, p.radius_card - 4)
        # 彩色淡底 + 同色描边，让统计磁贴更有层次
        painter.fillPath(path, qc(col, 0.10 if not p.dark else 0.16))
        painter.setPen(QPen(qc(col, 0.22), 1))
        painter.drawPath(path)
        painter.end()


class ProgressLine(QWidget):
    """细进度条，带流光。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._v = 0.0
        self.setFixedHeight(6)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._phase = 0.0
        self._indeterminate = False

    def set_value(self, v: float) -> None:
        self._v = max(0.0, min(1.0, v))
        self.update()

    def set_indeterminate(self, on: bool) -> None:
        self._indeterminate = on
        if on:
            self._timer.start(40)
        else:
            self._timer.stop()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.02) % 1.0
        self.update()

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        painter.setPen(Qt.NoPen)
        painter.setBrush(qc(p.text, 0.10))
        painter.drawRoundedRect(r, 3, 3)
        if self._indeterminate:
            w = r.width() * 0.3
            x = r.width() * self._phase * 1.4 - w
            g = QLinearGradient(x, 0, x + w, 0)
            g.setColorAt(0, qc(p.accent, 0.0))
            g.setColorAt(0.5, qc(p.accent, 0.95))
            g.setColorAt(1, qc(p.accent, 0.0))
            painter.setBrush(g)
            painter.drawRoundedRect(QRectF(max(x, 0), 0, min(w, r.width()), r.height()), 3, 3)
        else:
            painter.setBrush(qc(p.accent, 0.95))
            painter.drawRoundedRect(QRectF(0, 0, r.width() * self._v, r.height()), 3, 3)
        painter.end()


class GameRow(QFrame):
    """游戏列表中的一行：日常状态 / 无错天数 / 日志体积 / 异常文件夹标签。"""

    open_requested = Signal(str)        # 打开程序目录（日志所在程序的根目录）
    log_requested = Signal(str)         # 打开日志文件夹
    debug_requested = Signal(str)       # 打开 debug 目录
    threshold_requested = Signal(str)   # 设置日志阈值
    method_requested = Signal(str)      # 设置日常判断方式
    exclude_requested = Signal(str)     # 排除 / 取消排除
    copy_requested = Signal(str)        # 复制日志路径
    dropped_on = Signal(str, str, bool) # (拖拽的游戏名, 目标游戏名, 是否插到前面)

    MIME = "application/x-dc-row"

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self._hover = False
        self._draggable = False          # 手动排序模式下才启用拖拽
        self._press_pos = QPoint()
        self.setMouseTracking(True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        # 左侧：首字方块
        self.avatar = QLabel((item.name or "?")[:1].upper())
        self.avatar.setFixedSize(38, 38)
        self.avatar.setAlignment(Qt.AlignCenter)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(16)
        f.setWeight(QFont.Bold)
        self.avatar.setFont(f)

        # 中间：名称 + 徽章 + 路径
        mid = QVBoxLayout()
        mid.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        self.name_lbl = QLabel(item.name)
        fn = QFont("Microsoft YaHei UI")
        fn.setPixelSize(14)
        fn.setWeight(QFont.DemiBold)
        self.name_lbl.setFont(fn)
        title_row.addWidget(self.name_lbl)

        self.status_badge = Badge("")
        title_row.addWidget(self.status_badge)
        self.streak_badge = Badge("")
        title_row.addWidget(self.streak_badge)
        self.error_badge = Badge("")
        title_row.addWidget(self.error_badge)
        self.susp_badge = Badge("")
        title_row.addWidget(self.susp_badge)
        title_row.addStretch(1)
        mid.addLayout(title_row)

        self.detail_lbl = QLabel()
        fd = QFont("Microsoft YaHei UI")
        fd.setPixelSize(11)
        self.detail_lbl.setFont(fd)
        self.detail_lbl.setMaximumWidth(430)
        mid.addWidget(self.detail_lbl)

        # 右侧：日志体积
        self.size_lbl = QLabel()
        self.size_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.size_lbl.setMinimumWidth(108)
        fv = QFont("Consolas")
        fv.setPixelSize(12)
        self.size_lbl.setFont(fv)

        self.dir_btn = QPushButton("目录")
        self.dir_btn.setObjectName("ghost")
        self.dir_btn.setFixedHeight(30)
        self.dir_btn.setCursor(Qt.PointingHandCursor)
        self.dir_btn.setToolTip("打开日志所在程序的根目录")
        self.dir_btn.clicked.connect(
            lambda: self.open_requested.emit(self.item.key))

        self.log_btn = QPushButton("日志")
        self.log_btn.setObjectName("ghost")
        self.log_btn.setFixedHeight(30)
        self.log_btn.setCursor(Qt.PointingHandCursor)
        self.log_btn.setToolTip("打开存放日志关键字的文件夹")
        self.log_btn.clicked.connect(
            lambda: self.log_requested.emit(self.item.key))

        lay.addWidget(self.avatar)
        lay.addLayout(mid, 1)
        lay.addWidget(self.size_lbl)
        lay.addWidget(self.dir_btn)
        lay.addWidget(self.log_btn)
        self.refresh()

    # ---------- 状态刷新 ----------
    def set_item(self, item) -> None:
        self.item = item
        self.refresh()

    def refresh(self) -> None:
        p = THEME.p
        it = self.item
        self.name_lbl.setText(it.name)
        tint(self.name_lbl, p.text)
        self.detail_lbl.setText(it.detail or it.folder)
        tip = [it.detail or "尚未检查", f"日志目录：{it.folder}"]
        if it.rule:
            tip.append(f"匹配规则：{it.rule}")
        self.detail_lbl.setToolTip("\n".join(tip))
        tint(self.detail_lbl, p.text_muted)
        scoped(self.avatar,
               f"background: {rgba(p.accent, 0.16)}; color: {p.accent};"
               f"border-radius: {p.radius_btn}px;")

        # 日常状态
        if it.excluded:
            self.status_badge.setText("已排除")
            self.status_badge.set_color("dim")
        elif it.state == "running":
            self.status_badge.setText("检查中")
            self.status_badge.set_color("accent")
        elif not it.scanned:
            self.status_badge.setText("未检查")
            self.status_badge.set_color("dim")
        elif it.daily == "done":
            self.status_badge.setText("已完成")
            self.status_badge.set_color("success")
        else:
            self.status_badge.setText("未完成")
            self.status_badge.set_color("danger")

        # 无错天数（仅 My-maawork 游戏有意义，其它隐藏）
        if it.is_maawork and it.scanned and not it.excluded:
            self.streak_badge.setText(f"无错 {it.streak} 天")
            self.streak_badge.set_color("success" if it.streak > 0 else "warning")
        else:
            self.streak_badge.setText("")

        # 异常标签：直接显示检测到的文件夹名（on_error / screencap 等）
        if it.error_flags:
            self.error_badge.setText("、".join(sorted(it.error_flags)))
            self.error_badge.set_color("danger")
        else:
            self.error_badge.setText("")

        # 存疑
        if it.suspicious:
            self.susp_badge.setText("存疑")
            self.susp_badge.set_color("warning")
        else:
            self.susp_badge.setText("")

        # 日志体积
        if it.excluded:
            self.size_lbl.setText("")
        elif it.is_maawork and it.log_size > 0:
            over = it.log_size > it.threshold_mb * 1024 * 1024
            color = p.danger if over else p.text_dim
            self.size_lbl.setText(
                f"<span style='color:{color}'>{core.format_size(it.log_size)}</span>"
                f"<span style='color:{p.text_muted}'> / {it.threshold_mb}MB</span>")
            self.size_lbl.setToolTip(
                f"debug 目录日志合计，超过 {it.threshold_mb}MB 标记为存疑"
                + ("（已超过）" if over else ""))
        elif it.is_maawork and it.scanned:
            self.size_lbl.setText(f"<span style='color:{p.text_muted}'>0 B</span>")
        else:
            self.size_lbl.setText("")
        self.update()

    # ---------- 交互 ----------
    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    # ---------- 拖拽排序（手动模式） ----------
    def set_drag_enabled(self, on: bool) -> None:
        self._draggable = on
        self.setAcceptDrops(on)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (self._draggable and (e.buttons() & Qt.LeftButton)
                and not self._press_pos.isNull()
                and (e.position().toPoint() - self._press_pos).manhattanLength() > 12):
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(self.MIME, self.item.name.encode("utf-8"))
            drag.setMimeData(mime)
            pm = self.grab()
            if not pm.isNull():
                drag.setPixmap(pm.scaledToWidth(320, Qt.SmoothTransformation))
            drag.exec(Qt.MoveAction)
            self._press_pos = QPoint()
        super().mouseMoveEvent(e)

    def dragEnterEvent(self, e):
        if self._draggable and e.mimeData().hasFormat(self.MIME):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._draggable and e.mimeData().hasFormat(self.MIME):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        if not (self._draggable and e.mimeData().hasFormat(self.MIME)):
            e.ignore()
            return
        name = bytes(e.mimeData().data(self.MIME)).decode("utf-8", "ignore")
        before = e.position().toPoint().y() < self.height() // 2
        e.acceptProposedAction()
        self.dropped_on.emit(name, self.item.name, before)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.open_requested.emit(self.item.key)
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        it = self.item
        m = ThemedMenu(self)
        act_open = m.addAction("打开日志文件夹")
        act_debug = None
        if it.is_maawork and not it.excluded:
            act_debug = m.addAction("打开 debug 目录")
        act_thr = None
        if it.is_maawork and not it.excluded:
            act_thr = m.addAction("设置日志阈值…")
        act_method = m.addAction("设置判定规则…")
        cfg_now = core.load_config()
        # 写清楚这是可点的动作，别让「判定规则：xxx」看着像一行只读状态
        act_method.setText(f"修改判定规则…（当前：{core.rule_name_of(cfg_now, it.name)}）")
        m.addSeparator()
        act_excl = m.addAction("取消排除（恢复检查）" if it.excluded
                               else "排除此游戏（不再检查）")
        m.addSeparator()
        act_copy = m.addAction("复制日志路径")

        act = m.exec(e.globalPos())
        if act is None:
            return
        if act is act_open:
            # 「打开日志文件夹」要开日志目录，不是程序根目录
            self.log_requested.emit(it.key)
        elif act_debug is not None and act is act_debug:
            self.debug_requested.emit(it.key)
        elif act_thr is not None and act is act_thr:
            self.threshold_requested.emit(it.key)
        elif act is act_method:
            self.method_requested.emit(it.key)
        elif act is act_excl:
            self.exclude_requested.emit(it.key)
        elif act is act_copy:
            QApplication.clipboard().setText(it.folder)

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path.addRoundedRect(rect, p.radius_card, p.radius_card)
        painter.fillPath(path, card_color(p, hover=self._hover))
        if self.item.state == "running":
            border = qc(p.accent, 0.40)
        elif self.item.excluded:
            border = qc(p.border, p.border_alpha * 0.7)
        elif self.item.has_error or (self.item.scanned and self.item.daily == "failed"):
            border = qc(p.danger, 0.42)
        elif self.item.suspicious:
            border = qc(p.warning, 0.42)
        else:
            border = qc(p.border, p.border_alpha)
        painter.setPen(QPen(border, 1))
        painter.drawPath(path)
        painter.end()


class Toast(QWidget):
    """底部浮层提示。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_StyledBackground, True)   # 否则样式表背景不生效，露出深色调色板
        self.setObjectName("toast")
        self.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        self.lbl = QLabel()
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(12)
        self.lbl.setFont(f)
        lay.addWidget(self.lbl)
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self._anim = QPropertyAnimation(self.effect, b"opacity", self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        # 用可取消的定时器：singleShot 的残留回调会把刚弹出的下一条 Toast 一起隐藏
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(lambda: self.setVisible(False))

    def show_text(self, text: str, ok: bool = True) -> None:
        p = THEME.p
        self.lbl.setText(text)
        tint(self.lbl, p.text)
        border = rgba(p.danger, 0.45) if not ok else rgba(p.border, 0.22)
        # 带 #toast 选择器：无选择器的规则会级联到子 QLabel，给文字画出一圈多余边框
        self.setStyleSheet(
            f"#toast{{background: {rgba(p.surface(), 0.97)};"
            f"border: 1px solid {border}; border-radius: {p.radius_btn + 4}px;}}")
        self.adjustSize()
        self.move((self.parentWidget().width() - self.width()) // 2,
                  self.parentWidget().height() - self.height() - 26)
        self.raise_()                 # 页面重建后可能盖住 Toast，置顶再显示
        self.effect.setOpacity(0.0)
        self.setVisible(True)
        self._hide_timer.stop()
        self._anim.stop()
        self._anim.setDuration(160)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(2600)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setDuration(260)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()
        self._hide_timer.start(280)




class SegmentedControl(QFrame):
    """分段选择器：选中项整块填满（无内圈底色），仅整组外侧四角圆角。"""

    changed = Signal(str)
    HEIGHT = 32

    def __init__(self, items, current: str = "", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.buttons: list[QPushButton] = []
        self.keys: list[str] = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)      # 不留内边距，选中色才能填满
        lay.setSpacing(0)
        for key, text in items:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(key == current)
            b.setProperty("segKey", key)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            lay.addWidget(b)
            self.buttons.append(b)
            self.keys.append(key)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.restyle()

    def _pick(self, key: str) -> None:
        for k, b in zip(self.keys, self.buttons):
            b.setChecked(k == key)
        self.restyle()
        self.changed.emit(key)

    def value(self) -> str:
        for k, b in zip(self.keys, self.buttons):
            if b.isChecked():
                return k
        return self.keys[0] if self.keys else ""

    def set_value(self, key: str) -> None:
        for k, b in zip(self.keys, self.buttons):
            b.setChecked(k == key)
        self.restyle()

    def set_tooltip(self, key: str, text: str) -> None:
        for k, b in zip(self.keys, self.buttons):
            if k == key:
                b.setToolTip(text)

    def _btn_font(self, bold: bool) -> QFont:
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(12)
        f.setWeight(QFont.DemiBold if bold else QFont.Normal)
        return f

    def restyle(self) -> None:
        p = THEME.p
        r = max(8, min(p.radius_btn + 2, 14))
        n = len(self.buttons)
        total = 0
        for b in self.buttons:
            b.setFont(self._btn_font(False))
            fm = QFontMetrics(b.font())
            w = max(60, fm.horizontalAdvance(b.text()) + 34)
            b.setFixedSize(w, self.HEIGHT)   # 尺寸固定，切换皮肤不会跳动
            total += w
        self.setFixedSize(total, self.HEIGHT)
        for i, b in enumerate(self.buttons):
            active = b.isChecked()
            left = (f"border-top-left-radius:{r}px;border-bottom-left-radius:{r}px;"
                    if i == 0 else "border-top-left-radius:0;border-bottom-left-radius:0;")
            right = (f"border-top-right-radius:{r}px;border-bottom-right-radius:{r}px;"
                     if i == n - 1 else "border-top-right-radius:0;border-bottom-right-radius:0;")
            if active:
                bg, fg, fw = rgba(p.accent, 0.98), p.accent_on, "600"
            else:
                bg, fg, fw = "transparent", p.text_dim, "400"
            hover = rgba(p.text, 0.10) if not active else rgba(p.accent_hover, 1.0)
            b.setStyleSheet(
                f"QPushButton{{background:{bg};color:{fg};border:none;{left}{right}"
                f"padding:0;font-size:12px;font-weight:{fw};}}"
                f"QPushButton:hover{{background:{hover};}}")
        self.update()

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        r = max(8, min(p.radius_btn + 2, 14))
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        painter.fillPath(path, qc(p.text, 0.06))
        painter.setPen(QPen(qc(p.border, 0.12 if p.dark else 0.09), 1))
        painter.drawPath(path)
        painter.end()


class _MenuPanel(QFrame):
    """弹出面板的圆角底板（玻璃质感，跟随主题）。"""

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = max(8, min(p.radius_btn + 2, 14))
        path = paint_glass(painter, rect, radius, self.mapToGlobal(QPoint(0, 0)), p,
                           THEME.blur_active)
        painter.setPen(QPen(qc(p.border, 0.20), 1))
        painter.drawPath(path)
        painter.end()


class _PopupCard(QWidget):
    """主题化下拉弹层：圆角卡片 + 悬停高亮 + 选中打勾。"""

    picked = Signal(str)

    def __init__(self, items, current: str, width: int, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(max(width, 132))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)
        panel = _MenuPanel(self)
        outer.addWidget(panel)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.setSpacing(1)
        for key, text in items:
            b = QPushButton(("✓   " if key == current else "      ") + text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(30)
            b.setStyleSheet(self._btn_qss(key == current))
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            lay.addWidget(b)

    def _btn_qss(self, active: bool) -> str:
        p = THEME.p
        fg = p.accent if active else p.text
        return (f"QPushButton{{background:transparent;border:none;border-radius:7px;"
                f"text-align:left;padding:0 10px;font-family:'Microsoft YaHei UI';"
                f"font-size:12px;color:{fg};}}"
                f"QPushButton:hover{{background:{rgba(p.accent, 0.16)};color:{p.accent};}}")

    def _pick(self, key: str) -> None:
        self.picked.emit(key)
        self.close()

    def popup_at(self, anchor: QWidget) -> None:
        gp = anchor.mapToGlobal(QPoint(0, anchor.height() + 6))
        self.adjustSize()
        screen = anchor.screen().availableGeometry() if anchor.screen() else None
        x, y = gp.x(), gp.y()
        if screen is not None:
            if x + self.width() > screen.right():
                x = screen.right() - self.width() - 8
            if y + self.height() > screen.bottom():
                y = anchor.mapToGlobal(QPoint(0, 0)).y() - self.height() - 6
        BACKDROP.snapshot(anchor.screen())      # 先取样，弹层才透出背后画面
        self.move(x, y)
        self.show()


class ThemedCombo(QPushButton):
    """主题化下拉选择（替代原生 QComboBox，避免白底系统菜单）。"""

    changed = Signal(str)

    def __init__(self, items, current: str = "", width: int = 132, parent=None):
        super().__init__(parent)
        self._items = list(items)
        self._map = dict(self._items)
        self._key = current if current in self._map else (self._items[0][0] if self._items else "")
        self._w = width
        self._popup = None
        self.setFixedSize(width, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._open)
        self._restyle()

    def value(self) -> str:
        return self._key

    def set_value(self, key: str, emit: bool = False) -> None:
        if key not in self._map:
            return
        self._key = key
        self._restyle()
        if emit:
            self.changed.emit(key)

    def set_items(self, items) -> None:
        self._items = list(items)
        self._map = dict(self._items)
        if self._key not in self._map and self._items:
            self._key = self._items[0][0]
        self._restyle()

    def _restyle(self) -> None:
        p = THEME.p
        self.setText(self._map.get(self._key, ""))
        self.setStyleSheet(
            f"QPushButton{{background:{rgba(p.text, 0.07)};border:1px solid {rgba(p.border, 0.13)};"
            f"border-radius:8px;color:{p.text_dim};font-family:'Microsoft YaHei UI';"
            f"font-size:12px;text-align:left;padding-left:11px;padding-right:24px;}}"
            f"QPushButton:hover{{background:{rgba(p.text, 0.11)};color:{p.text};}}")

    def refresh_theme(self) -> None:
        """主题切换后重刷配色（与 Chip/SearchBox 保持同名协议）。"""
        self._restyle()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        x = self.width() - 15
        y = self.height() / 2 - 1
        painter.setPen(QPen(qc(p.text_dim, 0.9), 1.4, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(x - 4, y - 1), QPointF(x, y + 3))
        painter.drawLine(QPointF(x, y + 3), QPointF(x + 4, y - 1))
        painter.end()

    def _open(self) -> None:
        self._popup = _PopupCard(self._items, self._key, self.width() + 26, self)
        self._popup.picked.connect(self._on_pick)
        self._popup.popup_at(self)

    def _on_pick(self, key: str) -> None:
        if key == self._key:
            return
        self._key = key
        self._restyle()
        self.changed.emit(key)


class NumberStepper(QWidget):
    """主题化数值步进器：右侧上下两个小箭头（竖向排列），替代原生 QSpinBox。"""

    valueChanged = Signal(int)
    BTN_W = 24

    def __init__(self, value: int = 0, minimum: int = 0, maximum: int = 100,
                 step: int = 1, suffix: str = "", width: int = 104, parent=None):
        super().__init__(parent)
        self._min, self._max, self._step = int(minimum), int(maximum), max(1, int(step))
        self._suffix = suffix
        self._value = max(self._min, min(self._max, int(value)))
        self._zone = 0                      # 0 无 / 1 加 / -1 减
        self.setFixedSize(width, 32)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    # ---------- 值 ----------
    def value(self) -> int:
        return self._value

    def setValue(self, v: int, emit: bool = False) -> None:
        v = max(self._min, min(self._max, int(v)))
        if v == self._value:
            return
        self._value = v
        self.update()
        if emit:
            self.valueChanged.emit(v)

    def setRange(self, lo: int, hi: int) -> None:
        self._min, self._max = int(lo), int(hi)
        self.setValue(self._value)

    # ---------- 几何 ----------
    def _rects(self):
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bw = self.BTN_W
        up = QRectF(r.right() - bw, r.top(), bw, r.height() / 2)
        down = QRectF(r.right() - bw, r.center().y(), bw, r.height() / 2)
        return r, up, down

    def _hit(self, pos) -> int:
        _, up, down = self._rects()
        if up.contains(pos):
            return 1
        if down.contains(pos):
            return -1
        return 0

    # ---------- 交互 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            z = self._hit(e.position())
            if z:
                self.setValue(self._value + z * self._step, emit=True)
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        z = self._hit(e.position())
        if z != self._zone:
            self._zone = z
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._zone = 0
        self.update()
        super().leaveEvent(e)

    def wheelEvent(self, e):
        self.setValue(self._value + (self._step if e.angleDelta().y() > 0 else -self._step),
                      emit=True)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Up, Qt.Key_Right):
            self.setValue(self._value + self._step, emit=True)
        elif e.key() in (Qt.Key_Down, Qt.Key_Left):
            self.setValue(self._value - self._step, emit=True)
        else:
            super().keyPressEvent(e)

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r, up, down = self._rects()
        radius = 8
        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        painter.fillPath(path, qc(p.text, 0.07))
        painter.setPen(QPen(qc(p.border, 0.13), 1))
        painter.drawPath(path)

        # 两个箭头的悬停底色
        painter.setPen(Qt.NoPen)
        for zone, rect in ((1, up), (-1, down)):
            if self._zone == zone:
                hl = QPainterPath()
                hl.addRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 3, radius - 3)
                painter.fillPath(hl, qc(p.accent, 0.16))

        # 分隔线
        painter.setPen(QPen(qc(p.border, 0.12), 1))
        painter.drawLine(QPointF(r.right() - self.BTN_W, r.top() + 4),
                         QPointF(r.right() - self.BTN_W, r.bottom() - 4))

        # 数值
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(12)
        painter.setFont(f)
        painter.setPen(QPen(qc(p.text)))
        painter.drawText(QRectF(r.left() + 11, r.top(), r.width() - self.BTN_W - 14, r.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, f"{self._value}{self._suffix}")

        # 上下箭头
        cx = up.center().x()
        for sign, rect in ((1, up), (-1, down)):
            col = qc(p.accent if self._zone == sign else p.text_dim, 0.95)
            painter.setPen(QPen(col, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            cy = rect.center().y()
            tip = cy - 3.4 * sign
            painter.drawLine(QPointF(cx - 3.6, cy + 1.6 * sign), QPointF(cx, tip))
            painter.drawLine(QPointF(cx, tip), QPointF(cx + 3.6, cy + 1.6 * sign))
        painter.end()


class ChevronButton(QPushButton):
    """自绘展开箭头（不依赖图标字体）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._up = False
        self.setFixedSize(26, 26)
        self.setCursor(Qt.PointingHandCursor)
        scoped(self, "background: transparent; border: none;")

    def set_up(self, on: bool) -> None:
        self._up = on
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.underMouse():
            painter.setPen(Qt.NoPen)
            painter.setBrush(qc(p.text, 0.08))
            painter.drawEllipse(c, 11, 11)
        painter.setPen(QPen(qc(p.text_muted if not self.underMouse() else p.text, 0.95), 1.6,
                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        d = 2.0 if self._up else -2.0
        painter.drawLine(QPointF(c.x() - 4, c.y() + d), QPointF(c.x(), c.y() - d))
        painter.drawLine(QPointF(c.x(), c.y() - d), QPointF(c.x() + 4, c.y() + d))
        painter.end()


class _ClickRow(QWidget):
    """可点击的行容器（点哪都算）。"""

    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)




class SettingRow(Card):
    """设置项行：左图标 + 标题/说明 + 右侧控件，可选下方详细内容区。"""

    def __init__(self, icon: str = "", title: str = "", desc: str = "",
                 control=None, content=None, parent=None):
        super().__init__(parent, padding=0)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        body = QVBoxLayout()
        body.setContentsMargins(16, 13, 16, 13)
        body.setSpacing(10)
        self.lay.addLayout(body)

        line = QHBoxLayout()
        line.setSpacing(13)
        self.icon = None
        if icon:
            self.icon = Glyph(icon, 18, THEME.p.text_dim)
            self.icon.setFixedSize(26, 26)
            line.addWidget(self.icon, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(2)
        self.title_lbl = QLabel(title)
        ft = QFont("Microsoft YaHei UI")
        ft.setPixelSize(13)
        ft.setWeight(QFont.DemiBold)
        self.title_lbl.setFont(ft)
        tint(self.title_lbl, THEME.p.text)
        col.addWidget(self.title_lbl)
        self.desc_lbl = QLabel(desc, self)
        fd = QFont("Microsoft YaHei UI")
        fd.setPixelSize(11)
        self.desc_lbl.setFont(fd)
        self.desc_lbl.setWordWrap(True)
        tint(self.desc_lbl, THEME.p.text_muted)
        col.addWidget(self.desc_lbl)
        self.desc_lbl.setVisible(bool(desc))      # 有父级之后再显示，避免变成顶层小窗
        line.addLayout(col, 1)

        if control is not None:
            line.addWidget(control, 0, Qt.AlignVCenter)
        body.addLayout(line)

        if content is not None:
            body.addWidget(content)
        self.setMinimumHeight(58)

    def set_desc(self, text: str) -> None:
        self.desc_lbl.setText(text)
        self.desc_lbl.setVisible(bool(text))


class SettingGroup(QLabel):
    """设置分组标题（行列表上方的小标题）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(12)
        f.setWeight(QFont.DemiBold)
        self.setFont(f)
        self.setContentsMargins(4, 8, 0, 0)
        tint(self, THEME.p.text_dim)


class TabButton(QPushButton):
    """设置页顶部分页标签（带下划线指示）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setFixedHeight(34)
        self._apply()

    def _apply(self) -> None:
        p = THEME.p
        on = self.isChecked()
        self.setStyleSheet(
            f"QPushButton{{background: transparent; border: none;"
            f" border-bottom: 2px solid {p.accent if on else 'transparent'};"
            f" color: {p.text if on else p.text_muted};"
            f" font-size: 13px; font-weight: {'600' if on else '400'};"
            f" padding: 0 2px 0 2px;}}"
            f"QPushButton:hover{{color: {p.text};}}")

    def set_checked(self, on: bool) -> None:
        self.setChecked(on)
        self._apply()




