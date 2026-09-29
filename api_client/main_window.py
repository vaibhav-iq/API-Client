"""Main application window: top bar, sidebar, request tabs, console and status bar."""
import copy
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from PyQt5.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .codegen import curl as curl_snippet
from .config_store import AppSettings, SettingsStore
from .dialogs import CodeSnippetDialog, ContainerDialog, EnvironmentTab, ImportDialog, QuickLook, SaveRequestDialog, ThemedDialog
from .engine import RunResult, VariableContext, build_request, fetch_oauth2_token
from .importers import ImportResult, export_postman_collection, export_postman_environment, import_file, import_text
from .models import (
    AUTH_LABELS,
    AuthConfig,
    Collection,
    Environment,
    Folder,
    RequestModel,
    ancestors,
    clone_node,
    find_node,
    kv_map,
    kv_set,
    kv_unset,
    new_id,
    requests_in,
    walk,
)
from .request_tab import RequestTab
from .runner import RunnerDialog
from .settings_dialog import SettingsDialog
from .sidebar import Sidebar
from .storage import Workspace
from .theme import G
from .widgets import TabCloseButton, format_ms, refresh_tool_icons, tool_button, vdivider
from .workers import FunctionWorker

APP_NAME = "API Client"
APP_VERSION = "1.0"

SHORTCUTS = [
    ("Ctrl+T", "New request tab"),
    ("Ctrl+Enter", "Send request"),
    ("Ctrl+S", "Save request"),
    ("Ctrl+Shift+S", "Save request as…"),
    ("Ctrl+W", "Close tab"),
    ("Ctrl+Tab / Ctrl+Shift+Tab", "Next / previous tab"),
    ("Ctrl+L", "Focus the URL bar"),
    ("Ctrl+O", "Import"),
    ("Ctrl+Shift+C", "Generate code snippet"),
    ("Ctrl+F", "Find in the focused editor / response"),
    ("Ctrl+\\", "Toggle sidebar"),
    ("Ctrl+Alt+C", "Toggle console"),
    ("Ctrl+Alt+V", "Toggle two-pane (side-by-side) view"),
    ("Ctrl+,", "Settings"),
]


class Toast(QLabel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, error: bool = False) -> None:
        bg = theme.color("tooltip_bg")
        border = theme.color("danger") if error else theme.color("border_strong")
        self.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {theme.color('tooltip_fg')}; border: 1px solid {border};"
            f" border-radius: 6px; padding: 8px 16px; }}"
        )
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 44)
        self.raise_()
        self.show()
        self._timer.start(3500 if error else 2200)


class ConsolePanel(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("consolePanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)
        bar = QHBoxLayout()
        title = QLabel("Console")
        title.setObjectName("h2")
        bar.addWidget(title)
        bar.addStretch(1)
        clear = tool_button(G.CLEAR, "Clear console")
        clear.clicked.connect(lambda: self.tree.clear())
        bar.addWidget(clear)
        self.close_btn = tool_button(G.CLOSE, "Hide console", 12)
        bar.addWidget(self.close_btn)
        layout.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setObjectName("consoleTree")
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Time", "Method", "URL", "Status", "Duration"])
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        for col, width in ((0, 80), (1, 70), (3, 110), (4, 90)):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            self.tree.setColumnWidth(col, width)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setStretchLastSection(False)
        layout.addWidget(self.tree, 1)

    def log(self, result: RunResult) -> None:
        resp = result.response
        status = f"{resp.status} {resp.reason}" if resp else ("Cancelled" if result.cancelled else "Error")
        item = QTreeWidgetItem(
            [
                datetime.now().strftime("%H:%M:%S"),
                result.method,
                result.url,
                status,
                format_ms(resp.elapsed_ms) if resp else "—",
            ]
        )
        item.setForeground(1, theme.qcolor("muted") if not result.method else QColor(theme.method_color(result.method)))
        item.setForeground(3, theme.qcolor("danger") if resp is None else QColor(theme.status_color(resp.status)))

        def section(title: str, lines: List[str]) -> None:
            if not lines:
                return
            node = QTreeWidgetItem([title])
            node.setFirstColumnSpanned(True)
            for line in lines[:200]:
                child = QTreeWidgetItem([line])
                child.setFirstColumnSpanned(True)
                node.addChild(child)
            item.addChild(node)

        if result.error:
            section("Error", result.error.splitlines())
        if result.request:
            section("Request Headers", [f"{k}: {v}" for k, v in result.request.headers])
            if result.request.body_preview:
                section("Request Body", result.request.body_preview.splitlines()[:200])
        if resp:
            section("Response Headers", [f"{k}: {v}" for k, v in resp.headers])
            body = resp.text[:4000]
            section("Response Body (preview)", body.splitlines()[:200])
        section("Script / Test Logs", [f"[{level}] {message}" for level, message in result.logs])
        self.tree.addTopLevelItem(item)
        while self.tree.topLevelItemCount() > 500:
            self.tree.takeTopLevelItem(0)
        self.tree.scrollToItem(item)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(theme.app_icon())
        self.resize(1560, 960)
        self.setAcceptDrops(True)

        local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        self.app_data_dir = local_app_data / "APIClient"
        self.settings_store = SettingsStore(self.app_data_dir / "settings.json")
        self.settings: AppSettings = self.settings_store.load()
        self.workspace = Workspace(self.app_data_dir)
        self.collections: List[Collection] = self.workspace.load_collections()
        self.globals_env, self.environments, self.active_env_id = self.workspace.load_environments()
        self._workers: Set[Any] = set()
        self._dialogs: List[QWidget] = []
        self._last_code_language = "cURL"

        theme.apply_app_theme(self.settings.theme_mode, self.settings.editor_font_size)
        self._build_ui()
        self._build_menus()
        self._build_shortcuts()

        self.sidebar.collections.refresh()
        self.sidebar.environments.refresh()
        self._refresh_env_combo()
        self._auto_import_openapi()
        self._restore_session()
        self._update_workspace_page()

        self._session_timer = QTimer(self)
        self._session_timer.setInterval(30000)
        self._session_timer.timeout.connect(self._save_session)
        self._session_timer.start()
        theme.manager().changed.connect(self._on_theme_changed)

    # =====================================================================
    # UI construction
    # =====================================================================
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_top_bar())

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.sidebar = Sidebar(self)
        self.sidebar.setMinimumWidth(260)
        self.main_splitter.addWidget(self.sidebar)

        self.vertical_splitter = QSplitter(Qt.Vertical)
        self.vertical_splitter.setChildrenCollapsible(True)
        self.workspace_stack = QStackedWidget()
        self.workspace_stack.setObjectName("workspaceStack")
        self.workspace_stack.addWidget(self._build_landing())
        self.tabs = QTabWidget()
        self.tabs.setObjectName("requestTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.tabBar().setIconSize(QSize(40, 16))
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._tab_menu)
        self.tabs.tabBar().installEventFilter(self)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        corner = QWidget()
        cl = QHBoxLayout(corner)
        cl.setContentsMargins(4, 0, 6, 0)
        cl.setSpacing(2)
        new_tab_btn = tool_button(G.ADD, "New request (Ctrl+T)", 14)
        new_tab_btn.clicked.connect(lambda: self.new_request_tab())
        tabs_list_btn = tool_button(G.CHEVRON_DOWN, "Open tabs", 12)
        tabs_list_btn.setPopupMode(QToolButton.InstantPopup)
        self._tabs_menu = QMenu(tabs_list_btn)
        self._tabs_menu.aboutToShow.connect(self._fill_tabs_menu)
        tabs_list_btn.setMenu(self._tabs_menu)
        cl.addWidget(new_tab_btn)
        cl.addWidget(tabs_list_btn)
        self.tabs.setCornerWidget(corner, Qt.TopRightCorner)
        self.workspace_stack.addWidget(self.tabs)
        self.vertical_splitter.addWidget(self.workspace_stack)

        self.console = ConsolePanel()
        self.console.close_btn.clicked.connect(lambda: self.toggle_console(False))
        self.console.hide()
        self.vertical_splitter.addWidget(self.console)
        self.vertical_splitter.setStretchFactor(0, 5)
        self.vertical_splitter.setStretchFactor(1, 2)
        self.main_splitter.addWidget(self.vertical_splitter)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([340, 1220])
        layout.addWidget(self.main_splitter, 1)

        self._build_status_bar()
        self._toast_label = Toast(root)
        self.quick_look = QuickLook(self)

    def _build_top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topBar")
        bar.setFixedHeight(52)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 0, 12, 0)
        layout.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(theme.app_icon().pixmap(QSize(24, 24)))
        title = QLabel(APP_NAME)
        title.setObjectName("appTitle")
        layout.addWidget(logo)
        layout.addWidget(title)
        layout.addSpacing(16)

        new_btn = QPushButton("New")
        new_btn.setObjectName("accentButton")
        new_btn.setIcon(theme.icon(G.ADD, "#ffffff", 14))
        new_btn.setCursor(Qt.PointingHandCursor)
        new_menu = QMenu(new_btn)
        new_menu.addAction(theme.icon(G.SEND), "HTTP Request\tCtrl+T", lambda: self.new_request_tab())
        new_menu.addAction(theme.icon(G.LIBRARY), "Collection", lambda: self.new_collection())
        new_menu.addAction(theme.icon(G.LAYERS), "Environment", self.new_environment)
        new_btn.setMenu(new_menu)
        new_btn.setObjectName("accentButton")
        import_btn = QPushButton("Import")
        import_btn.setIcon(theme.icon(G.IMPORT, "muted", 14))
        import_btn.setCursor(Qt.PointingHandCursor)
        import_btn.clicked.connect(self.show_import_dialog)
        layout.addWidget(new_btn)
        layout.addWidget(import_btn)
        layout.addStretch(1)

        self.env_combo = QComboBox()
        self.env_combo.setObjectName("envCombo")
        self.env_combo.setToolTip("Active environment")
        self.env_combo.setCursor(Qt.PointingHandCursor)
        self.env_combo.currentIndexChanged.connect(self._on_env_combo)
        eye = tool_button(G.EYE, "Environment quick look")
        eye.clicked.connect(lambda: self.quick_look.popup(eye))
        layout.addWidget(self.env_combo)
        layout.addWidget(eye)
        layout.addWidget(vdivider(22))
        self.theme_btn = tool_button(G.SUN if theme.is_dark() else G.MOON, "Toggle light / dark theme")
        self.theme_btn.clicked.connect(self.toggle_theme)
        settings_btn = tool_button(G.SETTINGS, "Settings (Ctrl+,)")
        settings_btn.clicked.connect(self.show_settings)
        layout.addWidget(self.theme_btn)
        layout.addWidget(settings_btn)
        return bar

    def _build_landing(self) -> QWidget:
        page = QWidget()
        page.setObjectName("workspace")
        outer = QVBoxLayout(page)
        outer.addStretch(1)
        card = QWidget()
        card.setMaximumWidth(620)
        layout = QVBoxLayout(card)
        layout.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(theme.app_icon().pixmap(QSize(64, 64)))
        icon.setAlignment(Qt.AlignCenter)
        title = QLabel("Build, test and debug your APIs")
        title.setObjectName("h1")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("Create a request, organise it in collections, switch environments and automate checks with tests.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addSpacing(10)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for text, primary, callback, glyph in (
            ("New Request", True, lambda: self.new_request_tab(), G.SEND),
            ("New Collection", False, lambda: self.new_collection(), G.LIBRARY),
            ("Import", False, self.show_import_dialog, G.IMPORT),
        ):
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIcon(theme.icon(glyph, "#ffffff" if primary else "muted", 14))
            if primary:
                btn.setObjectName("primaryButton")
            btn.clicked.connect(callback)
            buttons.addWidget(btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addSpacing(18)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        for idx, (keys, label) in enumerate(SHORTCUTS[:8]):
            k = QLabel(keys)
            k.setObjectName("muted")
            d = QLabel(label)
            d.setObjectName("faint")
            grid.addWidget(k, idx // 2, (idx % 2) * 2, Qt.AlignRight)
            grid.addWidget(d, idx // 2, (idx % 2) * 2 + 1, Qt.AlignLeft)
        layout.addLayout(grid)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)
        return page

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self.sidebar_btn = tool_button(G.SIDEBAR, "Toggle sidebar (Ctrl+\\)", 14)
        self.sidebar_btn.clicked.connect(self.toggle_sidebar)
        self.console_btn = tool_button(G.CONSOLE, "Toggle console (Ctrl+Alt+C)", 14, text="Console")
        self.console_btn.clicked.connect(lambda: self.toggle_console())
        bar.addWidget(self.sidebar_btn)
        bar.addWidget(self.console_btn)
        self.status_label = QLabel("")
        bar.addWidget(self.status_label, 1)
        runner_btn = tool_button(G.PLAY, "Run the collection of the current request", 14, text="Runner")
        runner_btn.clicked.connect(self._runner_from_status)
        self.layout_btn = tool_button(G.DOCK_RIGHT, "Toggle two-pane view (Ctrl+Alt+V)", 14)
        self.layout_btn.clicked.connect(self.toggle_layout)
        shortcuts_btn = tool_button(G.KEYBOARD, "Keyboard shortcuts", 14)
        shortcuts_btn.clicked.connect(self.show_shortcuts)
        bar.addPermanentWidget(runner_btn)
        bar.addPermanentWidget(self.layout_btn)
        bar.addPermanentWidget(shortcuts_btn)

    def _build_menus(self) -> None:
        mb = self.menuBar()

        def act(menu: QMenu, text: str, slot, shortcut: Optional[str] = None, glyph: Optional[str] = None) -> QAction:
            action = QAction(text, self)
            if glyph:
                action.setIcon(theme.icon(glyph))
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _checked=False: slot())
            menu.addAction(action)
            return action

        file_menu = mb.addMenu("&File")
        act(file_menu, "New Request", self.new_request_tab, "Ctrl+T", G.SEND)
        act(file_menu, "New Collection", self.new_collection, None, G.LIBRARY)
        act(file_menu, "New Environment", self.new_environment, None, G.LAYERS)
        file_menu.addSeparator()
        act(file_menu, "Import…", self.show_import_dialog, "Ctrl+O", G.IMPORT)
        file_menu.addSeparator()
        act(file_menu, "Save", lambda: self._with_request_tab(lambda t: self.save_tab(t)), "Ctrl+S", G.SAVE)
        act(file_menu, "Save As…", lambda: self._with_request_tab(lambda t: self.save_tab(t, save_as=True)), "Ctrl+Shift+S")
        act(file_menu, "Close Tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        file_menu.addSeparator()
        act(file_menu, "Settings", self.show_settings, "Ctrl+,", G.SETTINGS)
        file_menu.addSeparator()
        act(file_menu, "Exit", self.close)

        view_menu = mb.addMenu("&View")
        act(view_menu, "Toggle Sidebar", self.toggle_sidebar, "Ctrl+\\", G.SIDEBAR)
        act(view_menu, "Toggle Console", lambda: self.toggle_console(), "Ctrl+Alt+C", G.CONSOLE)
        act(view_menu, "Toggle Two-Pane View", self.toggle_layout, "Ctrl+Alt+V", G.DOCK_RIGHT)
        view_menu.addSeparator()
        act(view_menu, "Toggle Light / Dark Theme", self.toggle_theme, None, G.SUN)
        view_menu.addSeparator()
        act(view_menu, "Collections", lambda: self._show_sidebar_panel(0), None, G.LIBRARY)
        act(view_menu, "Environments", lambda: self._show_sidebar_panel(1), None, G.LAYERS)
        act(view_menu, "History", lambda: self._show_sidebar_panel(2), None, G.HISTORY)

        run_menu = mb.addMenu("&Run")
        act(run_menu, "Send Request", self.send_current, None, G.SEND)
        act(run_menu, "Generate Code Snippet", lambda: self._with_request_tab(self.show_code_snippet), "Ctrl+Shift+C", G.CODE)
        act(run_menu, "Collection Runner…", self._runner_from_status, None, G.PLAY)

        help_menu = mb.addMenu("&Help")
        act(help_menu, "Keyboard Shortcuts", self.show_shortcuts, None, G.KEYBOARD)
        act(help_menu, "Open Data Folder", lambda: os.startfile(str(self.app_data_dir)) if hasattr(os, "startfile") else None, None, G.FOLDER)
        help_menu.addSeparator()
        act(help_menu, "About", self.show_about, None, G.INFO)

    def _build_shortcuts(self) -> None:
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(seq), self, activated=self.send_current, context=Qt.ApplicationShortcut)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: self._with_request_tab(lambda t: t.focus_url()))
        QShortcut(QKeySequence("Ctrl+Tab"), self, activated=lambda: self._cycle_tab(1), context=Qt.ApplicationShortcut)
        QShortcut(QKeySequence("Ctrl+Shift+Tab"), self, activated=lambda: self._cycle_tab(-1), context=Qt.ApplicationShortcut)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=lambda: self.new_request_tab())

    # =====================================================================
    # Theme / layout
    # =====================================================================
    def toast(self, message: str, error: bool = False) -> None:
        self._toast_label.show_message(message, error)

    def toggle_theme(self) -> None:
        self.settings.theme_mode = "light" if theme.is_dark() else "dark"
        self.settings_store.save(self.settings)
        theme.apply_app_theme(self.settings.theme_mode, self.settings.editor_font_size)

    def _on_theme_changed(self, *_args) -> None:
        refresh_tool_icons(self)
        self.theme_btn.setIcon(theme.icon(G.SUN if theme.is_dark() else G.MOON, "muted", 16))
        self.theme_btn.setProperty("glyph", G.SUN if theme.is_dark() else G.MOON)
        for idx in range(self.tabs.count()):
            self._update_tab(self.tabs.widget(idx))
        self._refresh_env_combo()
        theme.apply_native_titlebar(self)

    def toggle_sidebar(self) -> None:
        self.sidebar.setVisible(not self.sidebar.isVisible())

    def toggle_console(self, show: Optional[bool] = None) -> None:
        visible = (not self.console.isVisible()) if show is None else show
        self.console.setVisible(visible)
        if visible and self.vertical_splitter.sizes()[1] < 80:
            total = sum(self.vertical_splitter.sizes())
            self.vertical_splitter.setSizes([int(total * 0.72), int(total * 0.28)])

    def toggle_layout(self) -> None:
        self.settings.layout_mode = "horizontal" if self.settings.layout_mode == "vertical" else "vertical"
        self.settings_store.save(self.settings)
        for tab in self._request_tabs():
            tab.set_layout_mode(self.settings.layout_mode)

    def _show_sidebar_panel(self, index: int) -> None:
        self.sidebar.setVisible(True)
        self.sidebar.show_panel(index)

    def show_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec_() != dialog.Accepted:
            return
        old_layout = self.settings.layout_mode
        self.settings = dialog.values()
        self.settings_store.save(self.settings)
        theme.apply_app_theme(self.settings.theme_mode, self.settings.editor_font_size)
        if old_layout != self.settings.layout_mode:
            for tab in self._request_tabs():
                tab.set_layout_mode(self.settings.layout_mode)

    def show_shortcuts(self) -> None:
        dialog = ThemedDialog(self, "Keyboard Shortcuts", 520, 520)
        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(24)
        for row, (keys, label) in enumerate(SHORTCUTS):
            k = QLabel(keys)
            k.setStyleSheet(f"font-weight: 600; color: {theme.color('text')};")
            d = QLabel(label)
            d.setObjectName("muted")
            grid.addWidget(k, row, 0)
            grid.addWidget(d, row, 1)
        dialog.body_layout.addLayout(grid)
        dialog.body_layout.addStretch(1)
        dialog.add_button("Close", dialog.accept, primary=True)
        dialog.exec_()

    def show_about(self) -> None:
        dialog = ThemedDialog(self, f"About {APP_NAME}", 540, 420)
        row = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(theme.app_icon().pixmap(QSize(64, 64)))
        row.addWidget(icon, 0, Qt.AlignTop)
        text = QVBoxLayout()
        title = QLabel(APP_NAME)
        title.setObjectName("h1")
        version = QLabel(f"Version {APP_VERSION}")
        version.setObjectName("muted")
        dev = QLabel("Developer: Vaibhav Patil")
        features = QLabel(
            "• Collections with folders, drag & drop and inherited auth\n"
            "• Environments, globals, collection variables and {{variable}} autocomplete\n"
            "• Postman v2.1, OpenAPI 3 / Swagger 2 and cURL import · Postman export\n"
            "• Params, auth (API key, Bearer, Basic, Digest, JWT, OAuth 2.0), body modes, GraphQL\n"
            "• No-code tests, variable extraction, Python pre/post scripts\n"
            "• Collection runner, history, console and code generation"
        )
        features.setObjectName("muted")
        features.setWordWrap(True)
        data = QLabel(f"Data folder: {self.app_data_dir}")
        data.setObjectName("faint")
        data.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for widget in (title, version, dev, features, data):
            text.addWidget(widget)
        text.addStretch(1)
        row.addLayout(text, 1)
        dialog.body_layout.addLayout(row)
        dialog.add_button("OK", dialog.accept, primary=True)
        dialog.exec_()

    # =====================================================================
    # Tabs
    # =====================================================================
    def _request_tabs(self) -> List[RequestTab]:
        return [self.tabs.widget(i) for i in range(self.tabs.count()) if isinstance(self.tabs.widget(i), RequestTab)]

    def _env_tabs(self) -> List[EnvironmentTab]:
        return [self.tabs.widget(i) for i in range(self.tabs.count()) if isinstance(self.tabs.widget(i), EnvironmentTab)]

    def current_request_tab(self) -> Optional[RequestTab]:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, RequestTab) else None

    def _with_request_tab(self, fn) -> None:
        tab = self.current_request_tab()
        if tab is not None:
            fn(tab)

    def _update_workspace_page(self) -> None:
        self.workspace_stack.setCurrentIndex(1 if self.tabs.count() else 0)

    def _add_tab(self, widget: QWidget) -> int:
        idx = self.tabs.addTab(widget, "")
        close_btn = TabCloseButton()
        close_btn.clicked.connect(lambda _c=False, w=widget: self.close_tab(self.tabs.indexOf(w)))
        self.tabs.tabBar().setTabButton(idx, QTabBar.RightSide, close_btn)
        widget.dirtyChanged.connect(lambda _d, w=widget: self._update_tab(w))
        widget.titleChanged.connect(lambda w=widget: self._update_tab(w))
        self._update_tab(widget)
        self.tabs.setCurrentIndex(idx)
        self._update_workspace_page()
        return idx

    def _update_tab(self, widget: QWidget) -> None:
        idx = self.tabs.indexOf(widget)
        if idx < 0:
            return
        title = widget.title()
        self.tabs.setTabText(idx, title if len(title) <= 28 else title[:26] + "…")
        if isinstance(widget, RequestTab):
            self.tabs.setTabIcon(idx, theme.method_badge_icon(widget.method(), 40, 16))
            self.tabs.setTabToolTip(idx, f"{widget.method()} {widget.url_edit.text()}")
        else:
            glyph = G.GLOBE if getattr(widget, "env_id", "") == "globals" else G.LAYERS
            self.tabs.setTabIcon(idx, theme.icon(glyph, "muted", 14))
            self.tabs.setTabToolTip(idx, title)
        button = self.tabs.tabBar().tabButton(idx, QTabBar.RightSide)
        if isinstance(button, TabCloseButton):
            button.set_dirty(widget.is_dirty())

    def _on_tab_changed(self, _idx: int) -> None:
        tab = self.current_request_tab()
        if tab is not None and tab.collection_id:
            self.sidebar.collections.tree.blockSignals(True)
            self.sidebar.collections.refresh(select_id=tab.model_id)
            self.sidebar.collections.tree.blockSignals(False)

    def _cycle_tab(self, step: int) -> None:
        count = self.tabs.count()
        if count:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % count)

    def _fill_tabs_menu(self) -> None:
        self._tabs_menu.clear()
        for idx in range(self.tabs.count()):
            widget = self.tabs.widget(idx)
            action = self._tabs_menu.addAction(self.tabs.tabIcon(idx), widget.title())
            action.triggered.connect(lambda _c=False, i=idx: self.tabs.setCurrentIndex(i))
        if self.tabs.count():
            self._tabs_menu.addSeparator()
            self._tabs_menu.addAction("Close all tabs", self.close_all_tabs)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.tabs.tabBar() and event.type() == QEvent.MouseButtonRelease and event.button() == Qt.MiddleButton:
            idx = self.tabs.tabBar().tabAt(event.pos())
            if idx >= 0:
                self.close_tab(idx)
                return True
        return super().eventFilter(obj, event)

    def _tab_menu(self, pos: QPoint) -> None:
        idx = self.tabs.tabBar().tabAt(pos)
        if idx < 0:
            return
        widget = self.tabs.widget(idx)
        menu = QMenu(self)
        if isinstance(widget, RequestTab):
            menu.addAction(theme.icon(G.COPY), "Duplicate Tab", lambda: self.new_request_tab(widget.collect_model().clone(fresh_id=True)))
            menu.addSeparator()
        menu.addAction("Close Tab", lambda: self.close_tab(self.tabs.indexOf(widget)))
        menu.addAction("Close Other Tabs", lambda: self.close_other_tabs(widget))
        menu.addAction("Close All Tabs", self.close_all_tabs)
        menu.exec_(self.tabs.tabBar().mapToGlobal(pos))

    def close_tab(self, idx: int, ask: bool = True) -> bool:
        if idx < 0 or idx >= self.tabs.count():
            return False
        widget = self.tabs.widget(idx)
        if isinstance(widget, RequestTab):
            if ask and widget.is_dirty():
                self.tabs.setCurrentIndex(idx)
                box = QMessageBox(self)
                box.setWindowTitle("Unsaved changes")
                box.setIcon(QMessageBox.Question)
                box.setText(f"Save changes to “{widget.title()}” before closing?")
                save = box.addButton("Save", QMessageBox.AcceptRole)
                discard = box.addButton("Don't Save", QMessageBox.DestructiveRole)
                box.addButton("Cancel", QMessageBox.RejectRole)
                box.exec_()
                clicked = box.clickedButton()
                if clicked is save:
                    if not self.save_tab(widget):
                        return False
                elif clicked is not discard:
                    return False
            if widget.is_busy():
                widget.cancel()
        elif isinstance(widget, EnvironmentTab):
            widget.flush()
        self.tabs.removeTab(self.tabs.indexOf(widget))
        widget.deleteLater()
        self._update_workspace_page()
        return True

    def close_other_tabs(self, keep: QWidget) -> None:
        for idx in reversed(range(self.tabs.count())):
            if self.tabs.widget(idx) is not keep:
                if not self.close_tab(idx):
                    return

    def close_all_tabs(self) -> None:
        for idx in reversed(range(self.tabs.count())):
            if not self.close_tab(idx):
                return

    def new_request_tab(self, model: Optional[RequestModel] = None, collection_id: Optional[str] = None) -> RequestTab:
        model = model or RequestModel(name="Untitled Request", auth=AuthConfig("inherit"))
        tab = RequestTab(self, model, collection_id)
        self._add_tab(tab)
        if not model.url:
            tab.focus_url()
        return tab

    def send_current(self) -> None:
        tab = self.current_request_tab()
        if tab is not None:
            tab.send()

    # =====================================================================
    # Variables & auth context (used by request tabs)
    # =====================================================================
    def find_collection(self, collection_id: Optional[str]) -> Optional[Collection]:
        if not collection_id:
            return None
        return next((c for c in self.collections if c.id == collection_id), None)

    def find_node(self, collection_id: str, node_id: str):
        coll = self.find_collection(collection_id)
        if coll is None:
            return None
        node, _parent = find_node(coll, node_id)
        return node

    def find_environment(self, env_id: Optional[str]) -> Optional[Environment]:
        if env_id == "globals":
            return self.globals_env
        return next((e for e in self.environments if e.id == env_id), None)

    def variable_context(self, collection_id: Optional[str]) -> VariableContext:
        env = self.find_environment(self.active_env_id) if self.active_env_id else None
        coll = self.find_collection(collection_id)
        return VariableContext(
            globals_=kv_map(self.globals_env.variables),
            collection=kv_map(coll.variables) if coll else {},
            environment=kv_map(env.variables) if env else {},
            has_environment=env is not None,
        )

    def inherited_auths(self, collection_id: Optional[str], request_id: str) -> List[AuthConfig]:
        coll = self.find_collection(collection_id)
        if coll is None:
            return []
        return [folder.auth for folder in ancestors(coll, request_id)]

    def inherited_auth_hint(self, collection_id: Optional[str], request_id: str) -> str:
        coll = self.find_collection(collection_id)
        if coll is None:
            return "Save this request to a collection to inherit its authorization. Until then, no auth is sent."
        for folder in ancestors(coll, request_id):
            if folder.auth.type != "inherit":
                where = "collection" if folder is coll else "folder"
                if folder.auth.type == "noauth":
                    return f"The parent {where} “{folder.name}” uses No Auth."
                return f"Using {AUTH_LABELS.get(folder.auth.type, folder.auth.type)} from {where} “{folder.name}”."
        return "No authorization is configured on the parent folders or collection."

    def breadcrumb(self, collection_id: Optional[str], request_id: str) -> str:
        coll = self.find_collection(collection_id)
        if coll is None:
            return ""
        chain = list(reversed(ancestors(coll, request_id)))
        if not chain:
            return ""
        return " / ".join(folder.name for folder in chain) + " /"

    def apply_variable_ops(self, ops: List[Tuple[str, str, str, Optional[str]]], collection_id: Optional[str]) -> None:
        if not ops:
            return
        env = self.find_environment(self.active_env_id) if self.active_env_id else None
        coll = self.find_collection(collection_id)
        envs_changed = colls_changed = False
        for scope, op, key, value in ops:
            if scope == "environment":
                target = (env or self.globals_env).variables
                envs_changed = True
            elif scope == "collection" and coll is not None:
                target = coll.variables
                colls_changed = True
            else:
                target = self.globals_env.variables
                envs_changed = True
            if op == "set":
                kv_set(target, key, value or "")
            else:
                kv_unset(target, key)
        if envs_changed:
            self._save_environments()
            for tab in self._env_tabs():
                tab.reload()
        if colls_changed:
            self._save_collections()
        self._refresh_contexts()

    def _refresh_contexts(self) -> None:
        for tab in self._request_tabs():
            tab.refresh_context()

    def fetch_oauth_token(self, panel, config: AuthConfig, collection_id: Optional[str]) -> None:
        ctx = self.variable_context(collection_id)
        settings = copy.deepcopy(self.settings)
        panel.set_fetching(True)
        worker = FunctionWorker(lambda: fetch_oauth2_token(config, ctx, settings))

        def done(data: Dict[str, Any]) -> None:
            try:
                panel.set_access_token(str(data.get("access_token", "")))
                panel.set_fetching(False)
            except RuntimeError:
                return
            expires = data.get("expires_in")
            self.toast(f"Access token received{f' (expires in {expires}s)' if expires else ''}")

        def failed(message: str) -> None:
            try:
                panel.set_fetching(False)
            except RuntimeError:
                pass
            QMessageBox.warning(self, "OAuth 2.0", f"Could not get an access token:\n\n{message}")

        worker.done.connect(done)
        worker.failed.connect(failed)
        self.retire_worker(worker)
        worker.start()

    def retire_worker(self, worker) -> None:
        self._workers.add(worker)
        worker.finished.connect(lambda w=worker: self._workers.discard(w))
        if worker.isFinished():
            self._workers.discard(worker)

    def on_request_finished(self, tab: RequestTab, result: RunResult) -> None:
        self.apply_variable_ops(result.ops, tab.collection_id)
        resp = result.response
        if not result.cancelled:
            try:
                self.workspace.history.add(
                    result.method or tab.method(),
                    result.url or tab.url_edit.text(),
                    resp.status if resp else None,
                    resp.elapsed_ms if resp else None,
                    tab.collect_model().to_dict(),
                )
            except Exception:
                pass
            if self.sidebar.panels.currentIndex() == 2:
                self.sidebar.history.refresh()
        self.console.log(result)
        if resp is not None:
            passed = sum(1 for t in result.tests if t.passed)
            tests = f" · tests {passed}/{len(result.tests)}" if result.tests else ""
            self.status_label.setText(f"{result.method} {resp.status} {resp.reason} · {format_ms(resp.elapsed_ms)}{tests}")
        elif result.error:
            self.status_label.setText("Request failed: " + result.error.splitlines()[0][:120])

    # =====================================================================
    # Saving requests
    # =====================================================================
    def save_tab(self, tab: RequestTab, save_as: bool = False) -> bool:
        model = tab.collect_model()
        if tab.collection_id and not save_as:
            coll = self.find_collection(tab.collection_id)
            if coll is not None:
                node, parent = find_node(coll, model.id)
                if isinstance(node, RequestModel) and parent is not None:
                    parent.items[parent.items.index(node)] = model
                    self._save_collections()
                    tab.mark_saved(coll.id)
                    self.sidebar.collections.refresh(select_id=model.id)
                    self.toast("Saved")
                    return True

        dialog = SaveRequestDialog(self, model.name, select_id=tab.collection_id)
        if dialog.exec_() != dialog.Accepted or dialog.result_target is None:
            return False
        cid, folder_id = dialog.result_target
        coll = self.find_collection(cid)
        if coll is None:
            return False
        parent: Folder = coll
        if folder_id:
            found, _ = find_node(coll, folder_id)
            if isinstance(found, Folder):
                parent = found
        model.name = dialog.name()
        if save_as or any(node.id == model.id for node, _ in walk(coll)):
            model.id = new_id()
        parent.items.append(model)
        self._save_collections()
        tab.model_id = model.id
        tab.name_edit.blockSignals(True)
        tab.name_edit.setText(model.name)
        tab.name_edit.blockSignals(False)
        tab.mark_saved(coll.id)
        self._update_tab(tab)
        self.sidebar.collections._expanded.update({coll.id, parent.id})
        self.sidebar.collections.refresh(select_id=model.id)
        self._show_sidebar_panel(0)
        self.toast(f"Saved to {coll.name}")
        return True

    def show_code_snippet(self, tab: RequestTab) -> None:
        spec, error = None, ""
        try:
            spec = build_request(
                tab.collect_model(),
                self.variable_context(tab.collection_id),
                self.settings,
                self.inherited_auths(tab.collection_id, tab.model_id),
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        dialog = CodeSnippetDialog(self, spec, error, self._last_code_language)
        dialog.exec_()
        self._last_code_language = dialog.language()

    def copy_request_as_curl(self, collection_id: str, request_id: str) -> None:
        node = self.find_node(collection_id, request_id)
        if not isinstance(node, RequestModel):
            return
        try:
            spec = build_request(node, self.variable_context(collection_id), self.settings, self.inherited_auths(collection_id, request_id))
        except Exception as exc:  # noqa: BLE001
            self.toast(str(exc).splitlines()[0], error=True)
            return
        QApplication.clipboard().setText(curl_snippet(spec))
        self.toast("cURL command copied to clipboard")

    # =====================================================================
    # Collections
    # =====================================================================
    def _save_collections(self) -> None:
        try:
            self.workspace.save_collections(self.collections)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Could not save collections: {exc}", error=True)

    def _ask_name(self, title: str, label: str, default: str) -> Optional[str]:
        text, ok = QInputDialog.getText(self, title, label, text=default)
        text = text.strip()
        return text if ok and text else None

    def new_collection(self, prompt: bool = True, open_panel: bool = True) -> Optional[Collection]:
        name = self._ask_name("New Collection", "Collection name:", "New Collection") if prompt else "New Collection"
        if not name:
            return None
        coll = Collection(name=name)
        self.collections.append(coll)
        self._save_collections()
        self.sidebar.collections._expanded.add(coll.id)
        self.sidebar.collections.refresh(select_id=coll.id)
        if open_panel:
            self._show_sidebar_panel(0)
        return coll

    def open_request(self, collection_id: str, request_id: str, force_new: bool = False, send: bool = False) -> None:
        if not force_new:
            for tab in self._request_tabs():
                if tab.model_id == request_id:
                    self.tabs.setCurrentWidget(tab)
                    if send:
                        tab.send()
                    return
        node = self.find_node(collection_id, request_id)
        if not isinstance(node, RequestModel):
            return
        if force_new:
            tab = self.new_request_tab(node.clone(fresh_id=True), None)
        else:
            tab = self.new_request_tab(node.clone(), collection_id)
        if send:
            tab.send()

    def add_request(self, collection_id: str, parent_id: str) -> None:
        coll = self.find_collection(collection_id)
        if coll is None:
            return
        parent, _ = find_node(coll, parent_id)
        if not isinstance(parent, Folder):
            return
        model = RequestModel(name="New Request")
        parent.items.append(model)
        self._save_collections()
        self.sidebar.collections._expanded.update({coll.id, parent.id})
        self.sidebar.collections.refresh(select_id=model.id)
        self.open_request(collection_id, model.id)

    def add_folder(self, collection_id: str, parent_id: str) -> Optional[Folder]:
        coll = self.find_collection(collection_id)
        if coll is None:
            return None
        parent, _ = find_node(coll, parent_id)
        if not isinstance(parent, Folder):
            return None
        name = self._ask_name("New Folder", "Folder name:", "New Folder")
        if not name:
            return None
        folder = Folder(name=name)
        parent.items.append(folder)
        self._save_collections()
        self.sidebar.collections._expanded.update({coll.id, parent.id})
        self.sidebar.collections.refresh(select_id=folder.id)
        return folder

    def edit_container(self, collection_id: str, node_id: str) -> None:
        coll = self.find_collection(collection_id)
        if coll is None:
            return
        node = coll if node_id == collection_id else find_node(coll, node_id)[0]
        if not isinstance(node, Folder):
            return
        dialog = ContainerDialog(self, node, node is coll, collection_id)
        if dialog.exec_() == dialog.Accepted:
            dialog.apply()
            self._save_collections()
            self.sidebar.collections.refresh()
            self._refresh_contexts()

    def rename_node(self, collection_id: str, node_id: str) -> None:
        coll = self.find_collection(collection_id)
        if coll is None:
            return
        node = coll if node_id == collection_id else find_node(coll, node_id)[0]
        if node is None:
            return
        name = self._ask_name("Rename", "New name:", node.name)
        if not name:
            return
        node.name = name
        self._save_collections()
        self.sidebar.collections.refresh()
        for tab in self._request_tabs():
            if tab.model_id == node_id:
                was_dirty = tab.is_dirty()
                tab.name_edit.setText(name)
                if not was_dirty:
                    tab.mark_saved(collection_id)
            elif tab.collection_id == collection_id:
                tab.refresh_context()

    def duplicate_node(self, collection_id: str, node_id: str) -> None:
        coll = self.find_collection(collection_id)
        if coll is None:
            return
        if node_id == collection_id:
            copy_coll = Collection.from_dict(coll.to_dict())
            copy_coll.id = new_id()
            for node, _ in walk(copy_coll):
                node.id = new_id()
            copy_coll.name = f"{coll.name} Copy"
            self.collections.insert(self.collections.index(coll) + 1, copy_coll)
            select = copy_coll.id
        else:
            node, parent = find_node(coll, node_id)
            if node is None or parent is None:
                return
            duplicate = clone_node(node)
            duplicate.name = f"{node.name} Copy"
            parent.items.insert(parent.items.index(node) + 1, duplicate)
            select = duplicate.id
        self._save_collections()
        self.sidebar.collections.refresh(select_id=select)

    def delete_node(self, collection_id: str, node_id: str) -> None:
        coll = self.find_collection(collection_id)
        if coll is None:
            return
        is_coll = node_id == collection_id
        node = coll if is_coll else find_node(coll, node_id)[0]
        if node is None:
            return
        kind = "collection" if is_coll else "folder" if isinstance(node, Folder) else "request"
        answer = QMessageBox.question(
            self,
            f"Delete {kind}",
            f"Delete the {kind} “{node.name}”?" + (" All requests inside it will be deleted." if kind != "request" else ""),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        removed_ids = {node.id}
        if isinstance(node, Folder):
            removed_ids.update(child.id for child, _ in walk(node))
        if is_coll:
            self.collections.remove(coll)
        else:
            _node, parent = find_node(coll, node_id)
            if parent is not None:
                parent.items.remove(node)
        self._save_collections()
        for tab in self._request_tabs():
            if tab.model_id in removed_ids or (is_coll and tab.collection_id == collection_id):
                tab.collection_id = None
                tab.mark_dirty()
                tab.refresh_context()
        self.sidebar.collections.refresh()

    def apply_tree_structure(self, structure: Dict[str, List]) -> None:
        index: Dict[str, Any] = {}
        for coll in self.collections:
            for node, _ in walk(coll):
                index[node.id] = node

        def build(entries: List) -> List:
            items = []
            for node_id, children in entries:
                node = index.get(node_id)
                if node is None:
                    continue
                if isinstance(node, Folder):
                    node.items = build(children or [])
                items.append(node)
            return items

        built = {cid: build(entries) for cid, entries in structure.items()}
        for coll in self.collections:
            if coll.id in built:
                coll.items = built[coll.id]
        self._save_collections()
        owner = {}
        for coll in self.collections:
            for node, _ in walk(coll):
                owner[node.id] = coll.id
        for tab in self._request_tabs():
            if tab.model_id in owner:
                tab.collection_id = owner[tab.model_id]
            tab.refresh_context()
        QTimer.singleShot(0, self.sidebar.collections.refresh)

    def export_collection(self, collection_id: str) -> None:
        coll = self.find_collection(collection_id)
        if coll is None:
            return
        safe = "".join(ch for ch in coll.name if ch.isalnum() or ch in " -_").strip() or "collection"
        path, _ = QFileDialog.getSaveFileName(self, "Export collection", f"{safe}.postman_collection.json", "JSON (*.json)")
        if not path:
            return
        Path(path).write_text(json.dumps(export_postman_collection(coll), indent=2, ensure_ascii=False), encoding="utf-8")
        self.toast(f"Exported {coll.name} (Postman v2.1)")

    def runner_items(self, collection_id: str, folder_id: Optional[str]) -> List[Tuple[RequestModel, List[AuthConfig]]]:
        coll = self.find_collection(collection_id)
        if coll is None:
            return []
        container: Folder = coll
        if folder_id:
            node, _ = find_node(coll, folder_id)
            if isinstance(node, Folder):
                container = node
        return [(req.clone(), self.inherited_auths(collection_id, req.id)) for req in requests_in(container)]

    def run_collection(self, collection_id: str, folder_id: Optional[str] = None) -> None:
        dialog = RunnerDialog(self, collection_id, folder_id)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        self._dialogs.append(dialog)
        dialog.destroyed.connect(lambda _o=None, d=dialog: self._dialogs.remove(d) if d in self._dialogs else None)
        dialog.show()

    def _runner_from_status(self) -> None:
        tab = self.current_request_tab()
        if tab is not None and tab.collection_id:
            self.run_collection(tab.collection_id)
            return
        if not self.collections:
            self.toast("Create or import a collection to use the runner", error=True)
            return
        names = [c.name for c in self.collections]
        name, ok = QInputDialog.getItem(self, "Collection Runner", "Collection to run:", names, 0, False)
        if ok:
            self.run_collection(self.collections[names.index(name)].id)

    # =====================================================================
    # Import
    # =====================================================================
    def show_import_dialog(self) -> None:
        dialog = ImportDialog(self)
        if dialog.exec_() != dialog.Accepted:
            return
        try:
            if dialog.selected_file is not None:
                result = import_file(dialog.selected_file)
            else:
                result = import_text(dialog.raw_text)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        self.apply_import(result)

    def apply_import(self, result: ImportResult) -> None:
        for coll in result.collections:
            self.collections.append(coll)
            self.sidebar.collections._expanded.add(coll.id)
        for env in result.environments:
            self.environments.append(env)
        if result.collections:
            self._save_collections()
            self.sidebar.collections.refresh(select_id=result.collections[-1].id)
            self._show_sidebar_panel(0)
        if result.environments:
            self._save_environments()
            self.sidebar.environments.refresh()
            self._refresh_env_combo()
            if not result.collections:
                self._show_sidebar_panel(1)
        for req in result.requests:
            self.new_request_tab(req)
        parts = []
        if result.collections:
            count = sum(len(requests_in(c)) for c in result.collections)
            parts.append(f"{len(result.collections)} collection(s), {count} request(s)")
        if result.environments:
            parts.append(f"{len(result.environments)} environment(s)")
        if result.requests:
            parts.append(f"{len(result.requests)} request(s)")
        if parts:
            self.toast("Imported " + ", ".join(parts))
        if "js-script" in result.notes:
            QMessageBox.information(
                self,
                "Scripts imported as comments",
                "This collection contains Postman JavaScript scripts. They were imported as commented-out text "
                "in each request's Scripts tab. Port them to Python (using the pm API) to run them.",
            )

    def _auto_import_openapi(self) -> None:
        """Keep the v1 behaviour: load ./openapi.json on first run as a collection."""
        default = Path.cwd() / "openapi.json"
        if not default.exists() or any(c.source == str(default) for c in self.collections):
            return
        try:
            result = import_file(default)
        except Exception:
            return
        if result.collections:
            for coll in result.collections:
                coll.source = str(default)
            self.apply_import(ImportResult(collections=result.collections))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_file():
                try:
                    self.apply_import(import_file(path))
                except Exception as exc:  # noqa: BLE001
                    QMessageBox.warning(self, "Import failed", f"{path.name}: {exc}")

    # =====================================================================
    # Environments
    # =====================================================================
    def _save_environments(self) -> None:
        try:
            self.workspace.save_environments(self.globals_env, self.environments, self.active_env_id)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Could not save environments: {exc}", error=True)

    def _refresh_env_combo(self) -> None:
        self.env_combo.blockSignals(True)
        self.env_combo.clear()
        self.env_combo.addItem(theme.icon(G.LAYERS, "faint", 14), "No Environment", None)
        for env in self.environments:
            self.env_combo.addItem(theme.icon(G.LAYERS, "success", 14), env.name, env.id)
        idx = self.env_combo.findData(self.active_env_id) if self.active_env_id else 0
        self.env_combo.setCurrentIndex(max(0, idx))
        self.env_combo.blockSignals(False)

    def _on_env_combo(self, _idx: int) -> None:
        self.set_active_environment(self.env_combo.currentData())

    def set_active_environment(self, env_id: Optional[str]) -> None:
        self.active_env_id = env_id
        self._save_environments()
        self._refresh_env_combo()
        self.sidebar.environments.refresh()
        for tab in self._env_tabs():
            tab.reload()
        self._refresh_contexts()

    def new_environment(self) -> None:
        name = self._ask_name("New Environment", "Environment name:", "New Environment")
        if not name:
            return
        env = Environment(name=name)
        self.environments.append(env)
        self._save_environments()
        self._refresh_env_combo()
        self.sidebar.environments.refresh()
        self._show_sidebar_panel(1)
        self.open_environment(env.id)

    def open_environment(self, env_id: str) -> None:
        for tab in self._env_tabs():
            if tab.env_id == env_id:
                self.tabs.setCurrentWidget(tab)
                return
        if self.find_environment(env_id) is None:
            return
        self._add_tab(EnvironmentTab(self, env_id))

    def update_environment(self, env_id: str, name: str, items) -> None:
        env = self.find_environment(env_id)
        if env is None:
            return
        if env_id != "globals" and name:
            env.name = name
        env.variables = list(items)
        self._save_environments()
        self._refresh_env_combo()
        self.sidebar.environments.refresh()
        self._refresh_contexts()

    def rename_environment(self, env_id: str) -> None:
        env = self.find_environment(env_id)
        if env is None or env_id == "globals":
            return
        name = self._ask_name("Rename Environment", "New name:", env.name)
        if not name:
            return
        env.name = name
        self._save_environments()
        self._refresh_env_combo()
        self.sidebar.environments.refresh()
        for tab in self._env_tabs():
            if tab.env_id == env_id:
                tab.reload()
                self._update_tab(tab)

    def duplicate_environment(self, env_id: str) -> None:
        env = self.find_environment(env_id)
        if env is None:
            return
        dup = Environment.from_dict(env.to_dict())
        dup.id = new_id()
        dup.name = f"{env.name} Copy"
        self.environments.append(dup)
        self._save_environments()
        self._refresh_env_combo()
        self.sidebar.environments.refresh()

    def export_environment(self, env_id: str) -> None:
        env = self.find_environment(env_id)
        if env is None:
            return
        safe = "".join(ch for ch in env.name if ch.isalnum() or ch in " -_").strip() or "environment"
        path, _ = QFileDialog.getSaveFileName(self, "Export environment", f"{safe}.postman_environment.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(export_postman_environment(env), indent=2, ensure_ascii=False), encoding="utf-8")
            self.toast(f"Exported {env.name}")

    def delete_environment(self, env_id: str) -> None:
        env = self.find_environment(env_id)
        if env is None or env_id == "globals":
            return
        if QMessageBox.question(self, "Delete environment", f"Delete the environment “{env.name}”?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        for tab in self._env_tabs():
            if tab.env_id == env_id:
                self.tabs.removeTab(self.tabs.indexOf(tab))
                tab.deleteLater()
        self.environments.remove(env)
        if self.active_env_id == env_id:
            self.active_env_id = None
        self._save_environments()
        self._refresh_env_combo()
        self.sidebar.environments.refresh()
        self._refresh_contexts()
        self._update_workspace_page()

    # =====================================================================
    # History
    # =====================================================================
    def open_history(self, entry_id: int, save: bool = False) -> None:
        data = self.workspace.history.get_request(entry_id)
        if not data:
            return
        model = RequestModel.from_dict(data)
        model.id = new_id()
        if model.name in ("", "Untitled Request", "New Request"):
            model.name = model.url.split("?", 1)[0].rstrip("/").split("/")[-1] or "Request"
        tab = self.new_request_tab(model)
        if save:
            self.save_tab(tab, save_as=True)

    def delete_history(self, entry_id: int) -> None:
        self.workspace.history.delete(entry_id)
        self.sidebar.history.refresh()

    def clear_history(self) -> None:
        if QMessageBox.question(self, "Clear history", "Delete all request history?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.workspace.history.clear()
            self.sidebar.history.refresh()

    # =====================================================================
    # Session
    # =====================================================================
    def _save_session(self) -> None:
        tabs = []
        for idx in range(self.tabs.count()):
            widget = self.tabs.widget(idx)
            if isinstance(widget, RequestTab):
                tabs.append({"kind": "request", "model": widget.collect_model().to_dict(), "collection_id": widget.collection_id})
            elif isinstance(widget, EnvironmentTab):
                tabs.append({"kind": "environment", "env_id": widget.env_id})
        session = {
            "tabs": tabs,
            "active": self.tabs.currentIndex(),
            "geometry": bytes(self.saveGeometry()).hex(),
            "maximized": self.isMaximized(),
            "main_splitter": self.main_splitter.sizes(),
            "sidebar_panel": self.sidebar.panels.currentIndex(),
            "sidebar_visible": self.sidebar.isVisible(),
            "expanded": sorted(self.sidebar.collections._expanded),
        }
        try:
            self.workspace.save_session(session)
        except Exception:
            pass

    def _restore_session(self) -> None:
        session = self.workspace.load_session()
        if session.get("geometry"):
            try:
                from PyQt5.QtCore import QByteArray

                self.restoreGeometry(QByteArray(bytes.fromhex(session["geometry"])))
            except Exception:
                pass
        if isinstance(session.get("main_splitter"), list) and len(session["main_splitter"]) == 2:
            self.main_splitter.setSizes([int(x) for x in session["main_splitter"]])
        self.sidebar.collections._expanded.update(session.get("expanded") or [])
        self.sidebar.collections.refresh()
        if session.get("sidebar_visible") is False:
            self.sidebar.hide()
        panel = session.get("sidebar_panel")
        if isinstance(panel, int) and 0 <= panel <= 2:
            self.sidebar.show_panel(panel)

        for entry in session.get("tabs") or []:
            try:
                if entry.get("kind") == "environment":
                    if self.find_environment(entry.get("env_id")) is not None:
                        self._add_tab(EnvironmentTab(self, entry["env_id"]))
                    continue
                model = RequestModel.from_dict(entry.get("model") or {})
                cid = entry.get("collection_id")
                saved = self.find_node(cid, model.id) if cid else None
                if not isinstance(saved, RequestModel):
                    cid = None
                tab = RequestTab(self, model, cid)
                if isinstance(saved, RequestModel):
                    tab._snapshot = saved.to_dict()
                    tab._check_dirty()
                self._add_tab(tab)
            except Exception:
                continue
        active = session.get("active")
        if isinstance(active, int) and 0 <= active < self.tabs.count():
            self.tabs.setCurrentIndex(active)
        self._pending_maximize = bool(session.get("maximized"))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        theme.apply_native_titlebar(self)
        if getattr(self, "_pending_maximize", False):
            self._pending_maximize = False
            QTimer.singleShot(0, self.showMaximized)

    def closeEvent(self, event) -> None:
        for tab in self._env_tabs():
            tab.flush()
        self._save_session()
        for tab in self._request_tabs():
            if tab.is_busy():
                tab.cancel()
        for dialog in list(self._dialogs):
            dialog.close()
        for worker in list(self._workers):
            worker.wait(1500)
        super().closeEvent(event)


# Backwards-compatible name used by the v1 launcher.
PostmanLikeTester = MainWindow
