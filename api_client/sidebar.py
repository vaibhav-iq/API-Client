"""Left sidebar: icon rail plus Collections, Environments and History panels."""
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .models import Collection, Folder
from .theme import G
from .widgets import tool_button

if TYPE_CHECKING:  # pragma: no cover
    from .main_window import MainWindow

ROLE = Qt.UserRole


def _search_field(placeholder: str) -> QLineEdit:
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    field.setClearButtonEnabled(True)
    field.addAction(theme.icon(G.SEARCH, "faint", 14), QLineEdit.LeadingPosition)
    return field


def _empty_state(title: str, text: str, button_text: str, callback) -> QWidget:
    wrap = QWidget()
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(20, 30, 20, 20)
    layout.setSpacing(8)
    t = QLabel(title)
    t.setAlignment(Qt.AlignCenter)
    t.setObjectName("h2")
    d = QLabel(text)
    d.setWordWrap(True)
    d.setAlignment(Qt.AlignCenter)
    d.setObjectName("muted")
    btn = QPushButton(button_text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.clicked.connect(callback)
    layout.addWidget(t)
    layout.addWidget(d)
    row = QHBoxLayout()
    row.addStretch(1)
    row.addWidget(btn)
    row.addStretch(1)
    layout.addLayout(row)
    layout.addStretch(1)
    return wrap


class Rail(QFrame):
    changed = pyqtSignal(int)

    ITEMS = [(G.LIBRARY, "Collections"), (G.LAYERS, "Environments"), (G.HISTORY, "History")]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("rail")
        self.setFixedWidth(84)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(4)
        self.group = QButtonGroup(self)
        self.buttons: List[QToolButton] = []
        for idx, (glyph, label) in enumerate(self.ITEMS):
            btn = QToolButton()
            btn.setObjectName("railButton")
            btn.setText(label)
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setIconSize(QSize(20, 20))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumWidth(72)
            btn.setProperty("glyph", glyph)
            btn.setProperty("glyphSize", 20)
            btn.setProperty("glyphColor", "muted")
            self.group.addButton(btn, idx)
            self.buttons.append(btn)
            layout.addWidget(btn)
        layout.addStretch(1)
        self.group.buttonClicked.connect(lambda _b: self.changed.emit(self.group.checkedId()))
        self.buttons[0].setChecked(True)
        self.refresh_icons()
        theme.manager().changed.connect(self.refresh_icons)

    def refresh_icons(self, *_args) -> None:
        for btn in self.buttons:
            btn.setIcon(theme.icon(btn.property("glyph"), "muted", 20))

    def select(self, index: int) -> None:
        self.buttons[index].setChecked(True)
        self.changed.emit(index)


class _PanelHeader(QFrame):
    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("panelHeader")
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(12, 10, 8, 4)
        self.layout_.setSpacing(2)
        label = QLabel(title)
        label.setObjectName("h2")
        self.layout_.addWidget(label)
        self.layout_.addStretch(1)

    def add(self, widget: QWidget) -> None:
        self.layout_.addWidget(widget)


class CollectionTree(QTreeWidget):
    moved = pyqtSignal()

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        self.moved.emit()


class CollectionsPanel(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self.app = app
        self._expanded: Set[str] = set()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        header = _PanelHeader("Collections")
        add_btn = tool_button(G.ADD, "New collection")
        add_btn.clicked.connect(self.app.new_collection)
        import_btn = tool_button(G.IMPORT, "Import (Ctrl+O)")
        import_btn.clicked.connect(self.app.show_import_dialog)
        header.add(add_btn)
        header.add(import_btn)
        root.addWidget(header)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(10, 0, 10, 4)
        self.search = _search_field("Search collections")
        self.search.textChanged.connect(self._filter)
        search_row.addWidget(self.search)
        root.addLayout(search_row)

        self.stack = QStackedWidget()
        self.tree = CollectionTree()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setIconSize(QSize(38, 16))
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(True)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.tree.setDefaultDropAction(Qt.MoveAction)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemClicked.connect(self._on_click)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemExpanded.connect(lambda item: self._set_expanded(item, True))
        self.tree.itemCollapsed.connect(lambda item: self._set_expanded(item, False))
        self.tree.moved.connect(self._on_moved)
        self.tree.invisibleRootItem().setFlags(self.tree.invisibleRootItem().flags() & ~Qt.ItemIsDropEnabled)
        self.stack.addWidget(self.tree)
        self.stack.addWidget(
            _empty_state(
                "No collections yet",
                "Collections group related requests. Create one, or import a Postman collection, OpenAPI spec or cURL command.",
                "Create Collection",
                self.app.new_collection,
            )
        )
        root.addWidget(self.stack, 1)
        theme.manager().changed.connect(lambda *_: self.refresh())

    # -- building --------------------------------------------------------------------
    def _set_expanded(self, item: QTreeWidgetItem, expanded: bool) -> None:
        data = item.data(0, ROLE)
        if not data:
            return
        if expanded:
            self._expanded.add(data[2])
        else:
            self._expanded.discard(data[2])
        if data[0] in ("folder", "collection"):
            item.setIcon(0, self._folder_icon(data[0], expanded))

    @staticmethod
    def _folder_icon(kind: str, expanded: bool):
        if kind == "collection":
            return theme.icon(G.LIBRARY, "muted", 15)
        return theme.icon(G.FOLDER_OPEN if expanded else G.FOLDER, "muted", 15)

    def refresh(self, select_id: Optional[str] = None) -> None:
        selected = select_id
        if selected is None:
            current = self.tree.currentItem()
            if current is not None and current.data(0, ROLE):
                selected = current.data(0, ROLE)[2]
        self.tree.blockSignals(True)
        self.tree.clear()
        selected_item = None
        for coll in self.app.collections:
            item = QTreeWidgetItem([coll.name])
            item.setData(0, ROLE, ("collection", coll.id, coll.id))
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDropEnabled)
            item.setToolTip(0, coll.description[:300] if coll.description else coll.name)
            self.tree.addTopLevelItem(item)
            found = self._add_children(item, coll, coll, selected)
            if coll.id == selected:
                found = item
            selected_item = selected_item or found
            item.setIcon(0, self._folder_icon("collection", coll.id in self._expanded))
            item.setExpanded(coll.id in self._expanded)
        self.tree.blockSignals(False)
        if selected_item is not None:
            parent = selected_item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self.tree.setCurrentItem(selected_item)
            self.tree.scrollToItem(selected_item)
        self.stack.setCurrentIndex(0 if self.app.collections else 1)
        if self.search.text():
            self._filter(self.search.text())

    def _add_children(self, parent_item: QTreeWidgetItem, folder: Folder, coll: Collection, selected: Optional[str]):
        found = None
        for node in folder.items:
            if isinstance(node, Folder):
                item = QTreeWidgetItem([node.name])
                item.setData(0, ROLE, ("folder", coll.id, node.id))
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
                parent_item.addChild(item)
                child_found = self._add_children(item, node, coll, selected)
                item.setIcon(0, self._folder_icon("folder", node.id in self._expanded))
                item.setExpanded(node.id in self._expanded)
                found = found or child_found
            else:
                item = QTreeWidgetItem([node.name])
                item.setData(0, ROLE, ("request", coll.id, node.id))
                item.setIcon(0, theme.method_badge_icon(node.method, 38, 16))
                item.setToolTip(0, f"{node.method} {node.url}")
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
                parent_item.addChild(item)
            if node.id == selected:
                found = item
        return found

    def _filter(self, text: str) -> None:
        query = text.strip().lower()

        def apply(item: QTreeWidgetItem) -> bool:
            own = not query or query in item.text(0).lower() or query in (item.toolTip(0) or "").lower()
            child_visible = False
            for idx in range(item.childCount()):
                if apply(item.child(idx)):
                    child_visible = True
            item.setHidden(not (own or child_visible))
            if query and child_visible:
                item.setExpanded(True)
            return own or child_visible

        for idx in range(self.tree.topLevelItemCount()):
            apply(self.tree.topLevelItem(idx))

    # -- interaction ---------------------------------------------------------------
    def _on_click(self, item: QTreeWidgetItem, _col: int) -> None:
        kind, cid, nid = item.data(0, ROLE)
        if kind == "request":
            self.app.open_request(cid, nid)
        else:
            item.setExpanded(not item.isExpanded())

    def _on_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        kind, cid, nid = item.data(0, ROLE)
        if kind == "request":
            self.app.open_request(cid, nid)

    def _on_moved(self) -> None:
        def collect(item: QTreeWidgetItem) -> List:
            out = []
            for idx in range(item.childCount()):
                child = item.child(idx)
                kind, _cid, nid = child.data(0, ROLE)
                out.append((nid, collect(child) if kind == "folder" else None))
            return out

        structure: Dict[str, List] = {}
        for idx in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(idx)
            structure[top.data(0, ROLE)[1]] = collect(top)
        self.app.apply_tree_structure(structure)

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            menu.addAction(theme.icon(G.ADD), "New Collection", self.app.new_collection)
            menu.addAction(theme.icon(G.IMPORT), "Import…", self.app.show_import_dialog)
            menu.exec_(self.tree.viewport().mapToGlobal(pos))
            return
        kind, cid, nid = item.data(0, ROLE)
        if kind == "request":
            menu.addAction(theme.icon(G.NEW_WINDOW), "Open in New Tab", lambda: self.app.open_request(cid, nid, force_new=True))
            menu.addAction(theme.icon(G.PLAY), "Send", lambda: self.app.open_request(cid, nid, send=True))
            menu.addAction(theme.icon(G.CODE), "Copy as cURL", lambda: self.app.copy_request_as_curl(cid, nid))
            menu.addSeparator()
        else:
            menu.addAction(theme.icon(G.PLAY), "Run " + ("Collection" if kind == "collection" else "Folder"), lambda: self.app.run_collection(cid, None if kind == "collection" else nid))
            menu.addSeparator()
            menu.addAction(theme.icon(G.ADD), "Add Request", lambda: self.app.add_request(cid, nid))
            menu.addAction(theme.icon(G.FOLDER), "Add Folder", lambda: self.app.add_folder(cid, nid))
            menu.addSeparator()
            menu.addAction(theme.icon(G.SETTINGS), "Edit (auth, variables, docs)…", lambda: self.app.edit_container(cid, nid))
        menu.addAction(theme.icon(G.RENAME), "Rename", lambda: self.app.rename_node(cid, nid))
        menu.addAction(theme.icon(G.COPY), "Duplicate", lambda: self.app.duplicate_node(cid, nid))
        if kind == "collection":
            menu.addAction(theme.icon(G.DOWNLOAD), "Export…", lambda: self.app.export_collection(cid))
        menu.addSeparator()
        menu.addAction(theme.icon(G.DELETE, "danger"), "Delete", lambda: self.app.delete_node(cid, nid))
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def select_node(self, node_id: str) -> None:
        self.refresh(select_id=node_id)


class EnvironmentsPanel(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self.app = app
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        header = _PanelHeader("Environments")
        add_btn = tool_button(G.ADD, "New environment")
        add_btn.clicked.connect(self.app.new_environment)
        import_btn = tool_button(G.IMPORT, "Import environment")
        import_btn.clicked.connect(self.app.show_import_dialog)
        header.add(add_btn)
        header.add(import_btn)
        root.addWidget(header)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(10, 0, 10, 4)
        self.search = _search_field("Search environments")
        self.search.textChanged.connect(lambda _t: self.refresh())
        search_row.addWidget(self.search)
        root.addLayout(search_row)
        self.list = QListWidget()
        self.list.setIconSize(QSize(16, 16))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemClicked.connect(lambda item: self.app.open_environment(item.data(ROLE)))
        root.addWidget(self.list, 1)
        hint = QLabel("Pick the active environment in the top-right selector, or right-click an environment.")
        hint.setObjectName("faint")
        hint.setWordWrap(True)
        hint.setContentsMargins(12, 4, 12, 8)
        root.addWidget(hint)
        theme.manager().changed.connect(lambda *_: self.refresh())

    def refresh(self) -> None:
        query = self.search.text().strip().lower()
        self.list.clear()
        globals_item = QListWidgetItem(theme.icon(G.GLOBE, "muted", 15), "Globals")
        globals_item.setData(ROLE, "globals")
        self.list.addItem(globals_item)
        for env in self.app.environments:
            if query and query not in env.name.lower():
                continue
            active = env.id == self.app.active_env_id
            item = QListWidgetItem(theme.icon(G.CHECK if active else G.LAYERS, "success" if active else "muted", 15), env.name)
            item.setData(ROLE, env.id)
            item.setToolTip("Active environment" if active else "Click to edit")
            self.list.addItem(item)

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        env_id = item.data(ROLE)
        menu = QMenu(self)
        menu.addAction(theme.icon(G.EDIT), "Open", lambda: self.app.open_environment(env_id))
        if env_id != "globals":
            if env_id == self.app.active_env_id:
                menu.addAction(theme.icon(G.CLOSE), "Deactivate", lambda: self.app.set_active_environment(None))
            else:
                menu.addAction(theme.icon(G.CHECK), "Set as active", lambda: self.app.set_active_environment(env_id))
            menu.addSeparator()
            menu.addAction(theme.icon(G.RENAME), "Rename", lambda: self.app.rename_environment(env_id))
            menu.addAction(theme.icon(G.COPY), "Duplicate", lambda: self.app.duplicate_environment(env_id))
        menu.addAction(theme.icon(G.DOWNLOAD), "Export…", lambda: self.app.export_environment(env_id))
        if env_id != "globals":
            menu.addSeparator()
            menu.addAction(theme.icon(G.DELETE, "danger"), "Delete", lambda: self.app.delete_environment(env_id))
        menu.exec_(self.list.viewport().mapToGlobal(pos))


class HistoryPanel(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self.app = app
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        header = _PanelHeader("History")
        clear_btn = tool_button(G.CLEAR, "Clear all history")
        clear_btn.clicked.connect(self.app.clear_history)
        header.add(clear_btn)
        root.addWidget(header)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(10, 0, 10, 4)
        self.search = _search_field("Search history")
        self.search.textChanged.connect(lambda _t: self.refresh())
        search_row.addWidget(self.search)
        root.addLayout(search_row)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(12)
        self.tree.setIconSize(QSize(38, 16))
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemClicked.connect(self._on_click)
        root.addWidget(self.tree, 1)
        theme.manager().changed.connect(lambda *_: self.refresh())

    def refresh(self) -> None:
        query = self.search.text().strip().lower()
        self.tree.clear()
        groups: Dict[str, QTreeWidgetItem] = {}
        today = datetime.now().date()
        for entry in self.app.workspace.history.recent(500):
            if query and query not in entry["url"].lower() and query not in entry["method"].lower():
                continue
            when = datetime.fromtimestamp(entry["created_at"])
            delta = (today - when.date()).days
            label = "Today" if delta == 0 else "Yesterday" if delta == 1 else when.strftime("%B %d, %Y")
            group = groups.get(label)
            if group is None:
                group = QTreeWidgetItem([label])
                group.setFlags(Qt.ItemIsEnabled)
                group.setForeground(0, theme.qcolor("muted"))
                self.tree.addTopLevelItem(group)
                group.setExpanded(True)
                groups[label] = group
            item = QTreeWidgetItem([entry["url"]])
            item.setIcon(0, theme.method_badge_icon(entry["method"], 38, 16))
            item.setData(0, ROLE, entry["id"])
            status = entry["status"]
            elapsed = entry["elapsed_ms"]
            item.setToolTip(
                0,
                f"{entry['method']} {entry['url']}\n{when.strftime('%H:%M:%S')}"
                + (f"  ·  {status}" if status else "  ·  failed")
                + (f"  ·  {int(elapsed)} ms" if elapsed else ""),
            )
            group.addChild(item)

    def _on_click(self, item: QTreeWidgetItem, _col: int) -> None:
        entry_id = item.data(0, ROLE)
        if entry_id is not None:
            self.app.open_history(entry_id)

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None or item.data(0, ROLE) is None:
            return
        entry_id = item.data(0, ROLE)
        menu = QMenu(self)
        menu.addAction(theme.icon(G.NEW_WINDOW), "Open in New Tab", lambda: self.app.open_history(entry_id))
        menu.addAction(theme.icon(G.SAVE), "Save to Collection…", lambda: self.app.open_history(entry_id, save=True))
        menu.addSeparator()
        menu.addAction(theme.icon(G.DELETE, "danger"), "Delete", lambda: self.app.delete_history(entry_id))
        menu.exec_(self.tree.viewport().mapToGlobal(pos))


class Sidebar(QFrame):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self.setObjectName("sidebar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.rail = Rail()
        self.panels = QStackedWidget()
        self.collections = CollectionsPanel(app)
        self.environments = EnvironmentsPanel(app)
        self.history = HistoryPanel(app)
        self.panels.addWidget(self.collections)
        self.panels.addWidget(self.environments)
        self.panels.addWidget(self.history)
        self.rail.changed.connect(self._on_rail)
        layout.addWidget(self.rail)
        layout.addWidget(self.panels, 1)

    def _on_rail(self, index: int) -> None:
        self.panels.setCurrentIndex(index)
        if index == 2:
            self.history.refresh()

    def show_panel(self, index: int) -> None:
        self.rail.select(index)
