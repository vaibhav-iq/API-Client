"""Request editor sub-panels: Authorization, Body, Tests, Scripts and Docs."""
from typing import Callable, Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .code_editor import EditorWithSearch
from .engine import EXTRACT_SOURCES, TEST_OPS, TEST_SOURCES, VARIABLE_SCOPES
from .kv_table import KeyValueTable
from .models import AUTH_TYPES, BODY_MODES, RAW_LANGUAGES, AuthConfig, ExtractRule, RequestBody, TestRule
from .scripting import SCRIPT_SNIPPETS
from .theme import G

AUTH_DESCRIPTIONS = {
    "inherit": "The authorization header will be automatically generated from the parent folder or collection when you send the request.",
    "noauth": "This request does not use any authorization.",
    "apikey": "The key/value pair is added to the request headers or query parameters.",
    "bearer": "The token is sent in the Authorization header as 'Bearer <token>'.",
    "basic": "The username and password are Base64-encoded into the Authorization header.",
    "digest": "Username and password are used for the HTTP Digest challenge/response handshake.",
    "jwt": "A JWT is signed locally with HMAC (HS256/384/512) and attached to the request.",
    "oauth2": "Send a stored access token, or request a new one with the client credentials or password grant.",
}


def _field_line(placeholder: str = "", password: bool = False) -> QLineEdit:
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    if password:
        edit.setEchoMode(QLineEdit.Password)
        toggle = edit.addAction(theme.icon(G.EYE, "faint", 14), QLineEdit.TrailingPosition)
        toggle.setToolTip("Show / hide")
        toggle.triggered.connect(
            lambda: edit.setEchoMode(QLineEdit.Normal if edit.echoMode() == QLineEdit.Password else QLineEdit.Password)
        )
    return edit


class AuthPanel(QWidget):
    changed = pyqtSignal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        allow_inherit: bool = True,
        token_fetcher: Optional[Callable[["AuthPanel", AuthConfig], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.token_fetcher = token_fetcher
        self._fields: Dict[str, Dict[str, QWidget]] = {}
        self._types = [(k, v) for k, v in AUTH_TYPES if allow_inherit or k != "inherit"]

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(20)

        left = QVBoxLayout()
        left.setSpacing(8)
        type_label = QLabel("Auth Type")
        type_label.setObjectName("sectionTitle")
        self.type_combo = QComboBox()
        for key, label in self._types:
            self.type_combo.addItem(label, key)
        self.type_combo.currentIndexChanged.connect(self._on_type)
        self.description = QLabel()
        self.description.setObjectName("muted")
        self.description.setWordWrap(True)
        left.addWidget(type_label)
        left.addWidget(self.type_combo)
        left.addWidget(self.description)
        left.addStretch(1)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(270)
        root.addWidget(left_widget)

        self.stack = QStackedWidget()
        for key, _label in self._types:
            self.stack.addWidget(self._build_page(key))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.stack)
        root.addWidget(scroll, 1)
        self._on_type()

    # -- pages ---------------------------------------------------------------------
    def _form_page(self) -> Tuple[QWidget, QFormLayout]:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        return page, form

    def _register(self, auth_type: str, key: str, widget: QWidget) -> QWidget:
        self._fields.setdefault(auth_type, {})[key] = widget
        if isinstance(widget, QLineEdit):
            widget.textChanged.connect(self.changed.emit)
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(self.changed.emit)
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(self.changed.emit)
        elif isinstance(widget, EditorWithSearch):
            widget.editor.textChanged.connect(self.changed.emit)
        return widget

    def _combo(self, options: List[tuple]) -> QComboBox:
        combo = QComboBox()
        for value, label in options:
            combo.addItem(label, value)
        return combo

    def _build_page(self, auth_type: str) -> QWidget:
        if auth_type in ("inherit", "noauth"):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            label = QLabel(
                "This request inherits authorization from its parent folder or collection."
                if auth_type == "inherit"
                else "This request does not use any authorization."
            )
            label.setObjectName("muted")
            label.setWordWrap(True)
            layout.addWidget(label)
            if auth_type == "inherit":
                self.inherit_hint = QLabel("")
                self.inherit_hint.setWordWrap(True)
                layout.addWidget(self.inherit_hint)
            layout.addStretch(1)
            return page

        page, form = self._form_page()
        reg = lambda key, widget: self._register(auth_type, key, widget)  # noqa: E731
        if auth_type == "apikey":
            form.addRow("Key", reg("key", _field_line("X-API-Key")))
            form.addRow("Value", reg("value", _field_line("Value", password=True)))
            form.addRow("Add to", reg("in", self._combo([("header", "Header"), ("query", "Query Params")])))
        elif auth_type == "bearer":
            form.addRow("Token", reg("token", _field_line("Token", password=True)))
        elif auth_type in ("basic", "digest"):
            form.addRow("Username", reg("username", _field_line("Username")))
            form.addRow("Password", reg("password", _field_line("Password", password=True)))
        elif auth_type == "jwt":
            form.addRow("Add JWT token to", reg("add_to", self._combo([("header", "Request Header"), ("query", "Query Param")])))
            form.addRow("Algorithm", reg("algorithm", self._combo([("HS256", "HS256"), ("HS384", "HS384"), ("HS512", "HS512")])))
            form.addRow("Secret", reg("secret", _field_line("Secret", password=True)))
            form.addRow("", reg("secret_b64", QCheckBox("Secret Base64 encoded")))
            payload = EditorWithSearch(language="json", line_numbers=False)
            payload.setMinimumHeight(110)
            payload.editor.setPlaceholderText('{"sub": "user-id", "iat": 0}')
            form.addRow("Payload", reg("payload", payload))
            form.addRow("Header prefix", reg("header_prefix", _field_line("Bearer")))
            form.addRow("Query param name", reg("query_key", _field_line("token")))
        elif auth_type == "oauth2":
            current = QLabel("Current Token")
            current.setObjectName("h2")
            form.addRow(current)
            form.addRow("Access Token", reg("access_token", _field_line("Paste a token or get a new one below", password=True)))
            form.addRow("Header Prefix", reg("header_prefix", _field_line("Bearer")))
            form.addRow("Add token to", reg("add_to", self._combo([("header", "Request Headers"), ("query", "Request URL")])))
            configure = QLabel("Configure New Token")
            configure.setObjectName("h2")
            form.addRow(configure)
            form.addRow("Grant Type", reg("grant_type", self._combo([("client_credentials", "Client Credentials"), ("password", "Password Credentials")])))
            form.addRow("Access Token URL", reg("token_url", _field_line("https://auth.example.com/oauth/token")))
            form.addRow("Client ID", reg("client_id", _field_line("Client ID")))
            form.addRow("Client Secret", reg("client_secret", _field_line("Client Secret", password=True)))
            form.addRow("Username", reg("username", _field_line("Username (password grant)")))
            form.addRow("Password", reg("password", _field_line("Password (password grant)", password=True)))
            form.addRow("Scope", reg("scope", _field_line("e.g. read:org")))
            form.addRow("Audience", reg("audience", _field_line("Optional")))
            form.addRow("Client Authentication", reg("client_auth", self._combo([("header", "Send as Basic Auth header"), ("body", "Send client credentials in body")])))
            self.fetch_btn = QPushButton("Get New Access Token")
            self.fetch_btn.setObjectName("accentButton")
            self.fetch_btn.setCursor(Qt.PointingHandCursor)
            self.fetch_btn.clicked.connect(self._fetch)
            form.addRow("", self.fetch_btn)
        return page

    def _fetch(self) -> None:
        if self.token_fetcher is not None:
            self.token_fetcher(self, self.get_config())

    def set_access_token(self, token: str) -> None:
        widget = self._fields.get("oauth2", {}).get("access_token")
        if isinstance(widget, QLineEdit):
            widget.setText(token)

    def set_fetching(self, busy: bool) -> None:
        if hasattr(self, "fetch_btn"):
            self.fetch_btn.setEnabled(not busy)
            self.fetch_btn.setText("Requesting token…" if busy else "Get New Access Token")

    def _on_type(self) -> None:
        key = self.type_combo.currentData()
        self.stack.setCurrentIndex(self.type_combo.currentIndex())
        self.description.setText(AUTH_DESCRIPTIONS.get(key, ""))
        self.changed.emit()

    def set_inherited_hint(self, text: str) -> None:
        if hasattr(self, "inherit_hint"):
            self.inherit_hint.setText(text)

    # -- data ------------------------------------------------------------------------
    def get_config(self) -> AuthConfig:
        auth_type = self.type_combo.currentData() or "noauth"
        data: Dict[str, str] = {}
        for key, widget in self._fields.get(auth_type, {}).items():
            if isinstance(widget, QLineEdit):
                data[key] = widget.text()
            elif isinstance(widget, QComboBox):
                data[key] = widget.currentData()
            elif isinstance(widget, QCheckBox):
                data[key] = "true" if widget.isChecked() else "false"
            elif isinstance(widget, EditorWithSearch):
                data[key] = widget.editor.toPlainText()
        return AuthConfig(type=auth_type, data=data)

    def set_config(self, config: AuthConfig) -> None:
        self.blockSignals(True)
        idx = self.type_combo.findData(config.type)
        self.type_combo.setCurrentIndex(max(0, idx))
        defaults = {"key": "X-API-Key", "header_prefix": "Bearer", "query_key": "token", "payload": '{\n  "sub": "user-id"\n}'}
        for auth_type, widgets in self._fields.items():
            for key, widget in widgets.items():
                value = config.data.get(key, "") if auth_type == config.type else ""
                if not value and isinstance(widget, (QLineEdit, EditorWithSearch)):
                    value = defaults.get(key, "")
                if isinstance(widget, QLineEdit):
                    widget.setText(value)
                elif isinstance(widget, QComboBox):
                    widget.setCurrentIndex(max(0, widget.findData(value)))
                elif isinstance(widget, QCheckBox):
                    widget.setChecked(value == "true")
                elif isinstance(widget, EditorWithSearch):
                    widget.editor.setPlainText(value)
        self.blockSignals(False)
        self._on_type()


class BodyPanel(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(14)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: Dict[str, QRadioButton] = {}
        for idx, (key, label) in enumerate(BODY_MODES):
            btn = QRadioButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            self.mode_group.addButton(btn, idx)
            self.mode_buttons[key] = btn
            bar.addWidget(btn)
        self.language_combo = QComboBox()
        self.language_combo.setObjectName("flatCombo")
        for key, label in RAW_LANGUAGES:
            self.language_combo.addItem(label, key)
        self.language_combo.currentIndexChanged.connect(self._on_language)
        bar.addWidget(self.language_combo)
        bar.addStretch(1)
        self.beautify_btn = QPushButton("Beautify")
        self.beautify_btn.setObjectName("linkButton")
        self.beautify_btn.setCursor(Qt.PointingHandCursor)
        self.beautify_btn.clicked.connect(self.beautify)
        bar.addWidget(self.beautify_btn)
        root.addLayout(bar)

        self.stack = QStackedWidget()
        none_page = QLabel("This request does not have a body")
        none_page.setAlignment(Qt.AlignCenter)
        none_page.setObjectName("muted")
        self.stack.addWidget(none_page)

        self.form_table = KeyValueTable(type_options=[("text", "Text"), ("file", "File")], allow_bulk=True)
        self.form_table.changed.connect(self.changed.emit)
        self.stack.addWidget(self.form_table)

        self.urlencoded_table = KeyValueTable(allow_bulk=True)
        self.urlencoded_table.changed.connect(self.changed.emit)
        self.stack.addWidget(self.urlencoded_table)

        self.raw_editor = EditorWithSearch(language="json")
        self.raw_editor.editor.textChanged.connect(self.changed.emit)
        self.stack.addWidget(self.raw_editor)

        binary_page = QWidget()
        binary_layout = QHBoxLayout(binary_page)
        binary_layout.setContentsMargins(0, 0, 0, 0)
        self.binary_path = QLineEdit()
        self.binary_path.setPlaceholderText("Select a file to send as the request body")
        self.binary_path.textChanged.connect(self.changed.emit)
        browse = QPushButton("Select File")
        browse.clicked.connect(self._browse_binary)
        binary_layout.addWidget(self.binary_path, 1)
        binary_layout.addWidget(browse)
        binary_wrap = QWidget()
        wrap_layout = QVBoxLayout(binary_wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.addWidget(binary_page)
        wrap_layout.addStretch(1)
        self.stack.addWidget(binary_wrap)

        graphql = QSplitter(Qt.Horizontal)
        query_box = QWidget()
        q_layout = QVBoxLayout(query_box)
        q_layout.setContentsMargins(0, 0, 4, 0)
        q_label = QLabel("QUERY")
        q_label.setObjectName("sectionTitle")
        self.graphql_query = EditorWithSearch(language="graphql")
        self.graphql_query.editor.textChanged.connect(self.changed.emit)
        q_layout.addWidget(q_label)
        q_layout.addWidget(self.graphql_query, 1)
        vars_box = QWidget()
        v_layout = QVBoxLayout(vars_box)
        v_layout.setContentsMargins(4, 0, 0, 0)
        v_label = QLabel("GRAPHQL VARIABLES")
        v_label.setObjectName("sectionTitle")
        self.graphql_vars = EditorWithSearch(language="json")
        self.graphql_vars.editor.textChanged.connect(self.changed.emit)
        v_layout.addWidget(v_label)
        v_layout.addWidget(self.graphql_vars, 1)
        graphql.addWidget(query_box)
        graphql.addWidget(vars_box)
        graphql.setSizes([600, 400])
        self.stack.addWidget(graphql)

        root.addWidget(self.stack, 1)
        self.mode_group.buttonClicked.connect(self._on_mode)
        self.mode_buttons["none"].setChecked(True)
        self._on_mode()

    def _browse_binary(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select file")
        if path:
            self.binary_path.setText(path)

    def mode(self) -> str:
        return BODY_MODES[max(0, self.mode_group.checkedId())][0]

    def _on_mode(self, *_args) -> None:
        mode = self.mode()
        self.stack.setCurrentIndex(max(0, self.mode_group.checkedId()))
        self.language_combo.setVisible(mode == "raw")
        self.beautify_btn.setVisible(mode in ("raw", "graphql"))
        self.changed.emit()

    def _on_language(self) -> None:
        lang = self.language_combo.currentData()
        self.raw_editor.editor.set_language(lang)
        self.changed.emit()

    def beautify(self) -> None:
        if self.mode() == "graphql":
            ok, error = self.graphql_vars.editor.format_json()
        elif self.language_combo.currentData() == "json":
            ok, error = self.raw_editor.editor.format_json()
        else:
            return
        if not ok:
            QMessageBox.warning(self, "Beautify", f"The body is not valid JSON:\n{error}")

    def get_body(self) -> RequestBody:
        return RequestBody(
            mode=self.mode(),
            raw=self.raw_editor.editor.toPlainText(),
            raw_language=self.language_combo.currentData() or "json",
            form=self.form_table.items(),
            urlencoded=self.urlencoded_table.items(),
            binary_path=self.binary_path.text(),
            graphql_query=self.graphql_query.editor.toPlainText(),
            graphql_variables=self.graphql_vars.editor.toPlainText(),
        )

    def set_body(self, body: RequestBody) -> None:
        self.blockSignals(True)
        self.mode_buttons.get(body.mode, self.mode_buttons["none"]).setChecked(True)
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(body.raw_language)))
        self.raw_editor.editor.set_language(body.raw_language)
        self.raw_editor.editor.setPlainText(body.raw)
        self.form_table.set_items(body.form)
        self.urlencoded_table.set_items(body.urlencoded)
        self.binary_path.setText(body.binary_path)
        self.graphql_query.editor.setPlainText(body.graphql_query)
        self.graphql_vars.editor.setPlainText(body.graphql_variables)
        self.blockSignals(False)
        self._on_mode()

    def set_resolver(self, resolver) -> None:
        self.form_table.set_resolver(resolver)
        self.urlencoded_table.set_resolver(resolver)


class TestsPanel(QWidget):
    """No-code assertions plus 'set variable from response' rules."""

    changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        from .widgets import RuleTable

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(6)

        title = QLabel("ASSERTIONS")
        title.setObjectName("sectionTitle")
        hint = QLabel("Checks run after every response. Use JSON paths like $.data[0].id")
        hint.setObjectName("muted")
        root.addWidget(title)
        root.addWidget(hint)
        self.tests = RuleTable(
            [
                ("check", "", None),
                ("combo", "Source", TEST_SOURCES),
                ("text", "Property", "JSON path or header name"),
                ("combo", "Operator", TEST_OPS),
                ("text", "Expected", "Expected value"),
            ]
        )
        self.tests.changed.connect(self.changed.emit)
        root.addWidget(self.tests, 3)

        title2 = QLabel("SET VARIABLES FROM RESPONSE")
        title2.setObjectName("sectionTitle")
        hint2 = QLabel("Chain requests: store values (e.g. tokens, ids) in variables for later requests.")
        hint2.setObjectName("muted")
        root.addSpacing(6)
        root.addWidget(title2)
        root.addWidget(hint2)
        self.extractors = RuleTable(
            [
                ("check", "", None),
                ("text", "Variable", "Variable name"),
                ("combo", "Source", EXTRACT_SOURCES),
                ("text", "Path / Expression", "$.token, header name or regex"),
                ("combo", "Scope", VARIABLE_SCOPES),
            ]
        )
        self.extractors.changed.connect(self.changed.emit)
        root.addWidget(self.extractors, 2)

    def set_rules(self, tests: List[TestRule], extractors: List[ExtractRule]) -> None:
        self.tests.set_rows([[t.enabled, t.source, t.prop, t.op, t.expected] for t in tests])
        self.extractors.set_rows([[e.enabled, e.variable, e.source, e.path, e.scope] for e in extractors])

    def get_tests(self) -> List[TestRule]:
        rules = []
        for enabled, source, prop, op, expected in self.tests.rows():
            rules.append(TestRule(enabled=enabled, source=source, prop=prop, op=op, expected=expected))
        return rules

    def get_extractors(self) -> List[ExtractRule]:
        return [
            ExtractRule(enabled=enabled, variable=variable, source=source, path=path, scope=scope)
            for enabled, variable, source, path, scope in self.extractors.rows()
        ]

    def count(self) -> int:
        return len([t for t in self.get_tests() if t.enabled]) + len([e for e in self.get_extractors() if e.enabled])


class ScriptsPanel(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(10)

        self.nav = QListWidget()
        self.nav.setObjectName("settingsNav")
        self.nav.setFixedWidth(150)
        for label in ("Pre-request", "Post-response"):
            self.nav.addItem(QListWidgetItem(label))
        root.addWidget(self.nav)

        center = QVBoxLayout()
        center.setSpacing(6)
        note = QLabel("Python scripts with a Postman-like <b>pm</b> API. They run on this machine when you send the request.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        center.addWidget(note)
        self.stack = QStackedWidget()
        self.pre_editor = EditorWithSearch(language="python")
        self.pre_editor.editor.setPlaceholderText("# Runs before the request is sent\n# pm.environment.set(\"timestamp\", str(int(time.time())))")
        self.post_editor = EditorWithSearch(language="python")
        self.post_editor.editor.setPlaceholderText("# Runs after the response is received\n# pm.test(\"Status is 200\", pm.response.code == 200)")
        self.pre_editor.editor.textChanged.connect(self.changed.emit)
        self.post_editor.editor.textChanged.connect(self.changed.emit)
        self.stack.addWidget(self.pre_editor)
        self.stack.addWidget(self.post_editor)
        center.addWidget(self.stack, 1)
        center_widget = QWidget()
        center_widget.setLayout(center)
        root.addWidget(center_widget, 1)

        side = QVBoxLayout()
        side.setSpacing(4)
        snippets_title = QLabel("SNIPPETS")
        snippets_title.setObjectName("sectionTitle")
        side.addWidget(snippets_title)
        self.snippets = QListWidget()
        self.snippets.setWordWrap(True)
        self.snippets.itemClicked.connect(self._insert_snippet)
        side.addWidget(self.snippets, 1)
        side_widget = QWidget()
        side_widget.setLayout(side)
        side_widget.setFixedWidth(220)
        root.addWidget(side_widget)

        self.nav.currentRowChanged.connect(self._on_nav)
        self.nav.setCurrentRow(0)

    def _on_nav(self, row: int) -> None:
        self.stack.setCurrentIndex(max(0, row))
        event = "prerequest" if row == 0 else "test"
        self.snippets.clear()
        for title, snippet_event, code in SCRIPT_SNIPPETS:
            if snippet_event in ("any", event):
                item = QListWidgetItem(title)
                item.setData(Qt.UserRole, code)
                item.setForeground(theme.qcolor("link"))
                item.setToolTip(code)
                self.snippets.addItem(item)

    def _insert_snippet(self, item: QListWidgetItem) -> None:
        editor = self.pre_editor.editor if self.stack.currentIndex() == 0 else self.post_editor.editor
        cursor = editor.textCursor()
        text = editor.toPlainText()
        prefix = "" if not text or text.endswith("\n") else "\n"
        cursor.movePosition(cursor.End)
        cursor.insertText(prefix + item.data(Qt.UserRole))
        editor.setTextCursor(cursor)
        editor.setFocus()

    def set_scripts(self, pre: str, post: str) -> None:
        self.pre_editor.editor.setPlainText(pre)
        self.post_editor.editor.setPlainText(post)
        if not pre.strip() and post.strip():
            self.nav.setCurrentRow(1)

    def scripts(self) -> tuple:
        return self.pre_editor.editor.toPlainText(), self.post_editor.editor.toPlainText()


class DocsPanel(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(6)
        bar = QHBoxLayout()
        label = QLabel("Markdown documentation for this request")
        label.setObjectName("muted")
        bar.addWidget(label)
        bar.addStretch(1)
        self.toggle = QToolButton()
        self.toggle.setObjectName("segButton")
        self.toggle.setText("Preview")
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self._toggle)
        bar.addWidget(self.toggle)
        root.addLayout(bar)
        self.stack = QStackedWidget()
        self.editor = EditorWithSearch(language="text", line_numbers=False)
        self.editor.editor.setPlaceholderText("Describe what this request does, its parameters and expected responses…")
        self.editor.editor.set_wrap(True)
        self.editor.editor.textChanged.connect(self.changed.emit)
        self.preview = QTextBrowser()
        self.preview.setObjectName("markdownView")
        self.preview.setOpenExternalLinks(True)
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.preview)
        root.addWidget(self.stack, 1)

    def _toggle(self, preview: bool) -> None:
        if preview:
            self.preview.setMarkdown(self.editor.editor.toPlainText() or "_No documentation yet._")
        self.stack.setCurrentIndex(1 if preview else 0)

    def set_text(self, text: str) -> None:
        self.editor.editor.setPlainText(text)
        if text.strip():
            self.toggle.setChecked(True)
            self._toggle(True)
        else:
            self.toggle.setChecked(False)

    def text(self) -> str:
        return self.editor.editor.toPlainText()
