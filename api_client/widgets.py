"""Reusable widgets: variable-aware line edit, method picker, spinner, JSON tree, rule table."""
import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PyQt5.QtCore import QRectF, QSize, QStringListModel, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QPen, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextOption
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QCompleter,
    QFrame,
    QHeaderView,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from . import theme
from .engine import SCOPE_LABELS, VAR_RE
from .models import HTTP_METHODS
from .theme import G

Resolver = Callable[[str], Tuple[Optional[str], Optional[str]]]


def hline() -> QFrame:
    line = QFrame()
    line.setObjectName("hline")
    line.setFrameShape(QFrame.NoFrame)
    return line


def vdivider(height: int = 20) -> QFrame:
    line = QFrame()
    line.setObjectName("vDivider")
    line.setFixedSize(1, height)
    return line


def tool_button(glyph: str, tooltip: str = "", size: int = 16, color: str = "muted", text: str = "") -> QToolButton:
    btn = QToolButton()
    btn.setIcon(theme.icon(glyph, color, size))
    btn.setIconSize(QSize(size, size))
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setProperty("glyph", glyph)
    btn.setProperty("glyphColor", color)
    btn.setProperty("glyphSize", size)
    if text:
        btn.setText(text)
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    return btn


def refresh_tool_icons(root: QWidget) -> None:
    """Re-render glyph icons after a theme switch."""
    for btn in root.findChildren(QToolButton):
        glyph = btn.property("glyph")
        if glyph:
            btn.setIcon(theme.icon(glyph, btn.property("glyphColor") or "muted", int(btn.property("glyphSize") or 16)))


def format_size(num: int) -> str:
    if num < 1024:
        return f"{num} B"
    if num < 1024 * 1024:
        return f"{num / 1024:.2f} KB"
    return f"{num / (1024 * 1024):.2f} MB"


def format_ms(ms: float) -> str:
    if ms >= 10000:
        return f"{ms / 1000:.1f} s"
    return f"{int(round(ms))} ms"


# ---------------------------------------------------------------------------
# Variable-aware single line editor
# ---------------------------------------------------------------------------

class _VarHighlighter(QSyntaxHighlighter):
    def __init__(self, document, owner: "VariableLineEdit") -> None:
        super().__init__(document)
        self.owner = owner

    def highlightBlock(self, text: str) -> None:
        for match in VAR_RE.finditer(text):
            fmt = QTextCharFormat()
            resolved = True
            if self.owner.resolver is not None:
                resolved = self.owner.resolver(match.group(1))[0] is not None
            fmt.setForeground(theme.qcolor("var_ok" if resolved else "var_bad"))
            self.setFormat(match.start(), match.end() - match.start(), fmt)
        for match in re.finditer(r"(?<=/):[A-Za-z_][\w\-]*", text.split("?", 1)[0]):
            fmt = QTextCharFormat()
            fmt.setForeground(theme.qcolor("info"))
            self.setFormat(match.start(), match.end() - match.start(), fmt)


class VariableLineEdit(QPlainTextEdit):
    """Single-line editor that highlights {{variables}}, autocompletes them and shows values on hover."""

    returnPressed = pyqtSignal()
    textEdited = pyqtSignal(str)
    curlPasted = pyqtSignal(str)
    focusChanged = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None, placeholder: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("urlEdit")
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setWordWrapMode(QTextOption.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTabChangesFocus(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setPlaceholderText(placeholder)
        self.setMouseTracking(True)
        self.document().setDocumentMargin(2)
        self.resolver: Optional[Resolver] = None
        self._last_text = ""
        self.names_provider: Optional[Callable[[], Dict[str, Tuple[str, str]]]] = None
        self._highlighter = _VarHighlighter(self.document(), self)

        self._model = QStringListModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.activated.connect(self._insert_completion)
        self.textChanged.connect(self._on_text_changed)
        theme.manager().changed.connect(self._refresh_theme)
        self._refresh_theme()

    def _refresh_theme(self, *_args) -> None:
        font = QFont(theme.ui_font_family())
        font.setPixelSize(13)
        self.setFont(font)
        line = self.fontMetrics().height()
        height = line + 16
        self.setFixedHeight(height)
        margin = int(self.document().documentMargin())
        self.setViewportMargins(0, max(0, (height - line - 2 * margin) // 2 - 1), 0, 0)
        self._highlighter.rehighlight()

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:
        if text != self.toPlainText():
            self.blockSignals(True)
            self.setPlainText(text)
            self.blockSignals(False)
            self._last_text = text
            self._highlighter.rehighlight()
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.setTextCursor(cursor)

    def set_resolver(self, resolver: Optional[Resolver]) -> None:
        self.resolver = resolver
        self._highlighter.rehighlight()

    def rehighlight(self) -> None:
        self._highlighter.rehighlight()

    def _on_text_changed(self) -> None:
        text = self.toPlainText()
        if "\n" in text:
            cursor_pos = self.textCursor().position()
            self.blockSignals(True)
            self.setPlainText(text.replace("\r", "").replace("\n", ""))
            self.blockSignals(False)
            cursor = self.textCursor()
            cursor.setPosition(min(cursor_pos, len(self.toPlainText())))
            self.setTextCursor(cursor)
            text = self.toPlainText()
        if text == self._last_text:
            return  # formatting-only change (e.g. re-highlighting)
        self._last_text = text
        self.textEdited.emit(text)
        self._maybe_complete()

    def _prefix_before_cursor(self) -> Optional[str]:
        cursor = self.textCursor()
        before = self.toPlainText()[: cursor.position()]
        match = re.search(r"\{\{([\w\-.$]*)$", before)
        return match.group(1) if match else None

    def _maybe_complete(self) -> None:
        prefix = self._prefix_before_cursor()
        if prefix is None or self.names_provider is None:
            self._completer.popup().hide()
            return
        names = self.names_provider()
        self._model.setStringList(sorted(names.keys(), key=str.lower))
        self._completer.setCompletionPrefix(prefix)
        if self._completer.completionCount() == 0:
            self._completer.popup().hide()
            return
        rect = self.cursorRect()
        rect.setWidth(280)
        self._completer.complete(rect)

    def _insert_completion(self, name: str) -> None:
        prefix = self._prefix_before_cursor() or ""
        cursor = self.textCursor()
        for _ in range(len(prefix)):
            cursor.deletePreviousChar()
        after = self.toPlainText()[cursor.position(): cursor.position() + 2]
        cursor.insertText(name + ("" if after == "}}" else "}}"))
        self.setTextCursor(cursor)

    def keyPressEvent(self, event) -> None:
        popup = self._completer.popup()
        if popup.isVisible() and event.key() in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Escape, Qt.Key_Tab, Qt.Key_Backtab):
            event.ignore()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not event.modifiers() & Qt.ControlModifier:
                self.returnPressed.emit()
            event.ignore() if event.modifiers() & Qt.ControlModifier else event.accept()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source) -> None:
        text = source.text() if source.hasText() else ""
        if text.strip().lower().startswith("curl ") and not self.toPlainText().strip():
            self.curlPasted.emit(text)
            return
        self.textCursor().insertText(text.replace("\r", "").replace("\n", "").strip() if "\n" in text else text)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        cursor = self.cursorForPosition(event.pos())
        pos = cursor.position()
        text = self.toPlainText()
        for match in VAR_RE.finditer(text):
            if match.start() <= pos <= match.end():
                name = match.group(1)
                value, scope = self.resolver(name) if self.resolver else (None, None)
                if value is None:
                    tip = f"<b>{name}</b><br><span style='color:{theme.color('var_bad')}'>Unresolved variable</span><br>Define it in an environment, the collection or globals."
                else:
                    shown = value if len(value) <= 160 else value[:160] + "…"
                    shown = shown.replace("&", "&amp;").replace("<", "&lt;")
                    tip = f"<b>{name}</b><br>{shown}<br><span style='color:{theme.color('muted')}'>{SCOPE_LABELS.get(scope or '', scope)}</span>"
                QToolTip.showText(event.globalPos(), tip, self)
                return
        QToolTip.hideText()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focusChanged.emit(True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focusChanged.emit(False)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)


class MethodCombo(QComboBox):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("methodCombo")
        self.setEditable(False)
        for method in HTTP_METHODS:
            self.addItem(method)
        self.setFixedWidth(112)
        self.setCursor(Qt.PointingHandCursor)
        self.currentTextChanged.connect(self._recolor)
        theme.manager().changed.connect(lambda *_: self._recolor(self.currentText()))
        self._recolor(self.currentText())

    def _recolor(self, method: str) -> None:
        for idx in range(self.count()):
            self.setItemData(idx, QColor(theme.method_color(self.itemText(idx))), Qt.ForegroundRole)
        self.setStyleSheet(f"QComboBox#methodCombo {{ color: {theme.method_color(method)}; }}")

    def set_method(self, method: str) -> None:
        method = (method or "GET").upper()
        idx = self.findText(method)
        if idx < 0:
            self.addItem(method)
            idx = self.count() - 1
        self.setCurrentIndex(idx)


class Spinner(QWidget):
    def __init__(self, parent: Optional[QWidget] = None, size: int = 28) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(3, 3, self.width() - 6, self.height() - 6)
        pen = QPen(theme.qcolor("border_strong"), 3)
        painter.setPen(pen)
        painter.drawEllipse(rect)
        pen.setColor(theme.qcolor("accent"))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, -self._angle * 16, 100 * 16)


def _dot_icon(color_hex: str, size: int = 10) -> QIcon:
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.transparent)
    pix.setDevicePixelRatio(2.0)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color_hex))
    painter.drawEllipse(QRectF(size * 0.2, size * 0.2, size * 0.6, size * 0.6))
    painter.end()
    return QIcon(pix)


class TabCloseButton(QToolButton):
    """Close button that shows a dot while the tab has unsaved changes (Postman style)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("tabClose")
        self.setFixedSize(18, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Close tab (Ctrl+W)")
        self._dirty = False
        self._hover = False
        self._refresh()

    def set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self._refresh()

    def enterEvent(self, event) -> None:
        self._hover = True
        self._refresh()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self._refresh()
        super().leaveEvent(event)

    def _refresh(self) -> None:
        if self._dirty and not self._hover:
            self.setIcon(_dot_icon(theme.color("accent")))
        else:
            self.setIcon(theme.icon(G.CLOSE, "muted", 10))


# ---------------------------------------------------------------------------
# JSON tree
# ---------------------------------------------------------------------------

class JsonTreeView(QTreeWidget):
    _DATA = Qt.UserRole
    _PATH = Qt.UserRole + 1
    _LOADED = Qt.UserRole + 2

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("dataTree")
        self.setColumnCount(3)
        self.setHeaderLabels(["Key", "Value", "Type"])
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.header().setSectionResizeMode(2, QHeaderView.Fixed)
        self.header().setStretchLastSection(False)
        self.setColumnWidth(0, 260)
        self.setColumnWidth(2, 80)
        self.itemExpanded.connect(self._populate)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def load(self, data: Any) -> None:
        self.clear()
        root = QTreeWidgetItem(["$" if isinstance(data, (dict, list)) else "value"])
        self.addTopLevelItem(root)
        self._configure(root, data, "$")
        root.setExpanded(True)

    def _configure(self, item: QTreeWidgetItem, value: Any, path: str) -> None:
        item.setData(0, self._PATH, path)
        item.setFont(0, theme.mono_font())
        item.setFont(1, theme.mono_font())
        if isinstance(value, dict):
            item.setText(1, f"{{{len(value)}}}")
            item.setText(2, "object")
            item.setForeground(1, theme.qcolor("muted"))
        elif isinstance(value, list):
            item.setText(1, f"[{len(value)}]")
            item.setText(2, "array")
            item.setForeground(1, theme.qcolor("muted"))
        else:
            text, color_key, type_name = self._scalar(value)
            item.setText(1, text)
            item.setText(2, type_name)
            item.setForeground(1, theme.qcolor(color_key))
        item.setForeground(0, theme.qcolor("code_key"))
        item.setForeground(2, theme.qcolor("faint"))
        if isinstance(value, (dict, list)) and value:
            item.setData(0, self._DATA, value)
            item.setData(0, self._LOADED, False)
            item.addChild(QTreeWidgetItem(["…"]))

    @staticmethod
    def _scalar(value: Any) -> Tuple[str, str, str]:
        if value is None:
            return "null", "code_keyword", "null"
        if isinstance(value, bool):
            return ("true" if value else "false"), "code_keyword", "boolean"
        if isinstance(value, (int, float)):
            return str(value), "code_number", "number"
        text = json.dumps(value, ensure_ascii=False)
        return (text if len(text) < 2000 else text[:2000] + "…"), "code_string", "string"

    def _populate(self, item: QTreeWidgetItem) -> None:
        if item.data(0, self._LOADED) is not False:
            return
        item.setData(0, self._LOADED, True)
        item.takeChildren()
        value = item.data(0, self._DATA)
        path = item.data(0, self._PATH)
        entries = list(value.items()) if isinstance(value, dict) else list(enumerate(value))
        children = []
        for key, child_value in entries[:5000]:
            if isinstance(value, dict):
                label = str(key)
                child_path = f"{path}.{key}" if re.match(r"^[A-Za-z_]\w*$", str(key)) else f"{path}[{json.dumps(key)}]"
            else:
                label = f"[{key}]"
                child_path = f"{path}[{key}]"
            child = QTreeWidgetItem([label])
            self._configure(child, child_value, child_path)
            children.append(child)
        if len(entries) > 5000:
            children.append(QTreeWidgetItem([f"… {len(entries) - 5000} more items"]))
        item.addChildren(children)

    def _menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        clipboard = QApplication.clipboard()
        value = item.data(0, self._DATA)
        menu.addAction(theme.icon(G.COPY), "Copy value", lambda: clipboard.setText(json.dumps(value, indent=2, ensure_ascii=False) if value is not None else item.text(1).strip('"')))
        menu.addAction("Copy path", lambda: clipboard.setText(item.data(0, self._PATH) or ""))
        menu.addAction("Copy key", lambda: clipboard.setText(item.text(0)))
        menu.addSeparator()
        menu.addAction("Expand children", lambda: self._expand(item, 2))
        menu.addAction("Collapse all", self.collapseAll)
        menu.exec_(self.viewport().mapToGlobal(pos))

    def _expand(self, item: QTreeWidgetItem, depth: int) -> None:
        if depth <= 0:
            return
        item.setExpanded(True)
        for idx in range(item.childCount()):
            self._expand(item.child(idx), depth - 1)


# ---------------------------------------------------------------------------
# Rule table (tests / extractors)
# ---------------------------------------------------------------------------

class _CellEditorDelegate(QStyledItemDelegate):
    def __init__(self, table: "RuleTable") -> None:
        super().__init__(table)
        self.table = table

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setObjectName("cellEditor")
        editor.setFrame(False)
        return editor

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        if index.column() >= len(self.table.columns):
            return
        spec = self.table.columns[index.column()]
        if spec[0] == "text" and not index.data() and index.row() == self.table.rowCount() - 1:
            painter.save()
            painter.setPen(theme.qcolor("faint"))
            painter.drawText(option.rect.adjusted(8, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft, spec[2] or "")
            painter.restore()


class RuleTable(QTableWidget):
    """Table whose columns are ("check"|"combo"|"text", header, options_or_placeholder)."""

    changed = pyqtSignal()

    def __init__(self, columns: Sequence[Tuple[str, str, Any]], parent: Optional[QWidget] = None) -> None:
        super().__init__(0, len(columns) + 1, parent)
        self.columns = list(columns)
        self._updating = False
        self.setHorizontalHeaderLabels([c[1] for c in self.columns] + [""])
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(32)
        self.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setItemDelegate(_CellEditorDelegate(self))
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setHighlightSections(False)
        for idx, (kind, _h, _o) in enumerate(self.columns):
            if kind == "check":
                header.setSectionResizeMode(idx, QHeaderView.Fixed)
                self.setColumnWidth(idx, 34)
            elif kind == "combo":
                header.setSectionResizeMode(idx, QHeaderView.Fixed)
                self.setColumnWidth(idx, 150)
            else:
                header.setSectionResizeMode(idx, QHeaderView.Stretch)
        header.setSectionResizeMode(len(self.columns), QHeaderView.Fixed)
        self.setColumnWidth(len(self.columns), 36)
        self.itemChanged.connect(self._on_changed)
        self._append_row()

    def _append_row(self, values: Optional[List[Any]] = None) -> None:
        row = self.rowCount()
        self.insertRow(row)
        for col, (kind, _h, options) in enumerate(self.columns):
            if kind == "check":
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                self.setItem(row, col, item)
            elif kind == "combo":
                combo = QComboBox()
                combo.setObjectName("cellCombo")
                for value, label in options:
                    combo.addItem(label, value)
                combo.currentIndexChanged.connect(self._on_combo)
                self.setCellWidget(row, col, combo)
            else:
                self.setItem(row, col, QTableWidgetItem(""))
        btn = QToolButton()
        btn.setIcon(theme.icon(G.DELETE, "faint", 14))
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _c=False, b=btn: self._delete(b))
        self.setCellWidget(row, len(self.columns), btn)
        if values is not None:
            self._set_row(row, values)

    def _set_row(self, row: int, values: List[Any]) -> None:
        for col, (kind, _h, _o) in enumerate(self.columns):
            value = values[col]
            if kind == "check":
                self.item(row, col).setCheckState(Qt.Checked if value else Qt.Unchecked)
            elif kind == "combo":
                combo = self.cellWidget(row, col)
                combo.blockSignals(True)
                combo.setCurrentIndex(max(0, combo.findData(value)))
                combo.blockSignals(False)
            else:
                self.item(row, col).setText(str(value or ""))

    def _row_values(self, row: int) -> List[Any]:
        values: List[Any] = []
        for col, (kind, _h, _o) in enumerate(self.columns):
            if kind == "check":
                values.append(self.item(row, col).checkState() == Qt.Checked)
            elif kind == "combo":
                values.append(self.cellWidget(row, col).currentData())
            else:
                values.append(self.item(row, col).text() if self.item(row, col) else "")
        return values

    def _row_has_text(self, row: int) -> bool:
        return any(kind == "text" and self.item(row, col) and self.item(row, col).text().strip() for col, (kind, _h, _o) in enumerate(self.columns))

    def _ensure_trailing(self) -> None:
        if self.rowCount() == 0 or self._row_has_text(self.rowCount() - 1):
            self._append_row()

    def _on_changed(self, _item) -> None:
        if self._updating:
            return
        self._updating = True
        self._ensure_trailing()
        self._updating = False
        self.viewport().update()
        self.changed.emit()

    def _on_combo(self, _idx) -> None:
        if not self._updating:
            self.changed.emit()

    def _delete(self, button: QToolButton) -> None:
        for row in range(self.rowCount()):
            if self.cellWidget(row, len(self.columns)) is button:
                self.removeRow(row)
                break
        self._updating = True
        self._ensure_trailing()
        self._updating = False
        self.changed.emit()

    def set_rows(self, rows: List[List[Any]]) -> None:
        self._updating = True
        self.setRowCount(0)
        for values in rows:
            self._append_row(values)
        self._ensure_trailing()
        self._updating = False

    def rows(self) -> List[List[Any]]:
        return [self._row_values(r) for r in range(self.rowCount()) if self._row_has_text(r)]
