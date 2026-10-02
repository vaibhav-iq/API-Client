"""Left-sidebar Proxy panel: live capture list + an entry viewer, Burp-style.

Shows traffic captured by the intercepting proxy; entries can be opened to see
the request/response and sent to the Repeater or Intruder.
"""
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Set
from urllib.parse import urlsplit

from PyQt5.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QIcon, QPixmap, QTextOption
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .code_editor import EditorWithSearch
from .dialogs import ThemedDialog
from .engine import ResponseData, RunResult, SentRequest
from .models import AuthConfig, KeyValue, RequestBody, RequestModel
from .proxy_server import CaptureRecord
from .response_panel import ResponsePanel
from .theme import G
from .widgets import format_ms, format_size, tool_button

if TYPE_CHECKING:  # pragma: no cover
    from .main_window import MainWindow

ROLE = Qt.UserRole


def record_to_model(rec: CaptureRecord) -> RequestModel:
    name = f"{rec.method} {rec.host}{rec.path}"[:60] or rec.url
    body = rec.req_body.decode("utf-8", "replace") if rec.req_body else ""
    model = RequestModel(name=name, method=rec.method, url=rec.url, auth=AuthConfig("noauth"))
    model.headers = [KeyValue(key=k, value=v) for k, v in rec.req_headers
                     if k.lower() not in ("content-length", "proxy-connection")]
    if body:
        lang = "json" if body.lstrip()[:1] in "{[" else "text"
        model.body = RequestBody(mode="raw", raw=body, raw_language=lang)
    return model


def record_to_result(rec: CaptureRecord, original: bool = False) -> RunResult:
    sent = SentRequest(method=rec.method, url=rec.url, headers=list(rec.req_headers),
                       body_preview=rec.req_body.decode("utf-8", "replace")[:20000] if rec.req_body else "")
    if rec.status is None:
        return RunResult(request=sent, error=rec.error or "No response captured", method=rec.method, url=rec.url)
    use_orig = original and rec.resp_edited
    status = rec.orig_status if use_orig else rec.status
    reason = rec.orig_reason if use_orig else rec.reason
    headers = list(rec.orig_resp_headers if use_orig else rec.resp_headers)
    body = rec.orig_resp_body if use_orig else rec.resp_body
    content_type = next((v for k, v in headers if k.lower() == "content-type"), rec.content_type)
    resp = ResponseData(
        status=status, reason=reason, http_version="HTTP/1.1", url=rec.url,
        headers=headers, cookies=[], content=body, truncated=False,
        body_size=len(body), headers_size=sum(len(k) + len(v) + 4 for k, v in headers),
        elapsed_ms=rec.elapsed_ms, ttfb_ms=rec.elapsed_ms, encoding="utf-8", content_type=content_type,
    )
    return RunResult(request=sent, response=resp, method=rec.method, url=rec.url)


class ProxyEntryDialog(ThemedDialog):
    def __init__(self, app: "MainWindow", record: CaptureRecord) -> None:
        super().__init__(app, f"Proxy · {record.method} {record.host}{record.path}"[:80], 1180, 760)
        self.setWindowModality(Qt.NonModal)
        self.app = app
        self.record = record

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        head = QHBoxLayout()
        title = QLabel("Request")
        title.setObjectName("h2")
        head.addWidget(title)
        raw = EditorWithSearch(language="httpreq", read_only=True)
        raw.editor.setPlainText(self._raw_request())
        head.addLayout(editor_tools(raw))
        ll.addLayout(head)
        ll.addWidget(raw, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        self.response = ResponsePanel()
        rl.addWidget(self.response, 1)
        split.addWidget(right)
        split.setSizes([520, 660])
        self.body_layout.addWidget(split, 1)
        self.response.show_result(record_to_result(record))

        self.add_button("Send to Repeater", lambda: self.app.open_repeater(record_to_model(record)))
        self.add_button("Send to Intruder", lambda: self.app.open_intruder(record_to_model(record)))
        self.add_button("Save to Collection", lambda: self.app.save_model_to_collection(record_to_model(record)))
        self.add_button("Close", self.close, primary=True)

    def _raw_request(self) -> str:
        lines = [f"{self.record.method} {self.record.path or self.record.url} HTTP/1.1"]
        lines.extend(f"{k}: {v}" for k, v in self.record.req_headers)
        lines.append("")
        if self.record.req_body:
            lines.append(self.record.req_body.decode("utf-8", "replace"))
        return "\n".join(lines)


def _start_proxy(app: "MainWindow") -> None:
    app.proxy.start(app.settings.proxy_listen_port, app.settings.proxy_bind_host or "127.0.0.1")


def _set_whitespace(ews, on: bool) -> None:
    doc = ews.editor.document()
    opt = doc.defaultTextOption()
    flags = opt.flags()
    ws = QTextOption.ShowTabsAndSpaces | QTextOption.ShowLineAndParagraphSeparators
    opt.setFlags(flags | ws if on else flags & ~ws)
    doc.setDefaultTextOption(opt)
    ews.editor.viewport().update()


def editor_tools(ews) -> QHBoxLayout:
    """A compact toolbar (wrap / show-whitespace / find) for a request editor."""
    bar = QHBoxLayout()
    bar.setSpacing(2)
    bar.addStretch(1)
    wrap = tool_button(G.WRAP, "Word wrap")
    wrap.setCheckable(True)
    wrap.toggled.connect(lambda on: ews.editor.set_wrap(on))
    wrap.setChecked(True)  # word wrap on by default in the request view
    ws = QToolButton()
    ws.setText("¶")
    ws.setCheckable(True)
    ws.setToolTip("Show whitespace / line breaks")
    ws.setCursor(Qt.PointingHandCursor)
    ws.toggled.connect(lambda on: _set_whitespace(ews, on))
    search = tool_button(G.SEARCH, "Find (Ctrl+F)")
    search.clicked.connect(ews.open_search)
    for b in (wrap, ws, search):
        bar.addWidget(b)
    return bar


_MIME_LABELS = {"html": "HTML", "json": "JSON", "xml": "XML", "js": "script",
                "css": "CSS", "text": "text", "images": "image", "binary": "binary"}


def mime_label(content_type: str) -> str:
    return _MIME_LABELS.get(mime_category(content_type), "")


def has_params(rec: CaptureRecord) -> bool:
    return ("?" in rec.url and bool(rec.url.split("?", 1)[1])) or bool(rec.req_body)


def _raw_request(rec: CaptureRecord, original: bool = False) -> str:
    if original and rec.edited:
        method, headers, body = rec.orig_method, rec.orig_req_headers, rec.orig_req_body
        sp = urlsplit(rec.orig_url)
        path = sp.path + (f"?{sp.query}" if sp.query else "") or rec.orig_url
    else:
        method, headers, body, path = rec.method, rec.req_headers, rec.req_body, rec.path
    lines = [f"{method} {path or rec.url} HTTP/1.1"]
    lines.extend(f"{k}: {v}" for k, v in headers)
    lines.append("")
    if body:
        lines.append(body.decode("utf-8", "replace"))
    return "\n".join(lines)


HIGHLIGHT_NAMES = ["red", "orange", "yellow", "green", "cyan", "blue", "pink", "purple", "gray"]
_HL_DARK = {"red": "#5a2020", "orange": "#5a3a1a", "yellow": "#565018", "green": "#1f4a2a",
            "cyan": "#154a4a", "blue": "#1f3a5a", "pink": "#5a2342", "purple": "#3d2259", "gray": "#3a3a3a"}
_HL_LIGHT = {"red": "#ffd4d4", "orange": "#ffe2c2", "yellow": "#fff3b0", "green": "#cdeccf",
             "cyan": "#c7ecec", "blue": "#d0e2ff", "pink": "#ffd3e8", "purple": "#e4d4f5", "gray": "#e4e4e4"}


def highlight_color(name: str) -> str:
    if not name:
        return ""
    return (_HL_DARK if theme.is_dark() else _HL_LIGHT).get(name, "")


def _swatch(name: str) -> QIcon:
    pix = QPixmap(14, 14)
    pix.fill(Qt.transparent)
    from PyQt5.QtGui import QPainter

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QColor(theme.color("border_strong")))
    painter.setBrush(QColor(highlight_color(name) or theme.color("input")))
    painter.drawRoundedRect(1, 1, 12, 12, 3, 3)
    painter.end()
    return QIcon(pix)


ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
ALL_STATUS = ["2", "3", "4", "5", "err"]
STATUS_LABELS = [("2", "2xx"), ("3", "3xx"), ("4", "4xx"), ("5", "5xx"), ("err", "No response")]
MIME_LABELS = [("html", "HTML"), ("json", "JSON"), ("xml", "XML"), ("js", "JavaScript"),
               ("css", "CSS"), ("text", "Text"), ("images", "Images"), ("binary", "Other binary")]
ALL_MIME = [k for k, _ in MIME_LABELS]


def mime_category(ctype: str) -> str:
    c = (ctype or "").lower()
    if "html" in c:
        return "html"
    if "json" in c:
        return "json"
    if "xml" in c:
        return "xml"
    if "javascript" in c or "ecmascript" in c:
        return "js"
    if "css" in c:
        return "css"
    if c.startswith("image/"):
        return "images"
    if c.startswith("text/") or c == "":
        return "text"
    return "binary"


def path_ext(path: str) -> str:
    seg = path.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return seg.rsplit(".", 1)[-1].lower() if "." in seg else ""


@dataclass
class ProxyFilter:
    methods: Set[str] = field(default_factory=lambda: set(ALL_METHODS))
    statuses: Set[str] = field(default_factory=lambda: set(ALL_STATUS))
    mimes: Set[str] = field(default_factory=lambda: set(ALL_MIME))
    ext_show_on: bool = False
    ext_show: str = ""
    ext_hide_on: bool = False
    ext_hide: str = ""
    hide_no_response: bool = False
    only_parameterized: bool = False
    only_highlighted: bool = False
    regex: bool = False
    case_sensitive: bool = False
    negative: bool = False
    search_bodies: bool = False

    def is_active(self) -> bool:
        return (self.methods != set(ALL_METHODS) or self.statuses != set(ALL_STATUS)
                or self.mimes != set(ALL_MIME) or self.ext_show_on or self.ext_hide_on
                or self.hide_no_response or self.only_parameterized or self.only_highlighted)

    @staticmethod
    def _ext_set(text: str) -> Set[str]:
        return {e.strip().lstrip(".").lower() for e in text.split(",") if e.strip()}

    def matches(self, rec: CaptureRecord, search_text: str = "") -> bool:
        if rec.method.upper() not in self.methods:
            return False
        cls = "err" if rec.status is None else (str(rec.status // 100) if 200 <= rec.status < 600 else "err")
        if cls not in self.statuses:
            return False
        if mime_category(rec.content_type) not in self.mimes:
            return False
        ext = path_ext(rec.path)
        if self.ext_show_on:
            allow = self._ext_set(self.ext_show)
            if allow and ext not in allow:
                return False
        if self.ext_hide_on and ext and ext in self._ext_set(self.ext_hide):
            return False
        if self.hide_no_response and rec.status is None:
            return False
        if self.only_parameterized and not self._parameterized(rec):
            return False
        if self.only_highlighted and not rec.highlight:
            return False
        if search_text.strip() and not self._search(rec, search_text):
            return False
        return True

    @staticmethod
    def _parameterized(rec: CaptureRecord) -> bool:
        return ("?" in rec.url and bool(rec.url.split("?", 1)[1])) or bool(rec.req_body)

    def _search(self, rec: CaptureRecord, text: str) -> bool:
        hay = f"{rec.method} {rec.host} {rec.path}"
        if self.search_bodies:
            hay += " " + " ".join(f"{k}: {v}" for k, v in rec.req_headers)
            hay += " " + (rec.req_body.decode("utf-8", "replace") if rec.req_body else "")
            hay += " " + " ".join(f"{k}: {v}" for k, v in rec.resp_headers)
            hay += " " + (rec.resp_body.decode("utf-8", "replace") if rec.resp_body else "")
        if self.regex:
            try:
                matched = re.search(text, hay, 0 if self.case_sensitive else re.IGNORECASE) is not None
            except re.error:
                matched = False
        elif self.case_sensitive:
            matched = text in hay
        else:
            matched = text.lower() in hay.lower()
        return (not matched) if self.negative else matched


class ProxyFilterPopup(QFrame):
    """Burp-style HTTP-history filter popup anchored under the Filter button."""

    def __init__(self, pfilter: ProxyFilter, on_change, parent: QWidget) -> None:
        super().__init__(parent, Qt.Popup)
        self.setObjectName("quickLook")
        self.setFixedWidth(600)
        self.pf = pfilter
        self.on_change = on_change
        self._building = True
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        rows = QHBoxLayout()
        rows.setSpacing(10)
        self.method_cbs = self._check_group("Method", ALL_METHODS, [(m, m) for m in ALL_METHODS], self.pf.methods, cols=2)
        self.status_cbs = self._check_group("Status code", ALL_STATUS, STATUS_LABELS, self.pf.statuses, cols=1)
        rows.addWidget(self._group_box("Method", self.method_cbs, 2), 2)
        rows.addWidget(self._group_box("Status code", self.status_cbs, 1), 1)
        self.mime_cbs = self._make_checks(MIME_LABELS, self.pf.mimes)
        rows.addWidget(self._group_box("MIME type", self.mime_cbs, 2), 2)
        root.addLayout(rows)

        mid = QHBoxLayout()
        mid.setSpacing(10)
        # request type
        rt = QGroupBox("Request type")
        rtl = QVBoxLayout(rt)
        self.hide_no_resp = QCheckBox("Hide items without responses")
        self.only_param = QCheckBox("Show only parameterized requests")
        self.only_hl = QCheckBox("Show only highlighted items")
        for cb, attr in ((self.hide_no_resp, "hide_no_response"), (self.only_param, "only_parameterized"),
                         (self.only_hl, "only_highlighted")):
            cb.setChecked(getattr(self.pf, attr))
            cb.toggled.connect(self._emit)
            rtl.addWidget(cb)
        rtl.addStretch(1)
        mid.addWidget(rt, 1)
        # extensions
        ext = QGroupBox("File extension")
        exl = QGridLayout(ext)
        self.ext_show_cb = QCheckBox("Show only:")
        self.ext_show_in = QLineEdit(self.pf.ext_show)
        self.ext_show_in.setPlaceholderText("json,xml,php")
        self.ext_hide_cb = QCheckBox("Hide:")
        self.ext_hide_in = QLineEdit(self.pf.ext_hide)
        self.ext_hide_in.setPlaceholderText("js,css,png,ico,woff")
        self.ext_show_cb.setChecked(self.pf.ext_show_on)
        self.ext_hide_cb.setChecked(self.pf.ext_hide_on)
        for w in (self.ext_show_cb, self.ext_hide_cb):
            w.toggled.connect(self._emit)
        for w in (self.ext_show_in, self.ext_hide_in):
            w.textChanged.connect(self._emit)
        exl.addWidget(self.ext_show_cb, 0, 0)
        exl.addWidget(self.ext_show_in, 0, 1)
        exl.addWidget(self.ext_hide_cb, 1, 0)
        exl.addWidget(self.ext_hide_in, 1, 1)
        mid.addWidget(ext, 1)
        root.addLayout(mid)

        # search options
        so = QGroupBox("Search options (applies to the filter box text)")
        sol = QHBoxLayout(so)
        self.opt_regex = QCheckBox("Regex")
        self.opt_case = QCheckBox("Case sensitive")
        self.opt_neg = QCheckBox("Negative search")
        self.opt_bodies = QCheckBox("Search headers && bodies")
        for cb, attr in ((self.opt_regex, "regex"), (self.opt_case, "case_sensitive"),
                         (self.opt_neg, "negative"), (self.opt_bodies, "search_bodies")):
            cb.setChecked(getattr(self.pf, attr))
            cb.toggled.connect(self._emit)
            sol.addWidget(cb)
        sol.addStretch(1)
        root.addWidget(so)

        footer = QHBoxLayout()
        show_all = QPushButton("Show all")
        show_all.setObjectName("linkButton")
        show_all.clicked.connect(self._show_all)
        footer.addWidget(show_all)
        footer.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("primaryButton")
        close.clicked.connect(self.hide)
        footer.addWidget(close)
        root.addLayout(footer)
        self._building = False

    def _make_checks(self, labels, enabled: Set[str]):
        boxes = {}
        for key, label in labels:
            cb = QCheckBox(label)
            cb.setChecked(key in enabled)
            cb.toggled.connect(self._emit)
            boxes[key] = cb
        return boxes

    def _check_group(self, _title, keys, labels, enabled, cols):  # kept for symmetry
        return self._make_checks(labels, enabled)

    @staticmethod
    def _group_box(title, boxes, cols) -> QGroupBox:
        box = QGroupBox(title)
        grid = QGridLayout(box)
        grid.setVerticalSpacing(4)
        for i, cb in enumerate(boxes.values()):
            grid.addWidget(cb, i // cols, i % cols)
        return box

    def _emit(self, *_args) -> None:
        if self._building:
            return
        self.pf.methods = {k for k, cb in self.method_cbs.items() if cb.isChecked()}
        self.pf.statuses = {k for k, cb in self.status_cbs.items() if cb.isChecked()}
        self.pf.mimes = {k for k, cb in self.mime_cbs.items() if cb.isChecked()}
        self.pf.hide_no_response = self.hide_no_resp.isChecked()
        self.pf.only_parameterized = self.only_param.isChecked()
        self.pf.only_highlighted = self.only_hl.isChecked()
        self.pf.ext_show_on = self.ext_show_cb.isChecked()
        self.pf.ext_show = self.ext_show_in.text()
        self.pf.ext_hide_on = self.ext_hide_cb.isChecked()
        self.pf.ext_hide = self.ext_hide_in.text()
        self.pf.regex = self.opt_regex.isChecked()
        self.pf.case_sensitive = self.opt_case.isChecked()
        self.pf.negative = self.opt_neg.isChecked()
        self.pf.search_bodies = self.opt_bodies.isChecked()
        self.on_change()

    def _show_all(self) -> None:
        self._building = True
        for boxes, keys in ((self.method_cbs, ALL_METHODS), (self.status_cbs, ALL_STATUS), (self.mime_cbs, ALL_MIME)):
            for key, cb in boxes.items():
                cb.setChecked(True)
        for cb in (self.hide_no_resp, self.only_param, self.only_hl, self.ext_show_cb, self.ext_hide_cb,
                   self.opt_regex, self.opt_case, self.opt_neg, self.opt_bodies):
            cb.setChecked(False)
        self.ext_show_in.clear()
        self.ext_hide_in.clear()
        self._building = False
        self._emit()

    def popup(self, anchor: QWidget) -> None:
        pos = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 4))
        self.move(pos)
        self.show()


class _CaptureTree(QTreeWidget):
    """Tree that honours per-item highlight colours (a global stylesheet otherwise
    suppresses item BackgroundRole brushes)."""

    def drawRow(self, painter, option, index) -> None:
        item = self.itemFromIndex(index)
        brush = item.background(0) if item is not None else QBrush()
        highlighted = brush.style() != Qt.NoBrush
        selected = self.selectionModel().isSelected(index)
        if highlighted and not selected:
            painter.fillRect(option.rect, brush)
        super().drawRow(painter, option, index)
        if highlighted and selected:
            # selection paints an opaque background over the row, so overlay the
            # highlight colour (semi-transparent) on top so both stay visible.
            overlay = QColor(brush.color())
            overlay.setAlpha(130)
            painter.fillRect(option.rect, overlay)


class InterceptTab(QWidget):
    """Hold requests before they are forwarded; edit, Forward, Drop, Forward all."""

    queueChanged = pyqtSignal(int)

    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self.app = app
        self.current: Optional[object] = None
        self.queue: list = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(8)

        bar = QHBoxLayout()
        self.toggle = QPushButton("Intercept is off")
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.clicked.connect(self._toggle)
        self.forward_btn = QPushButton("Forward")
        self.forward_btn.setObjectName("primaryButton")
        self.forward_btn.clicked.connect(self._forward)
        self.drop_btn = QPushButton("Drop")
        self.drop_btn.setObjectName("dangerFilledButton")
        self.drop_btn.clicked.connect(self._drop)
        self.forward_all_btn = QPushButton("Forward all")
        self.forward_all_btn.clicked.connect(self._forward_all)
        self.resp_btn = QPushButton("Intercept response")
        self.resp_btn.setCheckable(True)
        self.resp_btn.setToolTip("Also hold this request's response before it reaches the client")
        self.resp_btn.toggled.connect(self._set_resp_intercept)
        for b in (self.toggle, self.forward_btn, self.drop_btn, self.forward_all_btn, self.resp_btn):
            b.setCursor(Qt.PointingHandCursor)
            bar.addWidget(b)
        bar.addStretch(1)
        self.info = QLabel()
        self.info.setObjectName("muted")
        bar.addWidget(self.info)
        root.addLayout(bar)

        self.editor = EditorWithSearch(language="httpreq")
        self.editor.editor.setPlaceholderText(
            "Turn on Intercept, then captured requests pause here — edit the raw request if you like, "
            "then Forward (or Drop).")
        self.editor.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        self.editor.editor.customContextMenuRequested.connect(self._editor_menu)
        root.addLayout(editor_tools(self.editor))
        root.addWidget(self.editor, 1)

        app.proxy.intercepted.connect(self._on_intercepted)
        app.proxy.intercept_changed.connect(self._on_intercept_changed)
        self._update_controls()

    def _toggle(self) -> None:
        self.app.proxy.set_intercept(not self.app.proxy.intercept_on())

    def _set_resp_intercept(self, on: bool) -> None:
        if self.current is not None and getattr(self.current, "kind", "request") == "request":
            self.current.intercept_response = on
        if self.resp_btn.isChecked() != on:
            self.resp_btn.blockSignals(True)
            self.resp_btn.setChecked(on)
            self.resp_btn.blockSignals(False)

    def _editor_menu(self, pos) -> None:
        editor = self.editor.editor
        menu = editor.createStandardContextMenu()
        menu.addSeparator()
        do_intercept = menu.addMenu("Do intercept")
        action = do_intercept.addAction("Response to this request")
        action.setCheckable(True)
        is_request = self.current is not None and getattr(self.current, "kind", "request") == "request"
        action.setEnabled(is_request)
        action.setChecked(bool(is_request and self.current.intercept_response))
        action.toggled.connect(self._set_resp_intercept)
        menu.exec_(editor.mapToGlobal(pos))

    def _held_count(self) -> int:
        return (1 if self.current else 0) + len(self.queue)

    def _on_intercept_changed(self, on: bool) -> None:
        if not on:  # releasing forwards everything; clear the UI queue
            self.current = None
            self.queue.clear()
            self.editor.editor.setPlainText("")
            self.queueChanged.emit(0)
        self._update_controls()

    def _on_intercepted(self, held) -> None:
        self.queue.append(held)
        if self.current is None:
            self._show_next()
        else:
            self.queueChanged.emit(self._held_count())
            self._update_controls()

    def _show_next(self) -> None:
        self.current = self.queue.pop(0) if self.queue else None
        self.editor.editor.setPlainText(self.current.raw() if self.current else "")
        self.queueChanged.emit(self._held_count())
        self._update_controls()

    def _release_current(self, action: str) -> None:
        if not self.current:
            return
        if action == "forward":
            self.current.apply_raw(self.editor.editor.toPlainText())
        self.current.action = action
        self.current.event.set()
        self.current = None
        self._show_next()

    def _forward(self) -> None:
        self._release_current("forward")

    def _drop(self) -> None:
        self._release_current("drop")

    def _forward_all(self) -> None:
        self._release_current("forward")
        for held in self.queue:
            held.action = "forward"
            held.event.set()
        self.queue.clear()
        self._show_next()

    def _update_controls(self) -> None:
        on = self.app.proxy.intercept_on()
        self.toggle.setChecked(on)
        self.toggle.setText("Intercept is on" if on else "Intercept is off")
        self.toggle.setObjectName("accentButton" if on else "")
        self.toggle.style().unpolish(self.toggle)
        self.toggle.style().polish(self.toggle)
        has = self.current is not None
        is_request = has and getattr(self.current, "kind", "request") == "request"
        for b in (self.forward_btn, self.drop_btn, self.forward_all_btn):
            b.setEnabled(has)
        self.resp_btn.setEnabled(is_request)
        self.resp_btn.blockSignals(True)
        self.resp_btn.setChecked(bool(is_request and self.current.intercept_response))
        self.resp_btn.blockSignals(False)
        if not on:
            self.info.setText("Intercept is off — traffic passes straight through.")
        elif has and self.current.kind == "response":
            self.info.setText(f"Response · {self.current.label}  ·  {self._held_count()} held")
        elif has:
            self.info.setText(f"{self.current.method} {self.current.host}{self.current.path}  ·  {self._held_count()} held")
        else:
            self.info.setText("Intercept is on — waiting for requests…")


COLUMNS = ["#", "Method", "Host", "Path", "Params", "Edited", "Status", "Length", "MIME", "TLS", "IP", "Time"]
(COL_NUM, COL_METHOD, COL_HOST, COL_PATH, COL_PARAMS, COL_EDITED, COL_STATUS,
 COL_LENGTH, COL_MIME, COL_TLS, COL_IP, COL_TIME) = range(len(COLUMNS))


class _CaptureItem(QTreeWidgetItem):
    """Row whose columns sort numerically where it makes sense (#, status, length, time)."""

    def _key(self, col: int):
        rec = self.data(0, ROLE)
        if rec is None:
            return (self.text(col) or "").lower()
        if col == 0:
            return rec.id
        if col == COL_STATUS:
            return rec.status if rec.status is not None else -1
        if col == COL_LENGTH:
            return len(rec.resp_body)
        if col == COL_TIME:
            return rec.elapsed_ms
        return (self.text(col) or "").lower()

    def __lt__(self, other: "QTreeWidgetItem") -> bool:
        tree = self.treeWidget()
        col = tree.sortColumn() if tree is not None else 0
        try:
            return self._key(col) < other._key(col)
        except (TypeError, AttributeError):
            return self.text(col) < other.text(col)


class ProxyView(QWidget):
    """Burp-style main-area view: Intercept + HTTP history tabs."""

    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self.app = app
        self.pfilter = ProxyFilter()
        self._filter_popup: Optional[ProxyFilterPopup] = None
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        top = QHBoxLayout()
        self.toggle_btn = QPushButton("Start proxy")
        self.toggle_btn.setObjectName("accentButton")
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._toggle)
        top.addWidget(self.toggle_btn)
        self.status = QLabel()
        self.status.setObjectName("muted")
        top.addWidget(self.status, 1)
        root.addLayout(top)

        self.section_tabs = QTabWidget()
        self.section_tabs.setDocumentMode(True)
        self.intercept_tab = InterceptTab(app)
        self.intercept_tab.queueChanged.connect(self._update_intercept_title)
        self.section_tabs.addTab(self.intercept_tab, "Intercept")

        history = QWidget()
        hist_layout = QVBoxLayout(history)
        hist_layout.setContentsMargins(0, 6, 0, 0)
        hist_layout.setSpacing(6)
        bar = QHBoxLayout()
        bar.addStretch(1)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by host, path or method")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMaximumWidth(280)
        self.filter.textChanged.connect(self._on_filter_changed)
        bar.addWidget(self.filter)
        self.filter_btn = tool_button(G.FILTER, "Filter captured traffic")
        self.filter_btn.clicked.connect(self._open_filter)
        bar.addWidget(self.filter_btn)
        clear_btn = tool_button(G.CLEAR, "Clear captured traffic")
        clear_btn.clicked.connect(self.clear)
        bar.addWidget(clear_btn)
        hist_layout.addLayout(bar)

        vsplit = QSplitter(Qt.Vertical)
        self.tree = _CaptureTree()
        self.tree.setObjectName("consoleTree")
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.currentItemChanged.connect(lambda *_: self._show_selected())
        self.tree.itemDoubleClicked.connect(lambda *_: self.app.open_proxy_entry(self._current_record()) if self._current_record() else None)
        head = self.tree.header()
        head.setSectionResizeMode(COL_PATH, QHeaderView.Stretch)
        head.setSectionResizeMode(COL_HOST, QHeaderView.Interactive)
        head.setSectionResizeMode(COL_IP, QHeaderView.Interactive)
        for col in (COL_NUM, COL_METHOD, COL_PARAMS, COL_EDITED, COL_STATUS, COL_LENGTH, COL_MIME, COL_TLS, COL_TIME):
            head.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.tree.setColumnWidth(COL_HOST, 200)
        self.tree.setColumnWidth(COL_IP, 120)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(COL_NUM, Qt.AscendingOrder)
        vsplit.addWidget(self.tree)

        detail = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 6, 6, 0)
        ll.setSpacing(4)
        req_header = QHBoxLayout()
        req_label = QLabel("Request")
        req_label.setObjectName("h2")
        req_header.addWidget(req_label)
        self.req_mode = QComboBox()
        self.req_mode.setObjectName("flatCombo")
        self.req_mode.addItems(["Edited request", "Original request"])
        self.req_mode.currentIndexChanged.connect(lambda *_: self._render_request())
        self.req_mode.hide()
        req_header.addWidget(self.req_mode)
        self.req_view = EditorWithSearch(language="httpreq", read_only=True)
        self.req_view.editor.setPlaceholderText("Select a captured call to see its request and response.")
        req_header.addLayout(editor_tools(self.req_view))
        ll.addLayout(req_header)
        ll.addWidget(self.req_view, 1)
        self._shown_rec: Optional[CaptureRecord] = None
        detail.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 6, 0, 0)
        self.resp_mode = QComboBox()
        self.resp_mode.setObjectName("flatCombo")
        self.resp_mode.addItems(["Edited response", "Original response"])
        self.resp_mode.currentIndexChanged.connect(lambda *_: self._render_response())
        self.resp_mode.hide()
        self.response = ResponsePanel()
        self.response.add_header_widget(self.resp_mode)  # same row as the "Response" title
        rl.addWidget(self.response, 1)
        detail.addWidget(right)
        detail.setSizes([560, 640])
        vsplit.addWidget(detail)
        vsplit.setSizes([260, 440])
        hist_layout.addWidget(vsplit, 1)
        self.section_tabs.addTab(history, "HTTP history")
        self.section_tabs.setCurrentWidget(history)
        root.addWidget(self.section_tabs, 1)

        app.proxy.captured.connect(self.add_record)
        app.proxy.started.connect(lambda *_: self._refresh_state())
        app.proxy.stopped.connect(lambda *_: self._refresh_state())
        app.proxy.failed.connect(self._on_failed)
        app.proxy.intercepted.connect(lambda *_: self.section_tabs.setCurrentWidget(self.intercept_tab))
        theme.manager().changed.connect(lambda *_: self._reapply_highlights())
        self._refresh_state()

    def _update_intercept_title(self, n: int) -> None:
        self.section_tabs.setTabText(0, f"Intercept ({n})" if n else "Intercept")

    # -- lifecycle --
    def _toggle(self) -> None:
        if self.app.proxy.running:
            self.app.proxy.stop()
        else:
            _start_proxy(self.app)

    def _refresh_state(self) -> None:
        running = self.app.proxy.running
        self.status.setStyleSheet("")
        self.toggle_btn.setText("Stop proxy" if running else "Start proxy")
        if running:
            self.status.setText(
                f"Listening on <b>{self.app.proxy.display_address()}</b> — set this as your client's HTTP/HTTPS "
                "proxy. For HTTPS, export &amp; trust the CA (Settings → Proxy)."
            )
        else:
            self.status.setText("Stopped. Start the proxy, then point a browser/app at this address to capture traffic.")
        if hasattr(self.app, "sidebar"):
            self.app.sidebar.proxy.sync()

    def _on_failed(self, message: str) -> None:
        self.toggle_btn.setText("Start proxy")
        self.status.setText("⚠ " + message + "  (is another instance using this port? change it in Settings → Proxy)")
        self.status.setStyleSheet(f"color: {theme.color('danger')};")
        self.app.toast(message, error=True)

    # -- captures --
    def add_record(self, rec: CaptureRecord) -> None:
        self.app.proxy_captures.append(rec)
        status = str(rec.status) if rec.status else (rec.error.split(":")[0] if rec.error else "—")
        cells = [""] * len(COLUMNS)
        cells[COL_NUM] = str(rec.id)
        cells[COL_METHOD] = rec.method
        cells[COL_HOST] = rec.host
        cells[COL_PATH] = rec.path
        cells[COL_PARAMS] = "✓" if has_params(rec) else ""
        cells[COL_EDITED] = "✓" if (rec.edited or rec.resp_edited) else ""
        cells[COL_STATUS] = status
        cells[COL_LENGTH] = format_size(len(rec.resp_body)) if rec.status else "—"
        cells[COL_MIME] = mime_label(rec.content_type) if rec.status else ""
        cells[COL_TLS] = "✓" if rec.scheme == "https" else ""
        cells[COL_IP] = rec.ip
        cells[COL_TIME] = format_ms(rec.elapsed_ms) if rec.status else "—"
        item = _CaptureItem(cells)
        item.setData(0, ROLE, rec)
        item.setForeground(COL_METHOD, QColor(theme.method_color(rec.method)))
        item.setForeground(COL_STATUS, QColor(theme.status_color(rec.status)) if rec.status else theme.qcolor("danger"))
        for col in (COL_PARAMS, COL_EDITED, COL_TLS):
            item.setTextAlignment(col, Qt.AlignCenter)
        item.setForeground(COL_EDITED, theme.qcolor("accent"))
        item.setForeground(COL_TLS, theme.qcolor("success"))
        item.setToolTip(COL_PATH, rec.url)
        self.tree.addTopLevelItem(item)
        self._apply_highlight(item, rec)
        self._apply_filter_to(item)
        while self.tree.topLevelItemCount() > 3000:
            self.tree.takeTopLevelItem(0)
        self.tree.scrollToItem(item)

    def clear(self) -> None:
        self.tree.clear()
        self.app.proxy_captures.clear()
        self._show(None)

    def _open_filter(self) -> None:
        if self._filter_popup is None:
            self._filter_popup = ProxyFilterPopup(self.pfilter, self._on_filter_changed, self)
        self._filter_popup.popup(self.filter_btn)

    def _on_filter_changed(self, *_args) -> None:
        self._apply_filter()
        active = self.pfilter.is_active() or bool(self.filter.text().strip())
        self.filter_btn.setIcon(theme.icon(G.FILTER, "accent" if active else "muted", 16))

    def _apply_filter(self, _text: str = "") -> None:
        for i in range(self.tree.topLevelItemCount()):
            self._apply_filter_to(self.tree.topLevelItem(i))

    def _apply_filter_to(self, item: QTreeWidgetItem) -> None:
        rec = item.data(0, ROLE)
        item.setHidden(not self.pfilter.matches(rec, self.filter.text()))

    def _current_record(self) -> Optional[CaptureRecord]:
        item = self.tree.currentItem()
        return item.data(0, ROLE) if item is not None else None

    def _show_selected(self) -> None:
        self._show(self._current_record())

    def _show(self, rec: Optional[CaptureRecord]) -> None:
        self._shown_rec = rec
        if rec is None:
            self.req_mode.hide()
            self.resp_mode.hide()
            self.req_view.editor.setPlainText("")
            self.response.clear()
            return
        self.req_mode.setVisible(rec.edited)
        self.resp_mode.setVisible(rec.resp_edited)
        for combo in (self.req_mode, self.resp_mode):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._render_request()
        self._render_response()

    def _render_request(self) -> None:
        rec = self._shown_rec
        if rec is None:
            return
        self.req_view.editor.setPlainText(_raw_request(rec, original=self.req_mode.currentIndex() == 1))

    def _render_response(self) -> None:
        rec = self._shown_rec
        if rec is None:
            return
        self.response.show_result(record_to_result(rec, original=self.resp_mode.currentIndex() == 1))

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        if item not in self.tree.selectedItems():
            self.tree.setCurrentItem(item)
        rec = item.data(0, ROLE)
        selected = self.tree.selectedItems() or [item]
        menu = QMenu(self)
        menu.addAction(theme.icon(G.NEW_WINDOW), "Open in window", lambda: self.app.open_proxy_entry(rec))
        menu.addSeparator()
        menu.addAction(theme.icon(G.REFRESH), "Send to Repeater", lambda: self.app.open_repeater(record_to_model(rec)))
        menu.addAction(theme.icon(G.FILTER), "Send to Intruder", lambda: self.app.open_intruder(record_to_model(rec)))
        menu.addAction(theme.icon(G.SAVE), "Save to Collection", lambda: self.app.save_model_to_collection(record_to_model(rec)))
        menu.addSeparator()
        hl_menu = menu.addMenu("Highlight")
        for name in HIGHLIGHT_NAMES:
            hl_menu.addAction(_swatch(name), name.capitalize(), lambda _c=False, n=name: self._set_highlight(selected, n))
        hl_menu.addSeparator()
        hl_menu.addAction("None", lambda: self._set_highlight(selected, ""))
        menu.addSeparator()
        menu.addAction(theme.icon(G.COPY), "Copy URL", lambda: self.app.copy_text_to_clipboard(rec.url))
        label = f"Delete {len(selected)} items" if len(selected) > 1 else "Delete"
        menu.addAction(theme.icon(G.DELETE, "danger"), label, lambda: self._delete(selected))
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def _set_highlight(self, items, name: str) -> None:
        for item in items:
            rec = item.data(0, ROLE)
            rec.highlight = name
            self._apply_highlight(item, rec)
        if self.pfilter.only_highlighted:
            self._apply_filter()

    def _apply_highlight(self, item: QTreeWidgetItem, rec: CaptureRecord) -> None:
        color = highlight_color(rec.highlight)
        brush = QBrush(QColor(color)) if color else QBrush()
        for col in range(self.tree.columnCount()):
            item.setBackground(col, brush)

    def _reapply_highlights(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            self._apply_highlight(item, item.data(0, ROLE))

    def _delete(self, items) -> None:
        for item in list(items):
            rec = item.data(0, ROLE)
            if rec in self.app.proxy_captures:
                self.app.proxy_captures.remove(rec)
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))


class ProxyPanel(QWidget):
    """Compact sidebar panel: proxy controls + status. The capture list and the
    request/response view live in the main-area ProxyView."""

    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self.app = app
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        from .sidebar import _PanelHeader  # local import avoids a cycle

        header = _PanelHeader("Proxy")
        self.clear_btn = tool_button(G.CLEAR, "Clear captured traffic")
        self.clear_btn.clicked.connect(lambda: self.app.proxy_view.clear())
        header.add(self.clear_btn)
        root.addWidget(header)

        row = QHBoxLayout()
        row.setContentsMargins(10, 0, 10, 0)
        self.toggle_btn = QPushButton("Start proxy")
        self.toggle_btn.setObjectName("accentButton")
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._toggle)
        row.addWidget(self.toggle_btn)
        row.addStretch(1)
        root.addLayout(row)

        self.status = QLabel()
        self.status.setObjectName("faint")
        self.status.setWordWrap(True)
        self.status.setContentsMargins(12, 2, 12, 2)
        root.addWidget(self.status)
        note = QLabel("Captured calls and the request / response view appear in the main panel.")
        note.setObjectName("faint")
        note.setWordWrap(True)
        note.setContentsMargins(12, 2, 12, 2)
        root.addWidget(note)
        root.addStretch(1)

        app.proxy.started.connect(lambda *_: self.sync())
        app.proxy.stopped.connect(lambda *_: self.sync())
        self.sync()

    def _toggle(self) -> None:
        if self.app.proxy.running:
            self.app.proxy.stop()
        else:
            _start_proxy(self.app)

    def sync(self) -> None:
        running = self.app.proxy.running
        self.toggle_btn.setText("Stop proxy" if running else "Start proxy")
        self.status.setText(
            f"Running · listening on <b>{self.app.proxy.display_address()}</b>" if running else "Stopped."
        )
