"""Postman-style key/value table with an always-present empty row and bulk edit."""
from typing import Callable, List, Optional, Sequence, Tuple

from PyQt5.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .engine import VAR_RE
from .models import KeyValue
from .theme import G

Resolver = Callable[[str], Tuple[Optional[str], Optional[str]]]


class _KVDelegate(QStyledItemDelegate):
    def __init__(self, table: "KeyValueTable") -> None:
        super().__init__(table.table)
        self.owner = table

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setObjectName("cellEditor")
        editor.setFrame(False)
        return editor

    def sizeHint(self, option, index) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), 32)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        col = index.column()
        owner = self.owner
        if col not in owner.text_columns():
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else None
        if style:
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        rect = option.rect.adjusted(8, 0, -6, 0)
        painter.save()
        painter.setClipRect(option.rect)
        row = index.row()
        is_last = row == owner.table.rowCount() - 1
        enabled = owner.row_enabled(row)
        base_color = theme.qcolor("text" if enabled else "faint")

        if not text:
            placeholder = owner.placeholder_for(col, row)
            if placeholder and (is_last or col == owner.col_value):
                painter.setPen(theme.qcolor("faint"))
                painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, placeholder)
            painter.restore()
            return

        if col == owner.col_value and owner.row_kind(row) == "secret":
            painter.setPen(base_color)
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, "•" * min(12, max(6, len(text))))
            painter.restore()
            return
        if col == owner.col_value and owner.row_kind(row) == "file":
            painter.setPen(theme.qcolor("link") if enabled else base_color)
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, text.replace("\\", "/").split("/")[-1])
            painter.restore()
            return

        metrics = painter.fontMetrics()
        x = rect.left()
        pos = 0
        segments = []
        for match in VAR_RE.finditer(text):
            if match.start() > pos:
                segments.append((text[pos:match.start()], None))
            segments.append((match.group(0), match.group(1)))
            pos = match.end()
        if pos < len(text):
            segments.append((text[pos:], None))
        for segment, var_name in segments:
            if x > rect.right():
                break
            if var_name is not None and enabled:
                resolved = owner.resolver(var_name)[0] is not None if owner.resolver else True
                painter.setPen(theme.qcolor("var_ok" if resolved else "var_bad"))
            else:
                painter.setPen(base_color)
            width = metrics.horizontalAdvance(segment)
            painter.drawText(QRect(x, rect.top(), width + 2, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, segment)
            x += width
        painter.restore()


class KeyValueTable(QWidget):
    changed = pyqtSignal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        type_options: Optional[Sequence[Tuple[str, str]]] = None,
        show_description: bool = True,
        key_placeholder: str = "Key",
        value_placeholder: str = "Value",
        title: str = "",
        allow_bulk: bool = True,
        type_header: str = "Type",
        value_header: str = "Value",
        key_header: str = "Key",
    ) -> None:
        super().__init__(parent)
        self.type_options = list(type_options or [])
        self.show_description = show_description
        self.key_placeholder = key_placeholder
        self.value_placeholder = value_placeholder
        self.resolver: Optional[Resolver] = None
        self._updating = False

        headers = ["", key_header]
        self.col_enabled = 0
        self.col_key = 1
        self.col_type = -1
        if self.type_options:
            self.col_type = len(headers)
            headers.append(type_header)
        self.col_value = len(headers)
        headers.append(value_header)
        self.col_desc = -1
        if show_description:
            self.col_desc = len(headers)
            headers.append("Description")
        self.col_delete = len(headers)
        headers.append("")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("sectionTitle")
        header_row.addWidget(self.title_label)
        header_row.addStretch(1)
        self.bulk_btn = QPushButton("Bulk Edit")
        self.bulk_btn.setObjectName("linkButton")
        self.bulk_btn.setCursor(Qt.PointingHandCursor)
        self.bulk_btn.clicked.connect(self.toggle_bulk)
        self.bulk_btn.setVisible(allow_bulk)
        header_row.addWidget(self.bulk_btn)
        self.header_widget = QWidget()
        self.header_widget.setLayout(header_row)
        self.header_widget.setVisible(bool(title) or allow_bulk)
        root.addWidget(self.header_widget)

        self.stack = QStackedWidget()
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.table.setShowGrid(True)
        self.table.setWordWrap(False)
        self.table.setItemDelegate(_KVDelegate(self))
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(self.col_enabled, QHeaderView.Fixed)
        header.setSectionResizeMode(self.col_delete, QHeaderView.Fixed)
        self.table.setColumnWidth(self.col_enabled, 34)
        self.table.setColumnWidth(self.col_delete, 36)
        if self.col_type >= 0:
            header.setSectionResizeMode(self.col_type, QHeaderView.Fixed)
            self.table.setColumnWidth(self.col_type, 112)
        header.setStretchLastSection(False)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.stack.addWidget(self.table)

        self.bulk_editor = QPlainTextEdit()
        self.bulk_editor.setObjectName("bulkEditor")
        self.bulk_editor.setFont(theme.mono_font())
        self.bulk_editor.setPlaceholderText(
            "Rows are separated by new lines\nKeys and values are separated by :\nPrepend // to any row you want to add but keep disabled"
        )
        self.bulk_editor.textChanged.connect(self._on_bulk_changed)
        self.stack.addWidget(self.bulk_editor)
        root.addWidget(self.stack, 1)

        self._ensure_trailing_row()
        theme.manager().changed.connect(self._refresh_theme)

    # -- layout -------------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_columns()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_columns()

    def _fit_columns(self) -> None:
        available = self.table.viewport().width()
        fixed = self.table.columnWidth(self.col_enabled) + self.table.columnWidth(self.col_delete)
        if self.col_type >= 0:
            fixed += self.table.columnWidth(self.col_type)
        flexible = [self.col_key, self.col_value] + ([self.col_desc] if self.col_desc >= 0 else [])
        remaining = max(240, available - fixed)
        weights = {self.col_key: 0.34, self.col_value: 0.38, self.col_desc: 0.28} if self.col_desc >= 0 else {self.col_key: 0.42, self.col_value: 0.58}
        for col in flexible:
            self.table.setColumnWidth(col, int(remaining * weights[col]))

    def _refresh_theme(self, *_args) -> None:
        self.bulk_editor.setFont(theme.mono_font())
        for row in range(self.table.rowCount()):
            button = self.table.cellWidget(row, self.col_delete)
            if isinstance(button, QToolButton):
                button.setIcon(theme.icon(G.DELETE, "faint", 14))
        self.table.viewport().update()

    # -- helpers used by the delegate -------------------------------------------
    def text_columns(self) -> Tuple[int, ...]:
        cols = [self.col_key, self.col_value]
        if self.col_desc >= 0:
            cols.append(self.col_desc)
        return tuple(cols)

    def placeholder_for(self, col: int, row: int) -> str:
        if col == self.col_key:
            return self.key_placeholder
        if col == self.col_value:
            if self.row_kind(row) == "file":
                return "Double-click to select a file"
            return self.value_placeholder if row == self.table.rowCount() - 1 else ""
        if col == self.col_desc:
            return "Description"
        return ""

    def row_enabled(self, row: int) -> bool:
        item = self.table.item(row, self.col_enabled)
        if item is None or not (item.flags() & Qt.ItemIsUserCheckable):
            return True
        return item.checkState() == Qt.Checked

    def row_kind(self, row: int) -> str:
        if self.col_type < 0:
            return "text"
        combo = self.table.cellWidget(row, self.col_type)
        if isinstance(combo, QComboBox):
            return combo.currentData() or "text"
        return "text"

    def set_resolver(self, resolver: Optional[Resolver]) -> None:
        self.resolver = resolver
        self.table.viewport().update()

    # -- rows -----------------------------------------------------------------------
    def _text(self, row: int, col: int) -> str:
        if col < 0:
            return ""
        item = self.table.item(row, col)
        return item.text() if item else ""

    def _row_has_content(self, row: int) -> bool:
        return any(self._text(row, c).strip() for c in self.text_columns())

    def _append_row(self, kv: Optional[KeyValue] = None) -> int:
        row = self.table.rowCount()
        self.table.insertRow(row)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(row, self.col_enabled, check)
        for col in self.text_columns():
            self.table.setItem(row, col, QTableWidgetItem(""))
        if self.col_type >= 0:
            combo = QComboBox()
            combo.setObjectName("cellCombo")
            for value, label in self.type_options:
                combo.addItem(label, value)
            combo.currentIndexChanged.connect(lambda _i, c=combo: self._on_type_changed(c))
            self.table.setCellWidget(row, self.col_type, combo)
        if kv is not None:
            self._fill_row(row, kv)
        return row

    def _fill_row(self, row: int, kv: KeyValue) -> None:
        self.table.item(row, self.col_key).setText(kv.key)
        self.table.item(row, self.col_value).setText(kv.value)
        if self.col_desc >= 0:
            self.table.item(row, self.col_desc).setText(kv.description)
        if self.col_type >= 0:
            combo = self.table.cellWidget(row, self.col_type)
            idx = combo.findData(kv.kind)
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)
            self._apply_kind_flags(row)
        self._activate_row(row, kv.enabled)

    def _activate_row(self, row: int, enabled: bool = True) -> None:
        check = self.table.item(row, self.col_enabled)
        if not check.flags() & Qt.ItemIsUserCheckable:
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        check.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
        if self.table.cellWidget(row, self.col_delete) is None:
            btn = QToolButton()
            btn.setIcon(theme.icon(G.DELETE, "faint", 14))
            btn.setToolTip("Delete row")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, b=btn: self._delete_row_of(b))
            self.table.setCellWidget(row, self.col_delete, btn)

    def _apply_kind_flags(self, row: int) -> None:
        item = self.table.item(row, self.col_value)
        if item is None:
            return
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if self.row_kind(row) != "file":
            flags |= Qt.ItemIsEditable
        item.setFlags(flags)

    def _ensure_trailing_row(self) -> None:
        count = self.table.rowCount()
        if count == 0 or self._row_has_content(count - 1):
            self._append_row()

    def _delete_row_of(self, button: QToolButton) -> None:
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, self.col_delete) is button:
                self.table.removeRow(row)
                break
        self._updating = True
        self._ensure_trailing_row()
        self._updating = False
        self.changed.emit()

    # -- events -------------------------------------------------------------------
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            row = item.row()
            if item.column() in self.text_columns() and self._row_has_content(row):
                check = self.table.item(row, self.col_enabled)
                if check is not None and not check.flags() & Qt.ItemIsUserCheckable:
                    self._activate_row(row, True)
            self._ensure_trailing_row()
        finally:
            self._updating = False
        self.table.viewport().update()
        self.changed.emit()

    def _on_type_changed(self, combo: QComboBox) -> None:
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, self.col_type) is combo:
                self._apply_kind_flags(row)
                if combo.currentData() == "file":
                    self.table.item(row, self.col_value).setText("")
                break
        self.table.viewport().update()
        if not self._updating:
            self.changed.emit()

    def _on_double_click(self, row: int, col: int) -> None:
        if col == self.col_value and self.row_kind(row) == "file":
            path, _ = QFileDialog.getOpenFileName(self, "Select file")
            if path:
                self.table.item(row, self.col_value).setText(path)

    # -- public API -------------------------------------------------------------
    def set_items(self, items: Sequence[KeyValue]) -> None:
        self._updating = True
        try:
            self.table.setRowCount(0)
            for kv in items:
                self._append_row(kv)
            self._ensure_trailing_row()
            if self.stack.currentIndex() == 1:
                self.bulk_editor.blockSignals(True)
                self.bulk_editor.setPlainText(self._to_bulk(list(items)))
                self.bulk_editor.blockSignals(False)
        finally:
            self._updating = False
        self.table.viewport().update()

    def items(self) -> List[KeyValue]:
        if self.stack.currentIndex() == 1:
            return self._from_bulk(self.bulk_editor.toPlainText())
        out: List[KeyValue] = []
        for row in range(self.table.rowCount()):
            if not self._row_has_content(row):
                continue
            out.append(
                KeyValue(
                    key=self._text(row, self.col_key).strip(),
                    value=self._text(row, self.col_value),
                    description=self._text(row, self.col_desc),
                    enabled=self.row_enabled(row),
                    kind=self.row_kind(row) if self.col_type >= 0 else "text",
                )
            )
        return out

    def enabled_count(self) -> int:
        return sum(1 for kv in self.items() if kv.enabled and kv.key)

    # -- bulk edit ----------------------------------------------------------------
    def _to_bulk(self, items: List[KeyValue]) -> str:
        return "\n".join(f"{'' if kv.enabled else '//'}{kv.key}:{kv.value}" for kv in items)

    def _from_bulk(self, text: str) -> List[KeyValue]:
        kinds = {kv.key: kv for kv in self._table_items()}
        out: List[KeyValue] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            enabled = not line.startswith("//")
            if not enabled:
                line = line[2:].strip()
            key, _, value = line.partition(":")
            key = key.strip()
            if not key:
                continue
            previous = kinds.get(key)
            out.append(
                KeyValue(
                    key=key,
                    value=value.strip(),
                    enabled=enabled,
                    description=previous.description if previous else "",
                    kind=previous.kind if previous else ("default" if self.type_options and self.type_options[0][0] == "default" else "text"),
                )
            )
        return out

    def _table_items(self) -> List[KeyValue]:
        idx = self.stack.currentIndex()
        self.stack.setCurrentIndex(0)
        items = self.items()
        self.stack.setCurrentIndex(idx)
        return items

    def toggle_bulk(self) -> None:
        if self.stack.currentIndex() == 0:
            self.bulk_editor.blockSignals(True)
            self.bulk_editor.setPlainText(self._to_bulk(self.items()))
            self.bulk_editor.blockSignals(False)
            self.stack.setCurrentIndex(1)
            self.bulk_btn.setText("Key-Value Edit")
        else:
            items = self._from_bulk(self.bulk_editor.toPlainText())
            self.stack.setCurrentIndex(0)
            self.set_items(items)
            self.bulk_btn.setText("Bulk Edit")
        self.changed.emit()

    def _on_bulk_changed(self) -> None:
        if not self._updating:
            self.changed.emit()
