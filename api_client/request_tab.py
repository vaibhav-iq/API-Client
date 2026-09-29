"""A single request editor tab: URL bar, request sub-tabs and the response panel."""
from typing import TYPE_CHECKING, Dict, List, Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .engine import PATH_VAR_RE, RunResult
from .importers import parse_curl
from .kv_table import KeyValueTable
from .models import KeyValue, RequestModel
from .request_panels import AuthPanel, BodyPanel, DocsPanel, ScriptsPanel, TestsPanel
from .response_panel import ResponsePanel
from .theme import G
from .widgets import MethodCombo, VariableLineEdit, tool_button, vdivider
from .workers import RequestWorker

if TYPE_CHECKING:  # pragma: no cover
    from .main_window import MainWindow


def split_query(url: str) -> List[KeyValue]:
    if "?" not in url:
        return []
    query = url.split("?", 1)[1].split("#", 1)[0]
    items = []
    for part in query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        items.append(KeyValue(key=key, value=value))
    return items


def join_query(url: str, params: List[KeyValue]) -> str:
    base, _, fragment = url.partition("#")
    base = base.split("?", 1)[0]
    enabled = [p for p in params if p.enabled and p.key]
    if enabled:
        base += "?" + "&".join(f"{p.key}={p.value}" if p.value != "" else p.key for p in enabled)
    return f"{base}#{fragment}" if fragment else base


class RequestTab(QWidget):
    kind = "request"
    dirtyChanged = pyqtSignal(bool)
    titleChanged = pyqtSignal()

    def __init__(self, app: "MainWindow", model: RequestModel, collection_id: Optional[str] = None) -> None:
        super().__init__()
        self.app = app
        self.model_id = model.id
        self.collection_id = collection_id
        self._loading = False
        self._syncing = False
        self._dirty = False
        self._worker: Optional[RequestWorker] = None
        self._snapshot: Dict = {}
        self._ctx = app.variable_context(collection_id)

        self._dirty_timer = QTimer(self)
        self._dirty_timer.setSingleShot(True)
        self._dirty_timer.setInterval(150)
        self._dirty_timer.timeout.connect(self._check_dirty)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header: breadcrumb + name + actions --------------------------------
        header = QFrame()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 10, 12, 2)
        hl.setSpacing(4)
        self.breadcrumb = QLabel()
        self.breadcrumb.setObjectName("breadcrumb")
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("titleEdit")
        self.name_edit.setMinimumWidth(240)
        self.name_edit.textChanged.connect(self._on_name_changed)
        hl.addWidget(self.breadcrumb)
        hl.addWidget(self.name_edit, 1)
        hl.addStretch(0)
        self.save_btn = QToolButton()
        self.save_btn.setObjectName("saveButton")
        self.save_btn.setText("Save")
        self.save_btn.setIcon(theme.icon(G.SAVE, "muted", 14))
        self.save_btn.setProperty("glyph", G.SAVE)
        self.save_btn.setProperty("glyphSize", 14)
        self.save_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.save_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setToolTip("Save (Ctrl+S)")
        save_menu = QMenu(self.save_btn)
        save_menu.addAction("Save As…", lambda: self.app.save_tab(self, save_as=True))
        self.save_btn.setMenu(save_menu)
        self.save_btn.clicked.connect(lambda: self.app.save_tab(self))
        self.code_btn = tool_button(G.CODE, "Code snippet (Ctrl+Shift+C)")
        self.code_btn.clicked.connect(lambda: self.app.show_code_snippet(self))
        hl.addWidget(self.save_btn)
        hl.addWidget(self.code_btn)
        root.addWidget(header)

        # URL bar ---------------------------------------------------------------
        url_row = QHBoxLayout()
        url_row.setContentsMargins(16, 6, 16, 10)
        url_row.setSpacing(8)
        self.url_bar = QFrame()
        self.url_bar.setObjectName("urlBar")
        ub = QHBoxLayout(self.url_bar)
        ub.setContentsMargins(0, 1, 6, 1)
        ub.setSpacing(0)
        self.method_combo = MethodCombo()
        self.method_combo.currentTextChanged.connect(self._on_method_changed)
        self.url_edit = VariableLineEdit(placeholder="Enter URL or paste cURL")
        self.url_edit.textEdited.connect(self._on_url_edited)
        self.url_edit.returnPressed.connect(self.send)
        self.url_edit.curlPasted.connect(self._on_curl_pasted)
        self.url_edit.focusChanged.connect(self._on_url_focus)
        self.url_edit.set_resolver(self.resolve_variable)
        self.url_edit.names_provider = lambda: self._ctx.all_names()
        ub.addWidget(self.method_combo)
        ub.addWidget(vdivider(22))
        ub.addSpacing(8)
        ub.addWidget(self.url_edit, 1)
        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("primaryButton")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setMinimumWidth(100)
        self.send_btn.setMinimumHeight(36)
        self.send_btn.setToolTip("Send (Ctrl+Enter)")
        self.send_btn.clicked.connect(self._on_send_clicked)
        url_row.addWidget(self.url_bar, 1)
        url_row.addWidget(self.send_btn)
        root.addLayout(url_row)

        # Request / response split ------------------------------------------------
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        request_area = QWidget()
        ra = QVBoxLayout(request_area)
        ra.setContentsMargins(16, 0, 16, 8)
        ra.setSpacing(0)
        self.req_tabs = QTabWidget()
        self.req_tabs.setDocumentMode(True)

        params_page = QWidget()
        pl = QVBoxLayout(params_page)
        pl.setContentsMargins(0, 10, 0, 0)
        pl.setSpacing(12)
        self.params_table = KeyValueTable(title="Query Params")
        self.params_table.changed.connect(self._on_params_changed)
        self.path_table = KeyValueTable(title="Path Variables", allow_bulk=False)
        self.path_table.changed.connect(self._on_changed)
        self.path_table.hide()
        pl.addWidget(self.params_table, 3)
        pl.addWidget(self.path_table, 2)
        self.req_tabs.addTab(params_page, "Params")

        self.auth_panel = AuthPanel(token_fetcher=lambda panel, cfg: self.app.fetch_oauth_token(panel, cfg, self.collection_id))
        self.auth_panel.changed.connect(self._on_changed)
        self.req_tabs.addTab(self._padded(self.auth_panel), "Authorization")

        headers_page = QWidget()
        hp = QVBoxLayout(headers_page)
        hp.setContentsMargins(0, 10, 0, 0)
        self.headers_table = KeyValueTable(title="Headers")
        self.headers_table.changed.connect(self._on_changed)
        hp.addWidget(self.headers_table)
        auto_note = QLabel("User-Agent, Accept and Content-Type (from the body) are added automatically when not set.")
        auto_note.setObjectName("faint")
        hp.addWidget(auto_note)
        self.req_tabs.addTab(headers_page, "Headers")

        self.body_panel = BodyPanel()
        self.body_panel.changed.connect(self._on_changed)
        self.req_tabs.addTab(self.body_panel, "Body")

        self.scripts_panel = ScriptsPanel()
        self.scripts_panel.changed.connect(self._on_changed)
        self.req_tabs.addTab(self.scripts_panel, "Scripts")

        self.tests_panel = TestsPanel()
        self.tests_panel.changed.connect(self._on_changed)
        self.req_tabs.addTab(self.tests_panel, "Tests")

        self.docs_panel = DocsPanel()
        self.docs_panel.changed.connect(self._on_changed)
        self.req_tabs.addTab(self.docs_panel, "Docs")

        ra.addWidget(self.req_tabs)
        self.splitter.addWidget(request_area)

        self.response = ResponsePanel()
        self.response.cancelRequested.connect(self.cancel)
        self.splitter.addWidget(self.response)
        self.splitter.setStretchFactor(0, 5)
        self.splitter.setStretchFactor(1, 6)
        root.addWidget(self.splitter, 1)

        self.set_layout_mode(app.settings.layout_mode)
        self.load_model(model)
        self._snapshot = self.collect_model().to_dict()
        self.refresh_context()

    @staticmethod
    def _padded(widget: QWidget) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(widget)
        return wrap

    # -- model <-> UI --------------------------------------------------------------
    def load_model(self, model: RequestModel) -> None:
        self._loading = True
        try:
            self.name_edit.setText(model.name)
            self.method_combo.set_method(model.method)
            self.url_edit.setText(model.url)
            self.params_table.set_items(model.params or split_query(model.url))
            self._sync_path_variables(model.path_variables)
            self.headers_table.set_items(model.headers)
            self.body_panel.set_body(model.body)
            self.auth_panel.set_config(model.auth)
            self.scripts_panel.set_scripts(model.pre_script, model.post_script)
            self.tests_panel.set_rules(model.tests, model.extractors)
            self.docs_panel.set_text(model.description)
        finally:
            self._loading = False
        self._update_tab_labels()

    def collect_model(self) -> RequestModel:
        pre, post = self.scripts_panel.scripts()
        return RequestModel(
            id=self.model_id,
            name=self.name_edit.text().strip() or "Untitled Request",
            method=self.method_combo.currentText(),
            url=self.url_edit.text().strip(),
            params=self.params_table.items(),
            path_variables=self.path_table.items() if self._path_names() else [],
            headers=self.headers_table.items(),
            body=self.body_panel.get_body(),
            auth=self.auth_panel.get_config(),
            description=self.docs_panel.text(),
            tests=self.tests_panel.get_tests(),
            extractors=self.tests_panel.get_extractors(),
            pre_script=pre,
            post_script=post,
        )

    def title(self) -> str:
        return self.name_edit.text().strip() or "Untitled Request"

    def method(self) -> str:
        return self.method_combo.currentText()

    def is_dirty(self) -> bool:
        return self._dirty

    def mark_saved(self, collection_id: Optional[str]) -> None:
        self.collection_id = collection_id
        self._snapshot = self.collect_model().to_dict()
        self._set_dirty(False)
        self.refresh_context()

    def mark_dirty(self) -> None:
        self._snapshot = {}
        self._set_dirty(True)

    # -- variables / context ------------------------------------------------------
    def resolve_variable(self, name: str):
        return self._ctx.lookup(name)

    def refresh_context(self) -> None:
        self._ctx = self.app.variable_context(self.collection_id)
        self.breadcrumb.setText(self.app.breadcrumb(self.collection_id, self.model_id))
        self.breadcrumb.setVisible(bool(self.breadcrumb.text()))
        self.auth_panel.set_inherited_hint(self.app.inherited_auth_hint(self.collection_id, self.model_id))
        self.url_edit.rehighlight()
        for table in (self.params_table, self.path_table, self.headers_table):
            table.set_resolver(self.resolve_variable)
        self.body_panel.set_resolver(self.resolve_variable)

    # -- change tracking ------------------------------------------------------------
    def _on_changed(self, *_args) -> None:
        if self._loading:
            return
        self._dirty_timer.start()

    def _check_dirty(self) -> None:
        self._update_tab_labels()
        self._set_dirty(self.collect_model().to_dict() != self._snapshot)

    def _set_dirty(self, dirty: bool) -> None:
        if dirty != self._dirty:
            self._dirty = dirty
            self.dirtyChanged.emit(dirty)

    def _on_name_changed(self, _text: str) -> None:
        self.titleChanged.emit()
        self._on_changed()

    def _on_method_changed(self, _method: str) -> None:
        self.titleChanged.emit()
        self._on_changed()

    def _update_tab_labels(self) -> None:
        def label(base: str, count: int) -> str:
            return f"{base} ({count})" if count else base

        self.req_tabs.setTabText(0, label("Params", self.params_table.enabled_count() + len(self._path_names())))
        auth_type = self.auth_panel.get_config().type
        self.req_tabs.setTabText(1, "Authorization" + (" •" if auth_type not in ("noauth", "inherit") else ""))
        self.req_tabs.setTabText(2, label("Headers", self.headers_table.enabled_count()))
        self.req_tabs.setTabText(3, "Body" + (" •" if self.body_panel.mode() != "none" else ""))
        pre, post = self.scripts_panel.scripts()
        self.req_tabs.setTabText(4, "Scripts" + (" •" if pre.strip() or post.strip() else ""))
        self.req_tabs.setTabText(5, label("Tests", self.tests_panel.count()))

    # -- URL <-> params sync -----------------------------------------------------
    def _path_names(self) -> List[str]:
        return PATH_VAR_RE.findall(self.url_edit.text().split("?", 1)[0])

    def _sync_path_variables(self, existing: Optional[List[KeyValue]] = None) -> None:
        names = self._path_names()
        current = {kv.key: kv for kv in (existing if existing is not None else self.path_table.items())}
        items = [current.get(name) or KeyValue(key=name) for name in dict.fromkeys(names)]
        self.path_table.set_items(items)
        self.path_table.setVisible(bool(items))

    def _on_url_edited(self, text: str) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            parsed = split_query(text)
            old = self.params_table.items()
            descriptions = {kv.key: kv.description for kv in old}
            for kv in parsed:
                kv.description = descriptions.get(kv.key, "")
            disabled = [kv for kv in old if not kv.enabled]
            self.params_table.set_items(parsed + disabled)
            if [kv.key for kv in self.path_table.items()] != list(dict.fromkeys(self._path_names())):
                self._sync_path_variables()
        finally:
            self._syncing = False
        self._on_changed()

    def _on_params_changed(self) -> None:
        if not self._syncing:
            self._syncing = True
            try:
                self.url_edit.setText(join_query(self.url_edit.text(), self.params_table.items()))
            finally:
                self._syncing = False
        self._on_changed()

    def _on_curl_pasted(self, text: str) -> None:
        try:
            model = parse_curl(text)
        except ValueError as exc:
            self.app.toast(str(exc), error=True)
            self.url_edit.textCursor().insertText(text.replace("\n", " "))
            return
        model.id = self.model_id
        model.name = self.title() if self.name_edit.text().strip() not in ("", "Untitled Request") else model.name
        self.load_model(model)
        self._on_changed()
        self.app.toast("Imported request from cURL")

    def _on_url_focus(self, focused: bool) -> None:
        self.url_bar.setProperty("focused", "true" if focused else "false")
        self.url_bar.style().unpolish(self.url_bar)
        self.url_bar.style().polish(self.url_bar)

    # -- sending ---------------------------------------------------------------------
    def _on_send_clicked(self) -> None:
        if self._worker is not None:
            self.cancel()
        else:
            self.send()

    def send(self) -> None:
        if self._worker is not None:
            return
        model = self.collect_model()
        worker = RequestWorker(
            model,
            self.app.variable_context(self.collection_id),
            self.app.settings,
            self.app.inherited_auths(self.collection_id, self.model_id),
            info={"requestName": model.name, "requestId": model.id},
        )
        worker.finished_result.connect(self._on_finished)
        self._worker = worker
        self.response.show_loading()
        self.send_btn.setText("Cancel")
        worker.start()

    def cancel(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        worker.finished_result.disconnect(self._on_finished)
        worker.cancel()
        self.app.retire_worker(worker)
        self.response.show_cancelled()
        self.send_btn.setText("Send")

    def is_busy(self) -> bool:
        return self._worker is not None

    def _on_finished(self, result: RunResult) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            self.app.retire_worker(worker)
        self.send_btn.setText("Send")
        self.response.show_result(result)
        self.app.on_request_finished(self, result)

    # -- misc ---------------------------------------------------------------------------
    def set_layout_mode(self, mode: str) -> None:
        self.splitter.setOrientation(Qt.Horizontal if mode == "horizontal" else Qt.Vertical)

    def focus_url(self) -> None:
        self.url_edit.setFocus()
        self.url_edit.selectAll()
