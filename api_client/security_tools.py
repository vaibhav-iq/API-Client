"""Security-testing UI: encoder/decoder toolkit, JWT workbench, Repeater,
Intruder (fuzzer) and the multi-identity IDOR/BOLA matrix.

For authorized API security testing only. All request execution reuses the same
engine and worker model as the rest of the app.
"""
import json
import statistics
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlsplit

from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import security, theme
from .code_editor import EditorWithSearch
from .config_store import AppSettings
from .dialogs import ThemedDialog
from .engine import RunResult, VariableContext, new_session, run_request
from .kv_table import KeyValueTable
from .models import AuthConfig, KeyValue, RequestBody, RequestModel, requests_in
from .response_panel import SEVERITY_COLOR, ResponsePanel
from .theme import G
from .widgets import MethodCombo, format_ms, format_size, tool_button
from .workers import RequestWorker

if TYPE_CHECKING:  # pragma: no cover
    from .main_window import MainWindow


# ===========================================================================
# Encoder / decoder & hashing toolkit
# ===========================================================================
class EncoderDialog(ThemedDialog):
    def __init__(self, parent: QWidget, initial: str = "") -> None:
        super().__init__(parent, "Encoder / Decoder", 820, 560)
        row = QHBoxLayout()
        row.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(4)
        op_label = QLabel("OPERATION")
        op_label.setObjectName("sectionTitle")
        left.addWidget(op_label)
        self.ops = QListWidget()
        self.ops.setObjectName("settingsNav")
        self.ops.setFixedWidth(200)
        for name in security.TRANSFORMS:
            self.ops.addItem(QListWidgetItem(name))
        self.ops.currentTextChanged.connect(lambda *_: self._run())
        left.addWidget(self.ops, 1)
        left_wrap = QWidget()
        left_wrap.setLayout(left)
        left_wrap.setFixedWidth(210)
        row.addWidget(left_wrap)

        right = QVBoxLayout()
        right.setSpacing(6)
        in_label = QLabel("Input")
        in_label.setObjectName("sectionTitle")
        right.addWidget(in_label)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Type or paste text to transform…")
        self.input.setPlainText(initial)
        self.input.textChanged.connect(self._run)
        right.addWidget(self.input, 1)
        out_row = QHBoxLayout()
        out_label = QLabel("Output")
        out_label.setObjectName("sectionTitle")
        out_row.addWidget(out_label)
        out_row.addStretch(1)
        copy_btn = QPushButton("Copy output")
        copy_btn.setObjectName("linkButton")
        copy_btn.clicked.connect(self._copy)
        use_btn = QPushButton("Output → Input")
        use_btn.setObjectName("linkButton")
        use_btn.clicked.connect(lambda: self.input.setPlainText(self.output.toPlainText()))
        out_row.addWidget(use_btn)
        out_row.addWidget(copy_btn)
        right.addLayout(out_row)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        right.addWidget(self.output, 1)
        row.addLayout(right, 1)
        self.body_layout.addLayout(row, 1)
        self.add_button("Close", self.accept, primary=True)
        self.ops.setCurrentRow(0)

    def _run(self) -> None:
        item = self.ops.currentItem()
        if item is None:
            return
        try:
            self.output.setPlainText(security.transform(item.text(), self.input.toPlainText()))
            self.output.setStyleSheet("")
        except ValueError as exc:
            self.output.setPlainText(f"⚠ {exc}")
            self.output.setStyleSheet(f"color: {theme.color('danger')};")

    def _copy(self) -> None:
        from PyQt5.QtWidgets import QApplication

        QApplication.clipboard().setText(self.output.toPlainText())


# ===========================================================================
# JWT workbench
# ===========================================================================
class JwtDialog(ThemedDialog):
    def __init__(self, parent: QWidget, token: str = "") -> None:
        super().__init__(parent, "JWT Workbench", 900, 640)
        self.body_layout.setSpacing(8)
        tok_label = QLabel("Encoded token")
        tok_label.setObjectName("sectionTitle")
        self.body_layout.addWidget(tok_label)
        self.token_edit = QPlainTextEdit()
        self.token_edit.setPlaceholderText("Paste a JWT (header.payload.signature)…")
        self.token_edit.setMaximumHeight(90)
        self.token_edit.setPlainText(token)
        self.token_edit.textChanged.connect(self._decode)
        self.body_layout.addWidget(self.token_edit)

        split = QSplitter(Qt.Horizontal)
        decoded = QWidget()
        dl = QVBoxLayout(decoded)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(4)
        h_label = QLabel("Header")
        h_label.setObjectName("sectionTitle")
        self.header_view = EditorWithSearch(language="json", read_only=True, line_numbers=False)
        self.header_view.setMaximumHeight(120)
        p_label = QLabel("Payload")
        p_label.setObjectName("sectionTitle")
        self.payload_view = EditorWithSearch(language="json", read_only=True, line_numbers=False)
        dl.addWidget(h_label)
        dl.addWidget(self.header_view)
        dl.addWidget(p_label)
        dl.addWidget(self.payload_view, 1)
        split.addWidget(decoded)

        analysis = QWidget()
        al = QVBoxLayout(analysis)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(4)
        a_label = QLabel("Analysis")
        a_label.setObjectName("sectionTitle")
        al.addWidget(a_label)
        self.findings = QTreeWidget()
        self.findings.setObjectName("resultsTree")
        self.findings.setHeaderHidden(True)
        self.findings.setColumnCount(2)
        self.findings.setRootIsDecorated(False)
        self.findings.header().setSectionResizeMode(0, QHeaderView.Fixed)
        self.findings.header().setStretchLastSection(True)
        self.findings.setColumnWidth(0, 80)
        al.addWidget(self.findings, 1)

        form = QFormLayout()
        self.secret_edit = QLineEdit()
        self.secret_edit.setPlaceholderText("HMAC secret to test / re-sign with")
        form.addRow("Secret", self.secret_edit)
        al.addLayout(form)
        btns = QHBoxLayout()
        for text, slot in (("Test secret", self._test_secret), ("Guess secret", self._guess),
                           ("alg:none", self._alg_none), ("Re-sign (HS256)", self._resign)):
            b = QPushButton(text)
            b.setObjectName("linkButton")
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)
        al.addLayout(btns)
        self.result_label = QLabel("")
        self.result_label.setObjectName("muted")
        self.result_label.setWordWrap(True)
        al.addWidget(self.result_label)
        self.forged = QLineEdit()
        self.forged.setReadOnly(True)
        self.forged.setPlaceholderText("Forged / re-signed token appears here")
        al.addWidget(self.forged)
        split.addWidget(analysis)
        split.setSizes([420, 460])
        self.body_layout.addWidget(split, 1)
        self.add_button("Close", self.accept, primary=True)
        self._decode()

    def _parts(self) -> security.JwtParts:
        return security.jwt_decode(self.token_edit.toPlainText())

    def _decode(self) -> None:
        parts = self._parts()
        self.header_view.editor.setPlainText(json.dumps(parts.header, indent=2) if parts.header else "")
        self.payload_view.editor.setPlainText(json.dumps(parts.payload, indent=2) if parts.payload else "")
        self.findings.clear()
        for finding in security.jwt_analyze(self.token_edit.toPlainText()):
            item = QTreeWidgetItem([finding.severity.upper(), f"{finding.title} — {finding.detail}" if finding.detail else finding.title])
            item.setForeground(0, theme.qcolor(SEVERITY_COLOR.get(finding.severity, "muted")))
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            self.findings.addTopLevelItem(item)

    def _test_secret(self) -> None:
        ok = security.jwt_verify_hs(self.token_edit.toPlainText(), self.secret_edit.text())
        self.result_label.setText("✔ Signature verifies with this secret." if ok else "✘ Secret does not verify the signature.")
        self.result_label.setStyleSheet(f"color: {theme.color('success' if ok else 'danger')};")

    def _guess(self) -> None:
        guessed = security.jwt_guess_secret(self.token_edit.toPlainText())
        if guessed:
            self.secret_edit.setText(guessed)
            self.result_label.setText(f"✔ Weak secret found: '{guessed}'")
            self.result_label.setStyleSheet(f"color: {theme.color('danger')};")
        else:
            self.result_label.setText("No match in the common-secret wordlist.")
            self.result_label.setStyleSheet(f"color: {theme.color('muted')};")

    def _alg_none(self) -> None:
        self.forged.setText(security.jwt_none_token(self._parts()))
        self.result_label.setText("alg:none token generated (unsigned). Works only if the server trusts it.")
        self.result_label.setStyleSheet(f"color: {theme.color('muted')};")

    def _resign(self) -> None:
        try:
            self.forged.setText(security.jwt_resign_hs(self._parts(), self.secret_edit.text() or "secret"))
            self.result_label.setText("Re-signed with HS256 using the secret above.")
            self.result_label.setStyleSheet(f"color: {theme.color('muted')};")
        except Exception as exc:  # noqa: BLE001
            self.result_label.setText(f"Could not re-sign: {exc}")
            self.result_label.setStyleSheet(f"color: {theme.color('danger')};")


# ===========================================================================
# Repeater
# ===========================================================================
class RepeaterDialog(ThemedDialog):
    def __init__(self, app: "MainWindow", model: RequestModel, collection_id: Optional[str]) -> None:
        super().__init__(app, f"Repeater · {model.name}", 1180, 760)
        self.setWindowModality(Qt.NonModal)
        self.app = app
        self.model = model.clone(fresh_id=True)
        self.collection_id = collection_id
        self._worker: Optional[RequestWorker] = None
        self._sends: List[RunResult] = []

        split = QSplitter(Qt.Horizontal)

        # -- request editor --
        req = QWidget()
        rl = QVBoxLayout(req)
        rl.setContentsMargins(0, 0, 8, 0)
        rl.setSpacing(8)
        url_row = QHBoxLayout()
        self.method = MethodCombo()
        self.method.set_method(self.model.method)
        self.url = QLineEdit(self.model.url)
        self.url.returnPressed.connect(self.send)
        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("primaryButton")
        self.send_btn.setMinimumWidth(90)
        self.send_btn.clicked.connect(self.send)
        url_row.addWidget(self.method)
        url_row.addWidget(self.url, 1)
        url_row.addWidget(self.send_btn)
        rl.addLayout(url_row)
        self.headers = KeyValueTable(title="Headers")
        self.headers.set_items(self.model.headers)
        rl.addWidget(self.headers, 1)
        body_label = QLabel("Body (raw)")
        body_label.setObjectName("sectionTitle")
        rl.addWidget(body_label)
        self.body = EditorWithSearch(language="json")
        self.body.editor.setPlainText(self.model.body.raw)
        rl.addWidget(self.body, 1)
        split.addWidget(req)

        # -- response + send history --
        right = QWidget()
        rr = QVBoxLayout(right)
        rr.setContentsMargins(8, 0, 0, 0)
        rr.setSpacing(6)
        hist_label = QLabel("SEND HISTORY")
        hist_label.setObjectName("sectionTitle")
        rr.addWidget(hist_label)
        self.history = QListWidget()
        self.history.setMaximumHeight(120)
        self.history.currentRowChanged.connect(self._show_from_history)
        rr.addWidget(self.history)
        self.response = ResponsePanel()
        rr.addWidget(self.response, 1)
        split.addWidget(right)
        split.setSizes([520, 660])
        self.body_layout.addWidget(split, 1)
        self.add_button("Close", self.close)

    def _collect(self) -> RequestModel:
        self.model.method = self.method.currentText()
        self.model.url = self.url.text().strip()
        self.model.headers = self.headers.items()
        raw = self.body.editor.toPlainText()
        self.model.body = RequestBody(mode="raw" if raw.strip() else "none", raw=raw,
                                      raw_language=self.model.body.raw_language or "json")
        return self.model

    def send(self) -> None:
        if self._worker is not None:
            return
        model = self._collect()
        inherited = self.app.inherited_auths(self.collection_id, model.id) if self.collection_id else []
        worker = RequestWorker(model, self.app.variable_context(self.collection_id), self.app.settings, inherited)
        worker.finished_result.connect(self._finished)
        self._worker = worker
        self.response.show_loading()
        self.send_btn.setText("…")
        self.send_btn.setEnabled(False)
        worker.start()

    def _finished(self, result: RunResult) -> None:
        self.app.retire_worker(self._worker)
        self._worker = None
        self.send_btn.setText("Send")
        self.send_btn.setEnabled(True)
        self._record(result)

    def _record(self, result: RunResult) -> None:
        self._sends.append(result)
        resp = result.response
        when = f"#{len(self._sends)}"
        status = f"{resp.status} {resp.reason}" if resp else ("cancelled" if result.cancelled else "error")
        timing = format_ms(resp.elapsed_ms) if resp else "—"
        item = QListWidgetItem(f"{when}  {self.model.method}  ·  {status}  ·  {timing}")
        if resp is not None:
            item.setForeground(QColor(theme.status_color(resp.status)))
        else:
            item.setForeground(theme.qcolor("danger"))
        self.history.addItem(item)
        self.history.setCurrentRow(self.history.count() - 1)
        self.response.show_result(result)

    def _show_from_history(self, row: int) -> None:
        if 0 <= row < len(self._sends):
            self.response.show_result(self._sends[row])

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.app.retire_worker(self._worker)
        super().closeEvent(event)


# ===========================================================================
# Shared batch worker for Intruder / IDOR
# ===========================================================================
class BatchWorker(QThread):
    progress = pyqtSignal(int, object)  # index, RunResult
    done = pyqtSignal()

    def __init__(self, items: Sequence[Tuple[RequestModel, VariableContext, List[AuthConfig]]], settings: AppSettings) -> None:
        super().__init__()
        self.items = list(items)
        self.settings = settings
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def run(self) -> None:
        session = new_session(self.settings)
        try:
            for index, (model, ctx, inherited) in enumerate(self.items):
                if self._stopped:
                    break
                try:
                    result = run_request(model, ctx, self.settings, inherited,
                                         session=session, cancelled=lambda: self._stopped)
                except Exception as exc:  # noqa: BLE001
                    result = RunResult(error=f"{type(exc).__name__}: {exc}", method=model.method, url=model.url)
                self.progress.emit(index, result)
        finally:
            session.close()
        self.done.emit()


# ===========================================================================
# Intruder (fuzzer)
# ===========================================================================
class IntruderDialog(ThemedDialog):
    """Burp-style fuzzer: mark parameter values with $$…$$ and attack them."""

    def __init__(self, app: "MainWindow", model: RequestModel, collection_id: Optional[str]) -> None:
        super().__init__(app, f"Intruder · {model.name}", 1220, 880)
        self.setWindowModality(Qt.NonModal)
        self.app = app
        self.base = model.clone(fresh_id=True)
        self.collection_id = collection_id
        self.worker: Optional[BatchWorker] = None
        self._rows_meta: List[Tuple[str, str]] = []  # (position label, raw payload) per request
        self._baseline_len: Optional[int] = None
        self._times: List[float] = []
        body_text, self._body_kind, self._raw_language = _body_as_text(self.base.body)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 10, 0)
        ll.setSpacing(6)
        note = QLabel("Select a parameter value and click <b>Mark $$</b> (or <b>Auto-mark</b>) to set payload "
                      "positions. Each marked value becomes <b>$$value$$</b>.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        ll.addWidget(note)

        tgt_row = QHBoxLayout()
        self.method = MethodCombo()
        self.method.set_method(self.base.method)
        self.url = QLineEdit(self.base.url)
        tgt_row.addWidget(self.method)
        tgt_row.addWidget(self.url, 1)
        ll.addLayout(tgt_row)

        tools = QHBoxLayout()
        tools.setSpacing(4)
        for text, slot in (("Mark $$", self._mark), ("Auto-mark", self._auto_mark), ("Clear $$", self._clear)):
            b = QPushButton(text)
            b.setObjectName("linkButton")
            b.setFocusPolicy(Qt.NoFocus)  # keep selection/focus in the editor
            b.clicked.connect(slot)
            tools.addWidget(b)
        tools.addStretch(1)
        self.positions_label = QLabel("0 positions")
        self.positions_label.setObjectName("faint")
        tools.addWidget(self.positions_label)
        ll.addLayout(tools)

        hdr_label = QLabel("HEADERS")
        hdr_label.setObjectName("sectionTitle")
        ll.addWidget(hdr_label)
        self.headers = QPlainTextEdit()
        self.headers.setPlaceholderText("Name: value (one per line)")
        self.headers.setPlainText("\n".join(f"{kv.key}: {kv.value}" for kv in self.base.headers if kv.enabled and kv.key))
        self.headers.setMaximumHeight(90)
        self.headers.textChanged.connect(self._update_positions)
        ll.addWidget(self.headers)

        body_label = QLabel("BODY")
        body_label.setObjectName("sectionTitle")
        ll.addWidget(body_label)
        self.body = EditorWithSearch(language="json" if self._body_kind == "raw" else "text")
        self.body.editor.setPlainText(body_text)
        self.body.editor.setPlaceholderText("Request body — mark values here too")
        self.body.editor.textChanged.connect(self._update_positions)
        ll.addWidget(self.body, 1)
        self.url.textChanged.connect(self._update_positions)

        pay_row = QHBoxLayout()
        pay = QLabel("PAYLOADS (one per line)")
        pay.setObjectName("sectionTitle")
        pay_row.addWidget(pay)
        pay_row.addStretch(1)
        self.attack = QComboBox()
        self.attack.addItem("Sniper (one position at a time)", "sniper")
        self.attack.addItem("Battering ram (all positions)", "ram")
        pay_row.addWidget(self.attack)
        self.preset = QComboBox()
        self.preset.addItem("Insert preset…", "")
        for name in PAYLOAD_PRESETS:
            self.preset.addItem(name, name)
        self.preset.activated.connect(self._insert_preset)
        pay_row.addWidget(self.preset)
        self.urlencode = QCheckBox("URL-encode")
        pay_row.addWidget(self.urlencode)
        ll.addLayout(pay_row)
        self.payloads = QPlainTextEdit()
        self.payloads.setPlaceholderText("' OR '1'='1\n../../etc/passwd\n<script>alert(1)</script>")
        self.payloads.setMaximumHeight(120)
        ll.addWidget(self.payloads)
        self.start_btn = QPushButton("Start attack")
        self.start_btn.setObjectName("accentButton")
        self.start_btn.setIcon(theme.icon(G.PLAY, "#ffffff", 14))
        self.start_btn.clicked.connect(self._toggle)
        ll.addWidget(self.start_btn)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 0, 0, 0)
        rl.setSpacing(6)
        self.summary = QLabel("Mark a position and add payloads, then Start attack.")
        self.summary.setObjectName("h2")
        rl.addWidget(self.summary)
        self.results = QTableWidget(0, 7)
        self.results.setHorizontalHeaderLabels(["#", "Position", "Payload", "Status", "Length", "Time", "Notes"])
        self.results.verticalHeader().setVisible(False)
        self.results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.results.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(6, QHeaderView.Stretch)
        for col in (0, 1, 3, 4, 5):
            hh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.results.itemSelectionChanged.connect(self._show_result_detail)

        # results table on top, request/response of the selected row below (proxy-style)
        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(self.results)
        detail = QSplitter(Qt.Horizontal)
        req_wrap = QWidget()
        rq = QVBoxLayout(req_wrap)
        rq.setContentsMargins(0, 6, 6, 0)
        rq.setSpacing(4)
        req_lbl = QLabel("Request")
        req_lbl.setObjectName("sectionTitle")
        rq.addWidget(req_lbl)
        self.detail_req = EditorWithSearch(language="httpreq", read_only=True)
        self.detail_req.editor.set_wrap(True)
        self.detail_req.editor.setPlaceholderText("Select a result row to see its request and response.")
        rq.addWidget(self.detail_req, 1)
        req_wrap.setMinimumWidth(300)
        detail.addWidget(req_wrap)
        self.detail_resp = ResponsePanel()
        detail.addWidget(self.detail_resp)
        detail.setStretchFactor(0, 1)
        detail.setStretchFactor(1, 1)
        detail.setSizes([360, 400])
        vsplit.addWidget(detail)
        vsplit.setStretchFactor(0, 0)
        vsplit.setStretchFactor(1, 1)
        vsplit.setSizes([240, 360])
        rl.addWidget(vsplit, 1)
        hint = QLabel("Rows are flagged when status ≥ 500, is 401/403, the payload is reflected, or the length deviates from the baseline.")
        hint.setObjectName("faint")
        hint.setWordWrap(True)
        rl.addWidget(hint)
        split.addWidget(right)
        split.setSizes([440, 780])
        self.body_layout.addWidget(split, 1)
        self.add_button("Close", self.close)
        self._update_positions()

    # -- marker editing --
    def _focused_editor(self):
        if self.url.hasFocus():
            return self.url
        if self.headers.hasFocus():
            return self.headers
        return self.body.editor

    def _mark(self) -> None:
        widget = self._focused_editor()
        if isinstance(widget, QLineEdit):
            if not widget.selectedText():
                return
            start = widget.selectionStart()
            new, _, _ = security.wrap_selection(widget.text(), start, start + len(widget.selectedText()))
            widget.setText(new)
        else:
            cursor = widget.textCursor()
            if not cursor.hasSelection():
                return
            new, _, _ = security.wrap_selection(widget.toPlainText(), cursor.selectionStart(), cursor.selectionEnd())
            widget.setPlainText(new)
        self._update_positions()

    def _auto_mark(self) -> None:
        self.url.setText(security.auto_mark_query(self.url.text()))
        text = self.body.editor.toPlainText()
        if text.lstrip().startswith(("{", "[")):
            self.body.editor.setPlainText(security.auto_mark_json(text))
        elif text.strip():
            self.body.editor.setPlainText(security.auto_mark_query(text))
        self._update_positions()

    def _clear(self) -> None:
        self.url.setText(security.strip_markers(self.url.text()))
        self.headers.setPlainText(security.strip_markers(self.headers.toPlainText()))
        self.body.editor.setPlainText(security.strip_markers(self.body.editor.toPlainText()))
        self._update_positions()

    def _position_count(self) -> int:
        return sum(security.count_positions(t) for t in
                   (self.url.text(), self.headers.toPlainText(), self.body.editor.toPlainText()))

    def _update_positions(self) -> None:
        count = self._position_count()
        self.positions_label.setText(f"{count} position{'s' if count != 1 else ''}")

    def _insert_preset(self, _idx: int) -> None:
        name = self.preset.currentData()
        if not name:
            return
        current = self.payloads.toPlainText()
        block = "\n".join(PAYLOAD_PRESETS[name])
        self.payloads.setPlainText((current + "\n" + block).strip() if current.strip() else block)
        self.preset.setCurrentIndex(0)

    # -- building requests --
    def _assemble(self, payload: str, active: Optional[int]) -> Tuple[str, str, str]:
        """Return (url, headers_text, body_text) with markers replaced.

        active=None replaces every position (battering ram); otherwise only the
        global position `active` gets the payload and the rest keep their value.
        """
        offset = [0]

        def make(text: str) -> str:
            base = offset[0]

            def repl(idx: int, original: str) -> str:
                return payload if (active is None or base + idx == active) else original

            out = security.fill_markers(text, repl)
            offset[0] += security.count_positions(text)
            return out

        return make(self.url.text()), make(self.headers.toPlainText()), make(self.body.editor.toPlainText())

    def _build_model(self, url: str, headers_text: str, body_text: str) -> RequestModel:
        model = self.base.clone(fresh_id=True)
        model.method = self.method.currentText()
        model.url = url
        header_pairs = _parse_header_lines(headers_text)
        model.headers = [KeyValue(key=k, value=v) for k, v in header_pairs]
        if any(k.lower() == "authorization" for k, _ in header_pairs):
            model.auth = AuthConfig("noauth")  # respect an explicit Authorization header
        else:
            model.auth = self.base.auth
        if self._body_kind == "urlencoded":
            model.body = RequestBody(mode="urlencoded", urlencoded=[KeyValue(key=k, value=v) for k, v in _parse_kv_amp(body_text)])
        elif self._body_kind == "form-data":
            model.body = RequestBody(mode="form-data", form=[KeyValue(key=k, value=v) for k, v in _parse_kv_amp(body_text)])
        elif body_text.strip():
            model.body = RequestBody(mode="raw", raw=body_text, raw_language=self._raw_language or "json")
        else:
            model.body = RequestBody(mode="none")
        return model

    def _toggle(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.start_btn.setText("Stopping…")
            self.start_btn.setEnabled(False)
            return
        payloads = [p for p in self.payloads.toPlainText().splitlines() if p != ""]
        if not payloads:
            self.summary.setText("Add at least one payload.")
            return
        total = self._position_count()
        if total == 0:
            self.summary.setText("Mark at least one payload position with $$…$$ (select a value and click Mark $$).")
            return
        ctx = self.app.variable_context(self.collection_id)
        inherited = self.app.inherited_auths(self.collection_id, self.base.id) if self.collection_id else []
        sniper = self.attack.currentData() == "sniper"
        positions = range(total) if sniper else [None]

        items = []
        self._rows_meta = []
        for pos in positions:
            for payload in payloads:
                value = quote(payload, safe="") if self.urlencode.isChecked() else payload
                url, headers_text, body_text = self._assemble(value, pos)
                items.append((self._build_model(url, headers_text, body_text), ctx, inherited))
                self._rows_meta.append((f"#{pos + 1}" if pos is not None else "all", payload))

        self.results.setRowCount(0)
        self._baseline_len = None
        self._times = []
        self.worker = BatchWorker(items, self.app.settings)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.start_btn.setText("Stop")
        self.start_btn.setIcon(theme.icon(G.STOP, "#ffffff", 14))
        self.summary.setText(f"Running {len(items)} requests ({total} position{'s' if total != 1 else ''} × {len(payloads)} payloads)…")
        self.worker.start()

    def _baseline_ms(self) -> Optional[float]:
        prior = self._times[:-1]  # exclude the current sample
        return statistics.median(prior) if len(prior) >= 3 else None

    def _on_progress(self, index: int, result: RunResult) -> None:
        position, payload = self._rows_meta[index]
        resp = result.response
        length = resp.body_size if resp else 0
        elapsed = resp.elapsed_ms if resp else 0.0
        if resp is not None:
            self._times.append(elapsed)
            if self._baseline_len is None:
                self._baseline_len = length

        flags: List[str] = []
        if resp is None:
            flags.append(result.error.splitlines()[0][:40] if result.error else "error")
        else:
            if resp.status >= 500:
                flags.append("5xx")
            elif resp.status in (401, 403):
                flags.append(str(resp.status))
            flags.extend(security.response_signature_labels(resp.text))
            if payload and payload in resp.text:
                flags.append("reflected")
            if self._baseline_len is not None and abs(length - self._baseline_len) > 40:
                flags.append("lenΔ")
            baseline = self._baseline_ms()
            if (baseline is not None and elapsed > max(baseline * 2, baseline + 800)) or \
               (security.is_timing_payload(payload) and elapsed > 1500):
                flags.append(f"slow {elapsed / 1000:.1f}s")

        anomaly = bool(flags)
        row = self.results.rowCount()
        self.results.insertRow(row)
        status = f"{resp.status}" if resp else "—"
        cells = [str(index + 1), position, payload, status,
                 format_size(length) if resp else "—", format_ms(elapsed) if resp else "—",
                 ", ".join(flags)]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if col == 3 and resp is not None:
                item.setForeground(QColor(theme.status_color(resp.status)))
            if anomaly and col in (0, 6):
                item.setForeground(theme.qcolor("warning"))
            if anomaly and col == 0:
                item.setText("⚑ " + text)
            self.results.setItem(row, col, item)
        self.results.item(row, 0).setData(Qt.UserRole, result)  # keep the result for the detail view
        self.summary.setText(f"Running… {row + 1}/{len(self._rows_meta)}")

    def _show_result_detail(self) -> None:
        row = self.results.currentRow()
        cell = self.results.item(row, 0) if row >= 0 else None
        result = cell.data(Qt.UserRole) if cell is not None else None
        if result is None:
            return
        sent = result.request
        if sent is not None:
            sp = urlsplit(sent.url)
            path = (sp.path + (f"?{sp.query}" if sp.query else "")) or sent.url
            lines = [f"{sent.method} {path} HTTP/1.1"]
            lines.extend(f"{k}: {v}" for k, v in sent.headers)
            lines.append("")
            if sent.body_preview:
                lines.append(sent.body_preview)
            self.detail_req.editor.setPlainText("\n".join(lines))
        else:
            self.detail_req.editor.setPlainText("")
        self.detail_resp.show_result(result)

    def _on_done(self) -> None:
        self.worker = None
        self.start_btn.setText("Start attack")
        self.start_btn.setEnabled(True)
        self.start_btn.setIcon(theme.icon(G.PLAY, "#ffffff", 14))
        self.summary.setText(f"Completed · {self.results.rowCount()} requests")

    def closeEvent(self, event) -> None:
        if self.worker is not None:
            self.worker.stop()
        super().closeEvent(event)


PAYLOAD_PRESETS = {
    "SQL injection": ["'", "' OR '1'='1", "' OR 1=1--", "1; DROP TABLE users--", "' UNION SELECT NULL--", "admin'--"],
    "Time-based blind": ["' OR SLEEP(5)-- -", "'; WAITFOR DELAY '0:0:5'--", "' || pg_sleep(5)--",
                         "1)) OR SLEEP(5)#", "' AND BENCHMARK(5000000,MD5(1))--", "; ping -c 5 127.0.0.1"],
    "XSS": ["<script>alert(1)</script>", "\"><img src=x onerror=alert(1)>", "javascript:alert(1)", "'><svg/onload=alert(1)>"],
    "Path traversal": ["../../../../etc/passwd", "..\\..\\..\\windows\\win.ini", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "/etc/passwd%00"],
    "Command injection": [";id", "| id", "`id`", "$(id)", "& whoami"],
    "SSTI": ["{{7*7}}", "${7*7}", "#{7*7}", "<%= 7*7 %>"],
    "NoSQL": ['{"$gt":""}', '{"$ne":null}', "'||'1'=='1"],
    "Boundary numbers": ["0", "-1", "999999999", "2147483648", "9999999999999999"],
}


def _parse_header_lines(text: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for line in text.splitlines():
        if ":" in line:
            name, _, value = line.partition(":")
            if name.strip():
                out.append((name.strip(), value.strip()))
    return out


def _parse_kv_amp(text: str) -> List[Tuple[str, str]]:
    """Parse `key=value&key2=value2` (used for urlencoded / form bodies)."""
    out: List[Tuple[str, str]] = []
    for part in text.replace("\n", "&").split("&"):
        part = part.strip()
        if not part:
            continue
        key, _, value = part.partition("=")
        if key.strip():
            out.append((key.strip(), value))
    return out


def _body_as_text(body: RequestBody) -> Tuple[str, str, str]:
    """Return (editable_text, kind, raw_language) for the Intruder body editor."""
    if body.mode == "urlencoded":
        return "&".join(f"{kv.key}={kv.value}" for kv in body.urlencoded if kv.enabled and kv.key), "urlencoded", "text"
    if body.mode == "form-data":
        return "&".join(f"{kv.key}={kv.value}" for kv in body.form if kv.enabled and kv.key and kv.kind != "file"), "form-data", "text"
    if body.mode == "graphql":
        return body.graphql_query, "raw", "json"
    if body.mode == "raw":
        return body.raw, "raw", body.raw_language or "json"
    return "", "none", "text"


# ===========================================================================
# Multi-identity IDOR / BOLA matrix
# ===========================================================================
class IdentityDialog(ThemedDialog):
    """Add or edit a single identity (a header injected to impersonate a user)."""

    def __init__(self, parent: QWidget, data: Optional[dict] = None) -> None:
        super().__init__(parent, "Identity", 460, 260)
        data = data or {}
        form = QFormLayout()
        form.setContentsMargins(0, 8, 0, 0)
        form.setVerticalSpacing(10)
        self.name = QLineEdit(data.get("name", ""))
        self.name.setPlaceholderText("e.g. admin, user-A, low-priv")
        self.header = QLineEdit(data.get("header", "Authorization"))
        self.value = QLineEdit(data.get("value", ""))
        self.value.setPlaceholderText("e.g. Bearer eyJ…  or  {{adminToken}}")
        form.addRow("Name", self.name)
        form.addRow("Header", self.header)
        form.addRow("Value", self.value)
        self.body_layout.addLayout(form)
        self.body_layout.addStretch(1)
        self.add_button("Cancel", self.reject)
        self.add_button("Save", self.accept, primary=True)

    def data(self) -> dict:
        return {"name": self.name.text().strip() or "identity",
                "header": self.header.text().strip() or "Authorization",
                "value": self.value.text()}


class IdorMatrixDialog(ThemedDialog):
    def __init__(self, app: "MainWindow", collection_id: str) -> None:
        coll = app.find_collection(collection_id)
        super().__init__(app, f"IDOR / BOLA Matrix · {coll.name if coll else ''}", 1120, 740)
        self.setWindowModality(Qt.NonModal)
        self.app = app
        self.collection_id = collection_id
        self.worker: Optional[BatchWorker] = None
        self.identities: List[dict] = app.workspace.load_identities()
        self._requests: List[RequestModel] = requests_in(coll) if coll else []
        self._cells: List[Tuple[int, int]] = []  # (request_row, identity_col) per job

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 10, 0)
        ll.setSpacing(6)
        note = QLabel("Replays each request as every identity. Compare the status codes: a low-privilege or anonymous "
                      "identity returning 2xx on another user's object is a likely IDOR/BOLA.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        ll.addWidget(note)
        id_row = QHBoxLayout()
        id_label = QLabel("IDENTITIES")
        id_label.setObjectName("sectionTitle")
        id_row.addWidget(id_label)
        id_row.addStretch(1)
        add_btn = tool_button(G.ADD, "Add identity")
        add_btn.clicked.connect(self._add_identity)
        edit_btn = tool_button(G.EDIT, "Edit identity")
        edit_btn.clicked.connect(self._edit_identity)
        del_btn = tool_button(G.DELETE, "Remove identity", color="danger")
        del_btn.clicked.connect(self._del_identity)
        for b in (add_btn, edit_btn, del_btn):
            id_row.addWidget(b)
        ll.addLayout(id_row)
        self.id_list = QListWidget()
        ll.addWidget(self.id_list, 1)
        anon_note = QLabel("An <b>Anonymous</b> identity (no auth) is always included.")
        anon_note.setObjectName("faint")
        anon_note.setWordWrap(True)
        ll.addWidget(anon_note)
        self.run_btn = QPushButton("Run matrix")
        self.run_btn.setObjectName("accentButton")
        self.run_btn.setIcon(theme.icon(G.PLAY, "#ffffff", 14))
        self.run_btn.clicked.connect(self._toggle)
        ll.addWidget(self.run_btn)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 0, 0, 0)
        rl.setSpacing(6)
        self.summary = QLabel(f"{len(self._requests)} requests in this collection.")
        self.summary.setObjectName("h2")
        rl.addWidget(self.summary)
        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(True)
        rl.addWidget(self.table, 1)
        legend = QLabel("Green = 2xx · Orange = 3xx/4xx · Red = 5xx/error. Scan each column for unexpected 2xx.")
        legend.setObjectName("faint")
        rl.addWidget(legend)
        split.addWidget(right)
        split.setSizes([360, 760])
        self.body_layout.addWidget(split, 1)
        self.add_button("Close", self.close)
        self._refresh_identities()
        if not self._requests:
            self.summary.setText("This collection has no requests to test.")
            self.run_btn.setEnabled(False)

    # -- identities --
    def _refresh_identities(self) -> None:
        self.id_list.clear()
        for ident in self.identities:
            self.id_list.addItem(QListWidgetItem(f"{ident['name']}  ·  {ident['header']}: {_mask(ident['value'])}"))

    def _save_identities(self) -> None:
        self.app.workspace.save_identities(self.identities)

    def _add_identity(self) -> None:
        dlg = IdentityDialog(self)
        if dlg.exec_() == dlg.Accepted:
            self.identities.append(dlg.data())
            self._save_identities()
            self._refresh_identities()

    def _edit_identity(self) -> None:
        row = self.id_list.currentRow()
        if not (0 <= row < len(self.identities)):
            return
        dlg = IdentityDialog(self, self.identities[row])
        if dlg.exec_() == dlg.Accepted:
            self.identities[row] = dlg.data()
            self._save_identities()
            self._refresh_identities()

    def _del_identity(self) -> None:
        row = self.id_list.currentRow()
        if 0 <= row < len(self.identities):
            del self.identities[row]
            self._save_identities()
            self._refresh_identities()

    # -- run --
    def _columns(self) -> List[dict]:
        return [{"name": "Anonymous", "header": "", "value": ""}] + self.identities

    def _build_grid(self, cols: List[dict]) -> None:
        self.table.clear()
        self.table.setRowCount(len(self._requests))
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels([c["name"] for c in cols])
        self.table.setVerticalHeaderLabels([r.name for r in self._requests])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._cells = []
        for r in range(len(self._requests)):
            for c in range(len(cols)):
                self._cells.append((r, c))
                self.table.setItem(r, c, QTableWidgetItem("…"))

    def _toggle(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.run_btn.setText("Stopping…")
            self.run_btn.setEnabled(False)
            return
        cols = self._columns()
        ctx = self.app.variable_context(self.collection_id)
        self._build_grid(cols)
        items = []
        for r, req in enumerate(self._requests):
            for c, ident in enumerate(cols):
                model = req.clone(fresh_id=True)
                model.auth = AuthConfig("noauth")  # force the identity, ignore inherited auth
                if ident["header"] and ident["value"]:
                    model.headers = [kv for kv in model.headers if kv.key.lower() != ident["header"].lower()]
                    model.headers.append(KeyValue(key=ident["header"], value=ident["value"]))
                items.append((model, ctx, []))

        self.worker = BatchWorker(items, self.app.settings)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.run_btn.setText("Stop")
        self.run_btn.setIcon(theme.icon(G.STOP, "#ffffff", 14))
        self.summary.setText(f"Running {len(items)} requests ({len(self._requests)} × {len(cols)})…")
        self.worker.start()

    def _on_progress(self, index: int, result: RunResult) -> None:
        r, c = self._cells[index]
        resp = result.response
        text = f"{resp.status}" if resp else "ERR"
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        if resp is None:
            item.setForeground(theme.qcolor("danger"))
        else:
            item.setForeground(QColor(theme.status_color(resp.status)))
            item.setToolTip(f"{result.method} {result.url}\n{resp.status} {resp.reason} · {format_ms(resp.elapsed_ms)}")
        self.table.setItem(r, c, item)

    def _on_done(self) -> None:
        self.worker = None
        self.run_btn.setText("Run matrix")
        self.run_btn.setEnabled(True)
        self.run_btn.setIcon(theme.icon(G.PLAY, "#ffffff", 14))
        self.summary.setText("Completed. Compare each identity column against the owner's results.")

    def closeEvent(self, event) -> None:
        if self.worker is not None:
            self.worker.stop()
        super().closeEvent(event)


def _mask(value: str) -> str:
    value = (value or "").strip()
    if len(value) <= 10:
        return value or "(none)"
    return f"{value[:6]}…{value[-4:]}"
