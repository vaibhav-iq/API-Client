from dataclasses import replace

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .config_store import AppSettings
from .dialogs import ThemedDialog
from .theme import G


class _ThemePreview(QWidget):
    """Tiny painted mock-up of the app in a given theme."""

    def __init__(self, mode: str, parent=None) -> None:
        super().__init__(parent)
        self.mode = mode
        self.setFixedHeight(110)

    def paintEvent(self, _event) -> None:
        p = theme.PALETTES[self.mode]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        painter.setPen(QColor(p["border"]))
        painter.setBrush(QColor(p["bg"]))
        painter.drawRoundedRect(0, 0, w - 1, h - 1, 6, 6)
        painter.fillRect(1, 1, w - 2, 16, QColor(p["surface"]))
        painter.fillRect(1, 17, int(w * 0.26), h - 18, QColor(p["sidebar"]))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(p["accent"]))
        painter.drawRoundedRect(6, 5, 14, 7, 2, 2)
        x = int(w * 0.26) + 10
        painter.setBrush(QColor(p["input"]))
        painter.setPen(QColor(p["border_strong"]))
        painter.drawRoundedRect(x, 26, w - x - 50, 14, 3, 3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(p["primary"]))
        painter.drawRoundedRect(w - 44, 26, 36, 14, 3, 3)
        painter.setBrush(QColor(theme.METHOD_COLORS[self.mode]["GET"]))
        painter.drawRoundedRect(x + 4, 31, 14, 4, 2, 2)
        for i, key in enumerate(("GET", "POST", "PUT", "DELETE")):
            painter.setBrush(QColor(theme.METHOD_COLORS[self.mode][key]))
            painter.drawRoundedRect(8, 26 + i * 14, 12, 5, 2, 2)
            painter.setBrush(QColor(p["faint"]))
            painter.drawRoundedRect(24, 26 + i * 14, int(w * 0.26) - 32, 5, 2, 2)
        painter.setBrush(QColor(p["code_key"]))
        painter.drawRoundedRect(x, 52, 60, 5, 2, 2)
        painter.setBrush(QColor(p["code_string"]))
        painter.drawRoundedRect(x + 66, 52, 80, 5, 2, 2)
        painter.setBrush(QColor(p["code_number"]))
        painter.drawRoundedRect(x + 12, 64, 50, 5, 2, 2)
        painter.setBrush(QColor(p["faint"]))
        painter.drawRoundedRect(x, 80, w - x - 12, 5, 2, 2)
        painter.drawRoundedRect(x, 92, int((w - x) * 0.6), 5, 2, 2)


class _ThemeCard(QFrame):
    def __init__(self, mode: str, title: str, subtitle: str, on_select) -> None:
        super().__init__()
        self.setObjectName("themeCard")
        self.mode = mode
        self.on_select = on_select
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(_ThemePreview(mode))
        name = QLabel(title)
        name.setObjectName("h2")
        sub = QLabel(subtitle)
        sub.setObjectName("muted")
        layout.addWidget(name)
        layout.addWidget(sub)

    def mousePressEvent(self, event) -> None:
        self.on_select(self.mode)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class SettingsDialog(ThemedDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent, "Settings", 940, 640)
        self.setModal(True)
        self._settings = replace(settings)
        self._theme_mode = settings.theme_mode

        self.body_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("settingsNav")
        self.nav.setFixedWidth(200)
        for glyph, name in ((G.SETTINGS, "General"), (G.SUN, "Themes"), (G.GLOBE, "Proxy")):
            self.nav.addItem(QListWidgetItem(theme.icon(glyph, "muted", 15), name))
        row.addWidget(self.nav)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._page(self._build_general_page()))
        self.pages.addWidget(self._page(self._build_themes_page()))
        self.pages.addWidget(self._page(self._build_proxy_page()))
        row.addWidget(self.pages, 1)
        self.body_layout.addLayout(row, 1)

        self.add_button("Cancel", self.reject)
        self.add_button("Save", self._accept, primary=True)

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self._bind_settings_to_ui()

    @staticmethod
    def _page(content: QWidget) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.addWidget(content)
        return wrap

    def _build_general_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QLabel("General")
        title.setObjectName("h1")
        layout.addWidget(title)

        request_group = QGroupBox("Request")
        request_form = QFormLayout(request_group)
        request_form.setHorizontalSpacing(20)
        request_form.setVerticalSpacing(10)
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(0, 600000)
        self.timeout_input.setSingleStep(1000)
        self.timeout_input.setSuffix(" ms")
        self.timeout_input.setSpecialValueText("No timeout")
        self.max_response_input = QSpinBox()
        self.max_response_input.setRange(0, 2048)
        self.max_response_input.setSuffix(" MB")
        self.max_response_input.setSpecialValueText("Unlimited")
        self.follow_redirects_check = QCheckBox("Automatically follow redirects")
        self.ssl_verify_check = QCheckBox("SSL certificate verification")
        self.send_no_cache_check = QCheckBox("Send no-cache headers")
        self.send_postman_token_check = QCheckBox("Send Postman-Token header")
        self.send_user_agent_check = QCheckBox("Send User-Agent header (APIClient/1.0)")
        request_form.addRow("Request timeout", self.timeout_input)
        request_form.addRow("Max response size", self.max_response_input)
        for check in (self.follow_redirects_check, self.ssl_verify_check, self.send_no_cache_check, self.send_postman_token_check, self.send_user_agent_check):
            request_form.addRow("", check)
        layout.addWidget(request_group)

        ui_group = QGroupBox("User interface")
        ui_form = QFormLayout(ui_group)
        ui_form.setHorizontalSpacing(20)
        ui_form.setVerticalSpacing(10)
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Stacked (request above response)", "vertical")
        self.layout_combo.addItem("Side by side (two-pane)", "horizontal")
        self.font_size = QSpinBox()
        self.font_size.setRange(7, 24)
        self.font_size.setSuffix(" pt")
        ui_form.addRow("Request / response layout", self.layout_combo)
        ui_form.addRow("Editor font size", self.font_size)
        layout.addWidget(ui_group)
        layout.addStretch(1)
        return page

    def _build_themes_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QLabel("Themes")
        title.setObjectName("h1")
        hint = QLabel("Personalise the look of the API Client.")
        hint.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(hint)
        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.dark_card = _ThemeCard("dark", "Dark", "Easy on the eyes for long sessions", self._select_theme)
        self.light_card = _ThemeCard("light", "Light", "Crisp and bright", self._select_theme)
        cards.addWidget(self.dark_card, 1)
        cards.addWidget(self.light_card, 1)
        layout.addLayout(cards)
        layout.addStretch(1)
        return page

    def _select_theme(self, mode: str) -> None:
        self._theme_mode = mode
        self.dark_card.set_selected(mode == "dark")
        self.light_card.set_selected(mode == "light")

    def _build_proxy_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QLabel("Proxy")
        title.setObjectName("h1")
        layout.addWidget(title)

        top_group = QGroupBox("Proxy configuration")
        top_layout = QVBoxLayout(top_group)
        top_layout.setSpacing(8)
        self.use_system_proxy_check = QCheckBox("Use system proxy")
        self.respect_env_proxy_check = QCheckBox("Respect HTTP_PROXY / HTTPS_PROXY / NO_PROXY environment variables")
        self.use_custom_proxy_check = QCheckBox("Add a custom proxy configuration")
        top_layout.addWidget(self.use_system_proxy_check)
        top_layout.addWidget(self.respect_env_proxy_check)
        top_layout.addWidget(self.use_custom_proxy_check)

        self.custom_proxy_group = QGroupBox("Custom proxy")
        custom_form = QFormLayout(self.custom_proxy_group)
        custom_form.setHorizontalSpacing(20)
        custom_form.setVerticalSpacing(10)
        self.proxy_http_check = QCheckBox("HTTP")
        self.proxy_https_check = QCheckBox("HTTPS")
        proxy_type_row = QWidget()
        proxy_type_layout = QHBoxLayout(proxy_type_row)
        proxy_type_layout.setContentsMargins(0, 0, 0, 0)
        proxy_type_layout.setSpacing(18)
        proxy_type_layout.addWidget(self.proxy_http_check)
        proxy_type_layout.addWidget(self.proxy_https_check)
        proxy_type_layout.addStretch(1)

        self.proxy_host_input = QLineEdit()
        self.proxy_host_input.setPlaceholderText("127.0.0.1")
        self.proxy_port_input = QSpinBox()
        self.proxy_port_input.setRange(1, 65535)
        proxy_server_row = QWidget()
        proxy_server_layout = QHBoxLayout(proxy_server_row)
        proxy_server_layout.setContentsMargins(0, 0, 0, 0)
        proxy_server_layout.setSpacing(10)
        proxy_server_layout.addWidget(self.proxy_host_input, 1)
        proxy_server_layout.addWidget(QLabel("Port"), 0)
        proxy_server_layout.addWidget(self.proxy_port_input, 0)

        self.proxy_auth_check = QCheckBox("Enable auth")
        self.proxy_user_input = QLineEdit()
        self.proxy_user_input.setPlaceholderText("Username")
        self.proxy_pass_input = QLineEdit()
        self.proxy_pass_input.setPlaceholderText("Password")
        self.proxy_pass_input.setEchoMode(QLineEdit.Password)
        proxy_auth_row = QWidget()
        proxy_auth_layout = QHBoxLayout(proxy_auth_row)
        proxy_auth_layout.setContentsMargins(0, 0, 0, 0)
        proxy_auth_layout.setSpacing(10)
        proxy_auth_layout.addWidget(self.proxy_auth_check, 0)
        proxy_auth_layout.addWidget(self.proxy_user_input, 1)
        proxy_auth_layout.addWidget(self.proxy_pass_input, 1)

        self.proxy_bypass_input = QLineEdit()
        self.proxy_bypass_input.setPlaceholderText("127.0.0.1, localhost, *.example.com")

        custom_form.addRow("Proxy type", proxy_type_row)
        custom_form.addRow("Proxy server", proxy_server_row)
        custom_form.addRow("Proxy auth", proxy_auth_row)
        custom_form.addRow("Proxy bypass", self.proxy_bypass_input)

        layout.addWidget(top_group)
        layout.addWidget(self.custom_proxy_group)
        layout.addStretch(1)

        self.use_custom_proxy_check.toggled.connect(self._toggle_custom_proxy_fields)
        self.proxy_auth_check.toggled.connect(self._toggle_proxy_auth_fields)
        return page

    def _bind_settings_to_ui(self) -> None:
        s = self._settings
        self.timeout_input.setValue(s.request_timeout_ms)
        self.max_response_input.setValue(s.max_response_size_mb)
        self.follow_redirects_check.setChecked(s.follow_redirects)
        self.send_no_cache_check.setChecked(s.send_no_cache_header)
        self.send_postman_token_check.setChecked(s.send_postman_token_header)
        self.send_user_agent_check.setChecked(s.send_user_agent_header)
        self.ssl_verify_check.setChecked(s.ssl_verification)
        self.layout_combo.setCurrentIndex(max(0, self.layout_combo.findData(s.layout_mode)))
        self.font_size.setValue(s.editor_font_size)
        self._select_theme(s.theme_mode)

        self.use_system_proxy_check.setChecked(s.use_system_proxy)
        self.respect_env_proxy_check.setChecked(s.respect_env_proxy)
        self.use_custom_proxy_check.setChecked(s.use_custom_proxy)
        self.proxy_http_check.setChecked(s.proxy_http_enabled)
        self.proxy_https_check.setChecked(s.proxy_https_enabled)
        self.proxy_host_input.setText(s.proxy_host)
        self.proxy_port_input.setValue(s.proxy_port)
        self.proxy_auth_check.setChecked(s.proxy_auth_enabled)
        self.proxy_user_input.setText(s.proxy_username)
        self.proxy_pass_input.setText(s.proxy_password)
        self.proxy_bypass_input.setText(s.proxy_bypass)

        self._toggle_custom_proxy_fields(self.use_custom_proxy_check.isChecked())

    def _toggle_custom_proxy_fields(self, enabled: bool) -> None:
        self.custom_proxy_group.setEnabled(enabled)
        self._toggle_proxy_auth_fields(self.proxy_auth_check.isChecked())

    def _toggle_proxy_auth_fields(self, enabled: bool) -> None:
        can_edit = enabled and self.use_custom_proxy_check.isChecked()
        self.proxy_user_input.setEnabled(can_edit)
        self.proxy_pass_input.setEnabled(can_edit)

    def _accept(self) -> None:
        self._settings = replace(
            self._settings,
            theme_mode=self._theme_mode,
            layout_mode=self.layout_combo.currentData(),
            editor_font_size=self.font_size.value(),
            request_timeout_ms=self.timeout_input.value(),
            max_response_size_mb=self.max_response_input.value(),
            follow_redirects=self.follow_redirects_check.isChecked(),
            send_no_cache_header=self.send_no_cache_check.isChecked(),
            send_postman_token_header=self.send_postman_token_check.isChecked(),
            send_user_agent_header=self.send_user_agent_check.isChecked(),
            ssl_verification=self.ssl_verify_check.isChecked(),
            use_system_proxy=self.use_system_proxy_check.isChecked(),
            respect_env_proxy=self.respect_env_proxy_check.isChecked(),
            use_custom_proxy=self.use_custom_proxy_check.isChecked(),
            proxy_http_enabled=self.proxy_http_check.isChecked(),
            proxy_https_enabled=self.proxy_https_check.isChecked(),
            proxy_host=self.proxy_host_input.text().strip(),
            proxy_port=self.proxy_port_input.value(),
            proxy_auth_enabled=self.proxy_auth_check.isChecked(),
            proxy_username=self.proxy_user_input.text().strip(),
            proxy_password=self.proxy_pass_input.text(),
            proxy_bypass=self.proxy_bypass_input.text().strip(),
        )
        self.accept()

    def values(self) -> AppSettings:
        return self._settings
