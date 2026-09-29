"""Response viewer: status bar, body views (pretty/raw/preview/tree), headers, cookies, tests, timeline."""
import json
import re
from http import HTTPStatus
from typing import List, Optional

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .code_editor import EditorWithSearch
from .engine import ResponseData, RunResult
from .theme import G
from .widgets import JsonTreeView, Spinner, format_ms, format_size, refresh_tool_icons, tool_button


def _status_phrase(code: int) -> str:
    try:
        status = HTTPStatus(code)
        return f"{status.phrase}: {status.description}"
    except ValueError:
        return ""


def _detect_language(resp: ResponseData) -> str:
    ctype = resp.content_type.lower()
    if resp.is_json:
        return "json"
    if "html" in ctype:
        return "html"
    if "xml" in ctype:
        return "xml"
    if "javascript" in ctype:
        return "javascript"
    head = resp.text.lstrip()[:100].lower()
    if head.startswith("<!doctype html") or head.startswith("<html"):
        return "html"
    if head.startswith("<?xml") or head.startswith("<"):
        return "xml"
    return "text"


def _pretty_xml(text: str) -> str:
    try:
        from xml.dom import minidom

        pretty = minidom.parseString(text.encode("utf-8")).toprettyxml(indent="  ")
        return "\n".join(line for line in pretty.splitlines() if line.strip())
    except Exception:
        return text


class _SimpleTable(QTableWidget):
    def __init__(self, headers: List[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(30)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setWordWrap(False)
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def set_rows(self, rows: List[List[str]]) -> None:
        self.setRowCount(0)
        for values in rows:
            row = self.rowCount()
            self.insertRow(row)
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value if len(value) < 2000 else value[:2000] + "…")
                if col == 0:
                    item.setForeground(theme.qcolor("text"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.setItem(row, col, item)
        if self.columnCount() > 1:
            self.resizeColumnToContents(0)
            self.setColumnWidth(0, min(max(self.columnWidth(0) + 24, 140), 320))

    def _menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        row = item.row()
        menu = QMenu(self)
        clipboard = QApplication.clipboard()
        menu.addAction(theme.icon(G.COPY), "Copy value", lambda: clipboard.setText(self.item(row, min(1, self.columnCount() - 1)).text()))
        menu.addAction("Copy row", lambda: clipboard.setText(": ".join(self.item(row, c).text() for c in range(min(2, self.columnCount())))))
        menu.addAction("Copy all", lambda: clipboard.setText("\n".join(f"{self.item(r, 0).text()}: {self.item(r, 1).text()}" for r in range(self.rowCount()))))
        menu.exec_(self.viewport().mapToGlobal(pos))


class ResponsePanel(QFrame):
    cancelRequested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("responsePanel")
        self._result: Optional[RunResult] = None
        self._body_view = "pretty"

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 8, 16, 8)
        root.setSpacing(6)

        # Header ----------------------------------------------------------
        header = QFrame()
        header.setObjectName("responseHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("responseTabs")
        self.tabs.setDocumentMode(True)
        title = QLabel("Response")
        title.setObjectName("h2")
        h.addWidget(title)
        h.addStretch(1)
        self.status_pill = QLabel()
        self.status_pill.setObjectName("statusPill")
        self.time_label = QLabel()
        self.size_label = QLabel()
        for label in (self.time_label, self.size_label):
            label.setObjectName("muted")
        h.addWidget(self.status_pill)
        h.addWidget(self.time_label)
        h.addWidget(self.size_label)
        self.more_btn = tool_button(G.MORE, "Response actions")
        self.more_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.more_btn)
        menu.addAction(theme.icon(G.DOWNLOAD), "Save response to file…", self.save_to_file)
        menu.addAction(theme.icon(G.COPY), "Copy response body", self.copy_body)
        menu.addSeparator()
        menu.addAction(theme.icon(G.CLEAR), "Clear response", self.clear)
        self.more_btn.setMenu(menu)
        h.addWidget(self.more_btn)
        self.meta_widgets = [self.status_pill, self.time_label, self.size_label, self.more_btn]
        root.addWidget(header)

        # Pages -------------------------------------------------------------
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self.pages.addWidget(self._empty_page())
        self.pages.addWidget(self._loading_page())
        self.pages.addWidget(self._error_page())
        self.pages.addWidget(self._content_page())
        self.clear()
        theme.manager().changed.connect(self._refresh_theme)

    # -- pages ---------------------------------------------------------------------
    def _empty_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        icon = QLabel()
        icon.setAlignment(Qt.AlignCenter)
        self._empty_icon = icon
        text = QLabel("Enter the URL and click Send to get a response")
        text.setObjectName("muted")
        text.setAlignment(Qt.AlignCenter)
        hint = QLabel("Ctrl+Enter to send  ·  Ctrl+S to save  ·  Ctrl+F to find in an editor")
        hint.setObjectName("faint")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon)
        layout.addSpacing(6)
        layout.addWidget(text)
        layout.addWidget(hint)
        layout.addStretch(2)
        return page

    def _loading_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        self.spinner = Spinner(page, 34)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.spinner)
        row.addStretch(1)
        layout.addLayout(row)
        label = QLabel("Sending request…")
        label.setAlignment(Qt.AlignCenter)
        label.setObjectName("muted")
        layout.addSpacing(8)
        layout.addWidget(label)
        cancel_row = QHBoxLayout()
        cancel_row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.cancelRequested.emit)
        cancel_row.addWidget(cancel)
        cancel_row.addStretch(1)
        layout.addSpacing(6)
        layout.addLayout(cancel_row)
        layout.addStretch(2)
        return page

    def _error_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        self._error_icon = QLabel()
        self._error_icon.setAlignment(Qt.AlignCenter)
        self.error_title = QLabel("Could not send request")
        self.error_title.setObjectName("h2")
        self.error_title.setAlignment(Qt.AlignCenter)
        self.error_text = QLabel()
        self.error_text.setObjectName("muted")
        self.error_text.setAlignment(Qt.AlignCenter)
        self.error_text.setWordWrap(True)
        self.error_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.error_text.setMaximumWidth(720)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.error_text)
        row.addStretch(1)
        layout.addWidget(self._error_icon)
        layout.addWidget(self.error_title)
        layout.addSpacing(4)
        layout.addLayout(row)
        layout.addStretch(2)
        return page

    def _content_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Body tab
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 8, 0, 0)
        body_layout.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.view_group = QButtonGroup(self)
        self.view_buttons = {}
        for idx, (key, label) in enumerate([("pretty", "Pretty"), ("raw", "Raw"), ("preview", "Preview"), ("tree", "Tree")]):
            btn = QToolButton()
            btn.setObjectName("segButton")
            btn.setText(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            self.view_group.addButton(btn, idx)
            self.view_buttons[key] = btn
            bar.addWidget(btn)
        self.view_group.buttonClicked.connect(self._on_view)
        self.lang_label = QLabel("JSON")
        self.lang_label.setObjectName("muted")
        bar.addSpacing(10)
        bar.addWidget(self.lang_label)
        bar.addStretch(1)
        self.wrap_btn = tool_button(G.WRAP, "Wrap lines")
        self.wrap_btn.setCheckable(True)
        self.wrap_btn.toggled.connect(self._on_wrap)
        self.search_btn = tool_button(G.SEARCH, "Search (Ctrl+F)")
        self.search_btn.clicked.connect(self._open_search)
        self.copy_btn = tool_button(G.COPY, "Copy response body")
        self.copy_btn.clicked.connect(self.copy_body)
        for btn in (self.wrap_btn, self.search_btn, self.copy_btn):
            bar.addWidget(btn)
        body_layout.addLayout(bar)

        self.body_stack = QStackedWidget()
        self.pretty_view = EditorWithSearch(language="json", read_only=True)
        self.raw_view = EditorWithSearch(language="text", read_only=True)
        self.preview_stack = QStackedWidget()
        self.preview_view = QTextBrowser()
        self.preview_view.setOpenExternalLinks(False)
        self.image_view = QLabel()
        self.image_view.setAlignment(Qt.AlignCenter)
        image_scroll = QScrollArea()
        image_scroll.setWidgetResizable(True)
        image_scroll.setWidget(self.image_view)
        self.preview_stack.addWidget(self.preview_view)
        self.preview_stack.addWidget(image_scroll)
        self.tree_view = JsonTreeView()
        for widget in (self.pretty_view, self.raw_view, self.preview_stack, self.tree_view):
            self.body_stack.addWidget(widget)
        body_layout.addWidget(self.body_stack, 1)
        self.tabs.addTab(body, "Body")

        self.cookies_table = _SimpleTable(["Name", "Value", "Domain", "Path", "Expires", "HttpOnly", "Secure"])
        cookies_wrap = QWidget()
        cw = QVBoxLayout(cookies_wrap)
        cw.setContentsMargins(0, 8, 0, 0)
        cw.addWidget(self.cookies_table)
        self.tabs.addTab(cookies_wrap, "Cookies")

        self.headers_table = _SimpleTable(["Key", "Value"])
        headers_wrap = QWidget()
        hw = QVBoxLayout(headers_wrap)
        hw.setContentsMargins(0, 8, 0, 0)
        hw.addWidget(self.headers_table)
        self.tabs.addTab(headers_wrap, "Headers")

        tests_wrap = QWidget()
        tw = QVBoxLayout(tests_wrap)
        tw.setContentsMargins(0, 8, 0, 0)
        filters = QHBoxLayout()
        filters.setSpacing(2)
        self.test_filter_group = QButtonGroup(self)
        for idx, label in enumerate(["All", "Passed", "Failed"]):
            btn = QToolButton()
            btn.setObjectName("segButton")
            btn.setText(label)
            btn.setCheckable(True)
            btn.setChecked(idx == 0)
            self.test_filter_group.addButton(btn, idx)
            filters.addWidget(btn)
        self.test_filter_group.buttonClicked.connect(lambda *_: self._render_tests())
        filters.addStretch(1)
        tw.addLayout(filters)
        self.tests_tree = QTreeWidget()
        self.tests_tree.setObjectName("resultsTree")
        self.tests_tree.setHeaderHidden(True)
        self.tests_tree.setColumnCount(2)
        self.tests_tree.setRootIsDecorated(False)
        self.tests_tree.header().setSectionResizeMode(0, QHeaderView.Fixed)
        self.tests_tree.header().setStretchLastSection(True)
        self.tests_tree.setColumnWidth(0, 70)
        tw.addWidget(self.tests_tree, 1)
        self.tabs.addTab(tests_wrap, "Test Results")

        self.timeline_view = EditorWithSearch(language="http", read_only=True, line_numbers=False)
        timeline_wrap = QWidget()
        tl = QVBoxLayout(timeline_wrap)
        tl.setContentsMargins(0, 8, 0, 0)
        tl.addWidget(self.timeline_view)
        self.tabs.addTab(timeline_wrap, "Timeline")

        layout.addWidget(self.tabs, 1)
        self.view_buttons["pretty"].setChecked(True)
        return page

    # -- state -------------------------------------------------------------------
    def _refresh_theme(self, *_args) -> None:
        self._empty_icon.setPixmap(theme.icon(G.SEND, "faint", 56).pixmap(QSize(56, 56)))
        self._error_icon.setPixmap(theme.icon(G.ERROR, "danger", 44).pixmap(QSize(44, 44)))
        refresh_tool_icons(self)
        if self._result is not None and self._result.response is not None:
            self._update_meta(self._result.response)
            self._render_tests()

    def _set_meta_visible(self, visible: bool) -> None:
        for widget in self.meta_widgets:
            widget.setVisible(visible)

    def clear(self) -> None:
        self._result = None
        self.spinner.stop()
        self._set_meta_visible(False)
        self.pages.setCurrentIndex(0)
        self._refresh_theme()

    def show_loading(self) -> None:
        self._set_meta_visible(False)
        self.pages.setCurrentIndex(1)
        self.spinner.start()

    def show_cancelled(self) -> None:
        self.spinner.stop()
        self._show_error("Request cancelled", "The request was cancelled before a response was received.")

    def _show_error(self, title: str, message: str) -> None:
        self.error_title.setText(title)
        self.error_text.setText(message)
        self._set_meta_visible(False)
        self.pages.setCurrentIndex(2)

    def show_result(self, result: RunResult) -> None:
        self.spinner.stop()
        self._result = result
        if result.cancelled:
            self.show_cancelled()
            return
        if result.error or result.response is None:
            self._show_error("Could not send request", result.error or "Unknown error")
            return
        resp = result.response
        self._update_meta(resp)
        self._set_meta_visible(True)
        self.pages.setCurrentIndex(3)

        language = _detect_language(resp)
        self.lang_label.setText({"json": "JSON", "html": "HTML", "xml": "XML", "javascript": "JavaScript"}.get(language, "Text"))
        text = resp.text
        pretty = text
        if language == "json":
            try:
                pretty = json.dumps(resp.json(), indent=2, ensure_ascii=False)
            except Exception:
                pass
        elif language == "xml":
            pretty = _pretty_xml(text) if len(text) < 2_000_000 else text
        if resp.truncated:
            notice = f"\n\n--- Response truncated at {format_size(len(resp.content))} (Settings > General > Max response size) ---"
            pretty += notice
            text += notice
        self.pretty_view.editor.set_language(language if language != "javascript" else "javascript")
        self.pretty_view.editor.set_text_fast(pretty)
        self.raw_view.editor.set_text_fast(text)

        is_image = resp.content_type.lower().startswith("image/")
        self.view_buttons["tree"].setVisible(language == "json")
        self.view_buttons["preview"].setVisible(True)
        if is_image:
            pix = QPixmap()
            pix.loadFromData(resp.content)
            self.image_view.setPixmap(pix)
            self.preview_stack.setCurrentIndex(1)
        else:
            self.preview_stack.setCurrentIndex(0)
            if language == "html":
                # Strip scripts; QTextBrowser renders a safe subset of HTML without fetching resources.
                self.preview_view.setHtml(re.sub(r"(?is)<script.*?</script>", "", text[:2_000_000]))
            else:
                self.preview_view.setPlainText(text[:2_000_000])
        if language == "json":
            try:
                self.tree_view.load(resp.json())
            except Exception:
                self.tree_view.clear()

        view = self._body_view
        if is_image:
            view = "preview"
        elif view == "tree" and language != "json":
            view = "pretty"
        self.view_buttons[view].setChecked(True)
        self._apply_view(view)

        self.headers_table.set_rows([[k, v] for k, v in resp.headers])
        self.cookies_table.set_rows(
            [[c["name"], c["value"], c["domain"], c["path"], c["expires"], c["httponly"], c["secure"]] for c in resp.cookies]
        )
        self.tabs.setTabText(1, f"Cookies ({len(resp.cookies)})" if resp.cookies else "Cookies")
        self.tabs.setTabText(2, f"Headers ({len(resp.headers)})")
        self._render_tests()
        self._render_timeline(result)

    def _update_meta(self, resp: ResponseData) -> None:
        color = theme.status_color(resp.status)
        self.status_pill.setText(f"{resp.status} {resp.reason}".strip())
        self.status_pill.setStyleSheet(f"QLabel#statusPill {{ color: {color}; background: {theme.color('selected')}; }}")
        self.status_pill.setToolTip(_status_phrase(resp.status))
        self.time_label.setText(f"<span style='color:{theme.color('muted')}'>Time</span> <span style='color:{color}'>{format_ms(resp.elapsed_ms)}</span>")
        self.time_label.setToolTip(
            f"Time to first byte: {format_ms(resp.ttfb_ms)}\nDownload: {format_ms(max(0.0, resp.elapsed_ms - resp.ttfb_ms))}\nTotal: {format_ms(resp.elapsed_ms)}"
        )
        total = resp.body_size + resp.headers_size
        self.size_label.setText(f"<span style='color:{theme.color('muted')}'>Size</span> <span style='color:{color}'>{format_size(total)}</span>")
        self.size_label.setToolTip(f"Headers: {format_size(resp.headers_size)}\nBody: {format_size(resp.body_size)}")

    def _render_tests(self) -> None:
        self.tests_tree.clear()
        result = self._result
        if result is None:
            return
        tests = result.tests
        passed = sum(1 for t in tests if t.passed)
        if tests:
            self.tabs.setTabText(3, f"Test Results ({passed}/{len(tests)})")
        else:
            self.tabs.setTabText(3, "Test Results")
        mode = self.test_filter_group.checkedId()
        for test in tests:
            if (mode == 1 and not test.passed) or (mode == 2 and test.passed):
                continue
            item = QTreeWidgetItem(["PASS" if test.passed else "FAIL", test.name + (f"  —  {test.message}" if test.message else "")])
            item.setForeground(0, theme.qcolor("success" if test.passed else "danger"))
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            if not test.passed:
                item.setForeground(1, theme.qcolor("text"))
            self.tests_tree.addTopLevelItem(item)
        if not tests:
            item = QTreeWidgetItem(["", "No tests for this request. Add assertions in the Tests tab or pm.test() in a post-response script."])
            item.setForeground(1, theme.qcolor("muted"))
            self.tests_tree.addTopLevelItem(item)
        if result.logs:
            for level, message in result.logs:
                item = QTreeWidgetItem([level.upper(), message])
                item.setForeground(0, theme.qcolor({"error": "danger", "warn": "warning"}.get(level, "muted")))
                self.tests_tree.addTopLevelItem(item)

    def _render_timeline(self, result: RunResult) -> None:
        resp = result.response
        lines = []
        if result.request is not None:
            lines.append(f"{result.request.method} {result.request.url}")
        if resp is not None:
            lines.append(f"Status: {resp.status} {resp.reason} · {resp.http_version}")
            lines.append(f"Time: total {format_ms(resp.elapsed_ms)} · first byte {format_ms(resp.ttfb_ms)}")
            lines.append(f"Size: headers {format_size(resp.headers_size)} · body {format_size(resp.body_size)}")
            if resp.redirects:
                lines.append("")
                lines.append("▾ Redirects")
                for code, url in resp.redirects:
                    lines.append(f"  {code} → {url}")
        if result.request is not None:
            lines.append("")
            lines.append("▾ Request Headers")
            lines.extend(f"{k}: {v}" for k, v in result.request.headers)
            if result.request.body_preview:
                lines.append("")
                lines.append("▾ Request Body")
                lines.append(result.request.body_preview)
        if resp is not None:
            lines.append("")
            lines.append("▾ Response Headers")
            lines.extend(f"{k}: {v}" for k, v in resp.headers)
        self.timeline_view.editor.setPlainText("\n".join(lines))

    # -- actions ---------------------------------------------------------------
    def _on_view(self, *_args) -> None:
        key = next(k for k, b in self.view_buttons.items() if b.isChecked())
        self._body_view = key
        self._apply_view(key)

    def _apply_view(self, key: str) -> None:
        self.body_stack.setCurrentIndex(["pretty", "raw", "preview", "tree"].index(key))
        self.wrap_btn.setEnabled(key in ("pretty", "raw"))
        self.search_btn.setEnabled(key in ("pretty", "raw"))

    def _on_wrap(self, wrap: bool) -> None:
        self.pretty_view.editor.set_wrap(wrap)
        self.raw_view.editor.set_wrap(wrap)

    def _open_search(self) -> None:
        if self.body_stack.currentIndex() == 1:
            self.raw_view.open_search()
        else:
            self.view_buttons["pretty"].setChecked(True)
            self._apply_view("pretty")
            self.pretty_view.open_search()

    def open_search(self) -> None:
        if self.pages.currentIndex() == 3:
            self.tabs.setCurrentIndex(0)
            self._open_search()

    def copy_body(self) -> None:
        if self._result and self._result.response:
            QApplication.clipboard().setText(self.pretty_view.editor.toPlainText())

    def save_to_file(self) -> None:
        if not (self._result and self._result.response):
            return
        resp = self._result.response
        ext = {"json": "json", "html": "html", "xml": "xml"}.get(_detect_language(resp), "txt")
        if resp.content_type.lower().startswith("image/"):
            ext = resp.content_type.split("/", 1)[1].split(";")[0]
        path, _ = QFileDialog.getSaveFileName(self, "Save response", f"response.{ext}")
        if path:
            with open(path, "wb") as handle:
                handle.write(resp.content)
