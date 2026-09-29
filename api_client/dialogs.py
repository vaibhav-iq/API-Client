"""Dialogs and secondary views: save request, collection settings, code snippets, import, quick look."""
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Tuple

from PyQt5.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .code_editor import EditorWithSearch
from .codegen import GENERATORS
from .engine import HttpSpec
from .kv_table import KeyValueTable
from .models import Folder, KeyValue
from .request_panels import AuthPanel
from .theme import G

if TYPE_CHECKING:  # pragma: no cover
    from .main_window import MainWindow


class ThemedDialog(QDialog):
    """Dialog with a footer bar and a title bar that follows the app theme."""

    def __init__(self, parent: Optional[QWidget], title: str, width: int = 640, height: int = 480) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(width, height)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(0)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(20, 16, 20, 16)
        self.body_layout.setSpacing(10)
        self.root.addWidget(self.body, 1)
        self.footer = QFrame()
        self.footer.setObjectName("dialogFooter")
        self.footer_layout = QHBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(16, 10, 16, 10)
        self.footer_layout.addStretch(1)
        self.root.addWidget(self.footer)

    def add_button(self, text: str, callback: Callable, primary: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        if primary:
            btn.setObjectName("primaryButton")
            btn.setDefault(True)
        btn.clicked.connect(callback)
        self.footer_layout.addWidget(btn)
        return btn

    def showEvent(self, event) -> None:
        super().showEvent(event)
        theme.apply_native_titlebar(self)


def _container_tree(app: "MainWindow", tree: QTreeWidget, select_id: Optional[str] = None) -> None:
    tree.clear()
    target = None

    def add(parent_item: QTreeWidgetItem, folder: Folder, cid: str) -> None:
        nonlocal target
        for node in folder.items:
            if isinstance(node, Folder):
                item = QTreeWidgetItem([node.name])
                item.setIcon(0, theme.icon(G.FOLDER, "muted", 15))
                item.setData(0, Qt.UserRole, (cid, node.id))
                parent_item.addChild(item)
                if node.id == select_id:
                    target = item
                add(item, node, cid)

    for coll in app.collections:
        item = QTreeWidgetItem([coll.name])
        item.setIcon(0, theme.icon(G.LIBRARY, "muted", 15))
        item.setData(0, Qt.UserRole, (coll.id, None))
        tree.addTopLevelItem(item)
        if coll.id == select_id:
            target = item
        add(item, coll, coll.id)
    tree.expandAll()
    if target is not None:
        tree.setCurrentItem(target)
    elif tree.topLevelItemCount():
        tree.setCurrentItem(tree.topLevelItem(0))


class SaveRequestDialog(ThemedDialog):
    def __init__(self, app: "MainWindow", name: str, select_id: Optional[str] = None) -> None:
        super().__init__(app, "Save Request", 560, 520)
        self.app = app
        label = QLabel("Request name")
        label.setObjectName("sectionTitle")
        self.name_edit = QLineEdit(name)
        self.name_edit.selectAll()
        self.body_layout.addWidget(label)
        self.body_layout.addWidget(self.name_edit)
        self.body_layout.addSpacing(6)
        row = QHBoxLayout()
        target_label = QLabel("Save to")
        target_label.setObjectName("sectionTitle")
        row.addWidget(target_label)
        row.addStretch(1)
        new_coll = QPushButton("New Collection")
        new_coll.setObjectName("linkButton")
        new_coll.clicked.connect(self._new_collection)
        new_folder = QPushButton("New Folder")
        new_folder.setObjectName("linkButton")
        new_folder.clicked.connect(self._new_folder)
        row.addWidget(new_folder)
        row.addWidget(new_coll)
        self.body_layout.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setObjectName("dataTree")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.body_layout.addWidget(self.tree, 1)
        _container_tree(app, self.tree, select_id)
        self.add_button("Cancel", self.reject)
        self.save_btn = self.add_button("Save", self._accept, primary=True)
        self.result_target: Optional[Tuple[str, Optional[str]]] = None

    def _new_collection(self) -> None:
        coll = self.app.new_collection(prompt=True, open_panel=False)
        if coll is not None:
            _container_tree(self.app, self.tree, coll.id)

    def _new_folder(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        cid, fid = item.data(0, Qt.UserRole)
        folder = self.app.add_folder(cid, fid or cid)
        if folder is not None:
            _container_tree(self.app, self.tree, folder.id)

    def _accept(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            if not self.app.collections:
                self._new_collection()
                item = self.tree.currentItem()
            if item is None:
                return
        self.result_target = item.data(0, Qt.UserRole)
        self.accept()

    def name(self) -> str:
        return self.name_edit.text().strip() or "Untitled Request"


class ContainerDialog(ThemedDialog):
    """Edit a collection or folder: overview, authorization and (collections only) variables."""

    def __init__(self, app: "MainWindow", node: Folder, is_collection: bool, collection_id: str) -> None:
        super().__init__(app, f"Edit {'Collection' if is_collection else 'Folder'} · {node.name}", 820, 600)
        self.node = node
        self.is_collection = is_collection
        self.body_layout.setContentsMargins(20, 10, 20, 10)
        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        overview = QWidget()
        form = QFormLayout(overview)
        form.setContentsMargins(0, 14, 0, 0)
        form.setVerticalSpacing(12)
        self.name_edit = QLineEdit(node.name)
        self.desc_edit = EditorWithSearch(language="text", line_numbers=False)
        self.desc_edit.editor.set_wrap(True)
        self.desc_edit.editor.setPlainText(node.description)
        self.desc_edit.editor.setPlaceholderText("Markdown description")
        form.addRow("Name", self.name_edit)
        form.addRow("Description", self.desc_edit)
        tabs.addTab(overview, "Overview")

        auth_wrap = QWidget()
        aw = QVBoxLayout(auth_wrap)
        aw.setContentsMargins(0, 6, 0, 0)
        self.auth_panel = AuthPanel(allow_inherit=not is_collection, token_fetcher=lambda panel, cfg: app.fetch_oauth_token(panel, cfg, collection_id))
        self.auth_panel.set_config(node.auth)
        aw.addWidget(self.auth_panel)
        tabs.addTab(auth_wrap, "Authorization")

        self.vars_table: Optional[KeyValueTable] = None
        if is_collection:
            vars_wrap = QWidget()
            vw = QVBoxLayout(vars_wrap)
            vw.setContentsMargins(0, 10, 0, 0)
            note = QLabel("Collection variables are available to every request in this collection. Environment values override them.")
            note.setObjectName("muted")
            note.setWordWrap(True)
            vw.addWidget(note)
            self.vars_table = KeyValueTable(show_description=False, key_header="Variable", key_placeholder="Add new variable")
            self.vars_table.set_items(node.variables)  # type: ignore[attr-defined]
            vw.addWidget(self.vars_table, 1)
            tabs.addTab(vars_wrap, "Variables")

        self.body_layout.addWidget(tabs, 1)
        self.add_button("Cancel", self.reject)
        self.add_button("Save", self.accept, primary=True)

    def apply(self) -> None:
        self.node.name = self.name_edit.text().strip() or self.node.name
        self.node.description = self.desc_edit.editor.toPlainText()
        self.node.auth = self.auth_panel.get_config()
        if self.vars_table is not None:
            self.node.variables = [kv if kv.kind in ("default", "secret") else KeyValue(kv.key, kv.value, kv.description, kv.enabled, "default") for kv in self.vars_table.items()]  # type: ignore[attr-defined]


class CodeSnippetDialog(ThemedDialog):
    def __init__(self, parent: QWidget, spec: Optional[HttpSpec], error: str = "", last_language: str = "cURL") -> None:
        super().__init__(parent, "Code snippet", 900, 600)
        self.spec = spec
        row = QHBoxLayout()
        row.setSpacing(12)
        self.languages = QListWidget()
        self.languages.setObjectName("settingsNav")
        self.languages.setFixedWidth(210)
        for name in GENERATORS:
            self.languages.addItem(QListWidgetItem(name))
        row.addWidget(self.languages)
        right = QVBoxLayout()
        top = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("h2")
        top.addWidget(self.title)
        top.addStretch(1)
        copy_btn = QPushButton("Copy")
        copy_btn.setIcon(theme.icon(G.COPY, "muted", 14))
        copy_btn.clicked.connect(self._copy)
        top.addWidget(copy_btn)
        right.addLayout(top)
        self.editor = EditorWithSearch(language="text", read_only=True)
        right.addWidget(self.editor, 1)
        note = QLabel("Variables are resolved using the active environment. Secrets appear in plain text — review before sharing.")
        note.setObjectName("faint")
        note.setWordWrap(True)
        right.addWidget(note)
        row.addLayout(right, 1)
        self.body_layout.addLayout(row, 1)
        self.add_button("Close", self.accept, primary=True)
        self.error = error
        self.languages.currentTextChanged.connect(self._render)
        items = self.languages.findItems(last_language, Qt.MatchExactly)
        self.languages.setCurrentItem(items[0] if items else self.languages.item(0))

    def _render(self, name: str) -> None:
        self.title.setText(name)
        if self.spec is None:
            self.editor.editor.setPlainText(f"Cannot generate a snippet:\n{self.error}")
            return
        generator, language = GENERATORS[name]
        self.editor.editor.set_language(language)
        self.editor.editor.setPlainText(generator(self.spec))

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.editor.editor.toPlainText())
        self.title.setText(self.languages.currentItem().text() + "  ·  copied")

    def language(self) -> str:
        item = self.languages.currentItem()
        return item.text() if item else "cURL"


class ImportDialog(ThemedDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, "Import", 720, 520)
        self.setAcceptDrops(True)
        self.selected_file: Optional[Path] = None
        self.raw_text = ""
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        self.tabs = tabs

        file_page = QWidget()
        fl = QVBoxLayout(file_page)
        fl.setContentsMargins(0, 16, 0, 0)
        drop = QFrame()
        drop.setObjectName("card")
        dl = QVBoxLayout(drop)
        dl.setContentsMargins(24, 40, 24, 40)
        icon = QLabel()
        icon.setPixmap(theme.icon(G.UPLOAD, "faint", 40).pixmap(QSize(40, 40)))
        icon.setAlignment(Qt.AlignCenter)
        text = QLabel("Drop a file here, or")
        text.setAlignment(Qt.AlignCenter)
        text.setObjectName("muted")
        browse = QPushButton("Choose File")
        browse.setObjectName("accentButton")
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(browse)
        row.addStretch(1)
        self.file_label = QLabel("")
        self.file_label.setAlignment(Qt.AlignCenter)
        dl.addWidget(icon)
        dl.addWidget(text)
        dl.addLayout(row)
        dl.addWidget(self.file_label)
        fl.addWidget(drop)
        formats = QLabel(
            "Supported: Postman Collection v2.0 / v2.1, Postman Environment, OpenAPI 3.x and Swagger 2.0 (JSON), cURL."
        )
        formats.setObjectName("faint")
        formats.setWordWrap(True)
        fl.addWidget(formats)
        fl.addStretch(1)
        tabs.addTab(file_page, "File")

        raw_page = QWidget()
        rl = QVBoxLayout(raw_page)
        rl.setContentsMargins(0, 12, 0, 0)
        self.raw_editor = EditorWithSearch(language="json")
        self.raw_editor.editor.setPlaceholderText("Paste a cURL command, Postman collection JSON or OpenAPI JSON…")
        rl.addWidget(self.raw_editor, 1)
        tabs.addTab(raw_page, "Raw text")

        self.body_layout.addWidget(tabs, 1)
        self.add_button("Cancel", self.reject)
        self.add_button("Import", self._accept, primary=True)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import file", str(Path.cwd()), "API files (*.json *.yaml *.yml *.txt *.sh);;All files (*)")
        if path:
            self._set_file(Path(path))

    def _set_file(self, path: Path) -> None:
        self.selected_file = path
        self.file_label.setText(path.name)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls:
            self._set_file(Path(urls[0].toLocalFile()))
            self.tabs.setCurrentIndex(0)
            self._accept()

    def _accept(self) -> None:
        if self.tabs.currentIndex() == 1:
            self.raw_text = self.raw_editor.editor.toPlainText()
            self.selected_file = None
            if not self.raw_text.strip():
                return
        elif self.selected_file is None:
            return
        self.accept()


class EnvironmentTab(QWidget):
    kind = "environment"
    dirtyChanged = pyqtSignal(bool)
    titleChanged = pyqtSignal()

    def __init__(self, app: "MainWindow", env_id: str) -> None:
        super().__init__()
        self.app = app
        self.env_id = env_id
        self._loading = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self._save)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 14)
        root.setSpacing(10)
        header = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(theme.icon(G.GLOBE if env_id == "globals" else G.LAYERS, "muted", 20).pixmap(QSize(20, 20)))
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("titleEdit")
        self.name_edit.setReadOnly(env_id == "globals")
        self.name_edit.textChanged.connect(self._on_changed)
        header.addWidget(icon)
        header.addWidget(self.name_edit, 1)
        self.active_btn = QPushButton()
        self.active_btn.setCursor(Qt.PointingHandCursor)
        self.active_btn.clicked.connect(self._toggle_active)
        self.active_btn.setVisible(env_id != "globals")
        header.addWidget(self.active_btn)
        root.addLayout(header)
        note = QLabel(
            "Global variables are available in every request, in every collection."
            if env_id == "globals"
            else "Environment variables override collection and global variables while this environment is active."
        )
        note.setObjectName("muted")
        root.addWidget(note)
        self.table = KeyValueTable(
            type_options=[("default", "default"), ("secret", "secret")],
            show_description=False,
            key_header="Variable",
            key_placeholder="Add new variable",
            value_header="Value",
        )
        self.table.changed.connect(self._on_changed)
        root.addWidget(self.table, 1)
        saved = QLabel("Changes are saved automatically.")
        saved.setObjectName("faint")
        root.addWidget(saved)
        self.reload()

    def title(self) -> str:
        return "Globals" if self.env_id == "globals" else (self.name_edit.text().strip() or "Environment")

    def method(self) -> str:
        return ""

    def is_dirty(self) -> bool:
        return False

    def reload(self) -> None:
        env = self.app.find_environment(self.env_id)
        if env is None:
            return
        self._loading = True
        self.name_edit.setText(env.name)
        if self.table.table.state() != QAbstractItemView.EditingState:
            self.table.set_items(env.variables)
        self._loading = False
        self._refresh_active()

    def _refresh_active(self) -> None:
        active = self.app.active_env_id == self.env_id
        self.active_btn.setText("Active" if active else "Set as active")
        self.active_btn.setIcon(theme.icon(G.CHECK, "success", 14) if active else theme.icon(G.LAYERS, "muted", 14))

    def _toggle_active(self) -> None:
        self.app.set_active_environment(None if self.app.active_env_id == self.env_id else self.env_id)
        self._refresh_active()

    def _on_changed(self, *_args) -> None:
        if self._loading:
            return
        self.titleChanged.emit()
        self._save_timer.start()

    def _save(self) -> None:
        items = [kv if kv.kind in ("default", "secret") else KeyValue(kv.key, kv.value, kv.description, kv.enabled, "default") for kv in self.table.items()]
        self.app.update_environment(self.env_id, self.name_edit.text().strip(), items)

    def flush(self) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save()


class QuickLook(QFrame):
    """Popup that shows the active environment and globals at a glance (Postman's eye button)."""

    def __init__(self, app: "MainWindow") -> None:
        super().__init__(app, Qt.Popup)
        self.setObjectName("quickLook")
        self.app = app
        self.setFixedWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        self.env_title = QLabel()
        self.env_title.setObjectName("h2")
        env_row = QHBoxLayout()
        env_row.addWidget(self.env_title)
        env_row.addStretch(1)
        self.env_edit = QPushButton("Edit")
        self.env_edit.setObjectName("linkButton")
        self.env_edit.clicked.connect(self._edit_env)
        env_row.addWidget(self.env_edit)
        layout.addLayout(env_row)
        self.env_table = self._table()
        layout.addWidget(self.env_table)
        glob_row = QHBoxLayout()
        glob_title = QLabel("Globals")
        glob_title.setObjectName("h2")
        glob_row.addWidget(glob_title)
        glob_row.addStretch(1)
        glob_edit = QPushButton("Edit")
        glob_edit.setObjectName("linkButton")
        glob_edit.clicked.connect(lambda: self._open("globals"))
        glob_row.addWidget(glob_edit)
        layout.addLayout(glob_row)
        self.glob_table = self._table()
        layout.addWidget(self.glob_table)

    @staticmethod
    def _table() -> QTableWidget:
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Variable", "Value"])
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(28)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.setColumnWidth(0, 180)
        table.setMaximumHeight(200)
        return table

    @staticmethod
    def _fill(table: QTableWidget, items: List[KeyValue]) -> None:
        rows = [kv for kv in items if kv.key]
        table.setRowCount(len(rows) or 1)
        if not rows:
            empty = QTableWidgetItem("No variables")
            empty.setForeground(theme.qcolor("faint"))
            table.setItem(0, 0, empty)
            table.setItem(0, 1, QTableWidgetItem(""))
        for idx, kv in enumerate(rows):
            key = QTableWidgetItem(kv.key)
            value = QTableWidgetItem("••••••••" if kv.kind == "secret" else kv.value)
            value.setToolTip("" if kv.kind == "secret" else kv.value)
            if not kv.enabled:
                key.setForeground(theme.qcolor("faint"))
                value.setForeground(theme.qcolor("faint"))
            table.setItem(idx, 0, key)
            table.setItem(idx, 1, value)
        table.setFixedHeight(min(220, 40 + 28 * max(1, len(rows))))

    def _edit_env(self) -> None:
        if self.app.active_env_id:
            self._open(self.app.active_env_id)
        else:
            self._open(None)

    def _open(self, env_id: Optional[str]) -> None:
        self.hide()
        if env_id is None:
            self.app.sidebar.show_panel(1)
        else:
            self.app.open_environment(env_id)

    def popup(self, anchor: QWidget) -> None:
        env = self.app.find_environment(self.app.active_env_id) if self.app.active_env_id else None
        self.env_title.setText(env.name if env else "No Environment")
        self.env_edit.setText("Edit" if env else "Manage")
        self._fill(self.env_table, env.variables if env else [])
        self._fill(self.glob_table, self.app.globals_env.variables)
        self.adjustSize()
        pos = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 4))
        self.move(pos)
        self.show()
