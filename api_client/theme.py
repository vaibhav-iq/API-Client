"""Design tokens, global stylesheet and icon helpers for the API Client UI."""
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

from PyQt5.QtCore import QObject, QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt5.QtWidgets import QApplication, QWidget

PALETTES: Dict[str, Dict[str, str]] = {
    "dark": {
        "bg": "#212121",
        "surface": "#262626",
        "sidebar": "#1f1f1f",
        "rail": "#1a1a1a",
        "input": "#1b1b1b",
        "code_bg": "#1b1b1b",
        "btn_bg": "#2b2b2b",
        "menu_bg": "#2b2b2b",
        "border": "#333333",
        "border_strong": "#424242",
        "text": "#e8e8e8",
        "muted": "#a6a6a6",
        "faint": "#6b6b6b",
        "hover": "#2f2f2f",
        "selected": "#383838",
        "row_selected": "#2c2c2c",
        "accent": "#ff6c37",
        "accent_hover": "#ff855a",
        "primary": "#097bed",
        "primary_hover": "#2d8ff2",
        "focus": "#5a8fd6",
        "link": "#74aef6",
        "success": "#47c47e",
        "warning": "#f0b429",
        "danger": "#f26b5b",
        "info": "#74aef6",
        "selection": "#264f78",
        "selection_fg": "#ffffff",
        "tooltip_bg": "#353535",
        "tooltip_fg": "#f0f0f0",
        "scroll": "#454545",
        "scroll_hover": "#5a5a5a",
        "var_ok": "#ff8f5c",
        "var_bad": "#f26b5b",
        "line_no": "#5c5c5c",
        "current_line": "#242424",
        "search_hit": "#5c4a1f",
        "code_key": "#9cdcfe",
        "code_string": "#ce9178",
        "code_number": "#b5cea8",
        "code_keyword": "#569cd6",
        "code_punct": "#b4b4b4",
        "code_tag": "#569cd6",
        "code_attr": "#9cdcfe",
        "code_comment": "#6a9955",
        "code_builtin": "#dcdcaa",
    },
    "light": {
        "bg": "#ffffff",
        "surface": "#f9f9f9",
        "sidebar": "#ffffff",
        "rail": "#f4f4f4",
        "input": "#ffffff",
        "code_bg": "#ffffff",
        "btn_bg": "#f5f5f5",
        "menu_bg": "#ffffff",
        "border": "#e6e6e6",
        "border_strong": "#d4d4d4",
        "text": "#212121",
        "muted": "#6b6b6b",
        "faint": "#a6a6a6",
        "hover": "#f0f0f0",
        "selected": "#e8e8e8",
        "row_selected": "#f3f7fd",
        "accent": "#ff6c37",
        "accent_hover": "#e8551f",
        "primary": "#0265d2",
        "primary_hover": "#0053b3",
        "focus": "#0265d2",
        "link": "#0265d2",
        "success": "#0a8c3c",
        "warning": "#a86b00",
        "danger": "#d0271d",
        "info": "#0265d2",
        "selection": "#b3d4fc",
        "selection_fg": "#212121",
        "tooltip_bg": "#2b2b2b",
        "tooltip_fg": "#ffffff",
        "scroll": "#cfcfcf",
        "scroll_hover": "#b0b0b0",
        "var_ok": "#d4561c",
        "var_bad": "#d0271d",
        "line_no": "#b0b0b0",
        "current_line": "#f7f7f7",
        "search_hit": "#ffe8a3",
        "code_key": "#0451a5",
        "code_string": "#a31515",
        "code_number": "#098658",
        "code_keyword": "#0000ff",
        "code_punct": "#555555",
        "code_tag": "#800000",
        "code_attr": "#e50000",
        "code_comment": "#008000",
        "code_builtin": "#795e26",
    },
}

METHOD_COLORS: Dict[str, Dict[str, str]] = {
    "dark": {
        "GET": "#6bdd9a",
        "POST": "#ffe47e",
        "PUT": "#74aef6",
        "PATCH": "#c0a8e1",
        "DELETE": "#f79a8e",
        "HEAD": "#6bdd9a",
        "OPTIONS": "#f15eb0",
        "TRACE": "#a6a6a6",
    },
    "light": {
        "GET": "#007f31",
        "POST": "#ad7a03",
        "PUT": "#0053b8",
        "PATCH": "#623497",
        "DELETE": "#8e1a10",
        "HEAD": "#007f31",
        "OPTIONS": "#a61468",
        "TRACE": "#6b6b6b",
    },
}

METHOD_SHORT = {"DELETE": "DEL", "OPTIONS": "OPT", "PATCH": "PATCH", "TRACE": "TRACE", "HEAD": "HEAD"}


class G:
    """Segoe Fluent / MDL2 icon glyphs."""

    ADD = ""
    CLOSE = ""
    MORE = ""
    SETTINGS = ""
    SEARCH = ""
    SEND = ""
    DELETE = ""
    SAVE = ""
    PLAY = ""
    STOP = ""
    EDIT = ""
    CHEVRON_DOWN = ""
    CHEVRON_RIGHT = ""
    REFRESH = ""
    COPY = ""
    FOLDER = ""
    FOLDER_OPEN = ""
    DOC = ""
    HISTORY = ""
    GLOBE = ""
    DOWNLOAD = ""
    UPLOAD = ""
    IMPORT = ""
    EYE = ""
    CODE = ""
    LIBRARY = ""
    LAYERS = ""
    FILTER = ""
    SUN = ""
    MOON = ""
    CLEAR = ""
    RENAME = ""
    LOCK = ""
    LINK = ""
    CONSOLE = ""
    CHECK = ""
    ERROR = ""
    WARNING = ""
    INFO = ""
    CHECKLIST = ""
    LIST = ""
    PAGE = ""
    DOCK_BOTTOM = ""
    DOCK_RIGHT = ""
    NEW_WINDOW = ""
    WRAP = ""
    KEYBOARD = ""
    DUPLICATE = ""
    SIDEBAR = ""


class _ThemeManager(QObject):
    changed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.mode = "dark"
        self.editor_font_size = 10


_manager: Optional[_ThemeManager] = None
_icon_cache: Dict[Tuple, QIcon] = {}
_glyph_family: Optional[str] = None
_glyph_checked = False


def manager() -> _ThemeManager:
    global _manager
    if _manager is None:
        _manager = _ThemeManager()
    return _manager


def mode() -> str:
    return manager().mode


def is_dark() -> bool:
    return mode() == "dark"


def color(key: str) -> str:
    return PALETTES[mode()].get(key, key)


def qcolor(key: str) -> QColor:
    return QColor(color(key))


def method_color(method: str) -> str:
    return METHOD_COLORS[mode()].get((method or "").upper(), color("muted"))


def status_color(status: int) -> str:
    if status >= 500:
        return color("danger")
    if status >= 400:
        return color("warning")
    if status >= 300:
        return color("info")
    if status >= 200:
        return color("success")
    return color("muted")


def _first_family(candidates) -> Optional[str]:
    families = set(QFontDatabase().families())
    for name in candidates:
        if name in families:
            return name
    return None


def ui_font_family() -> str:
    return _first_family(["Segoe UI", "Inter", "Helvetica Neue", "Arial"]) or "Sans Serif"


def mono_font_family() -> str:
    return _first_family(["Cascadia Mono", "Cascadia Code", "JetBrains Mono", "Consolas", "Courier New"]) or "Monospace"


def mono_font(size: Optional[int] = None) -> QFont:
    font = QFont(mono_font_family())
    font.setStyleHint(QFont.Monospace)
    font.setPointSize(size or manager().editor_font_size)
    return font


def glyph_family() -> Optional[str]:
    global _glyph_family, _glyph_checked
    if not _glyph_checked:
        _glyph_family = _first_family(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
        _glyph_checked = True
    return _glyph_family


def _resolve_color(value: Optional[str]) -> QColor:
    if not value:
        return qcolor("muted")
    if value.startswith("#"):
        return QColor(value)
    return qcolor(value)


def icon(glyph: str, color_name: Optional[str] = None, size: int = 16) -> QIcon:
    col = _resolve_color(color_name)
    key = ("glyph", glyph, col.name(), size)
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached

    dpr = 2.0
    pix = QPixmap(int(size * dpr), int(size * dpr))
    pix.fill(Qt.transparent)
    pix.setDevicePixelRatio(dpr)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    family = glyph_family()
    font = QFont(family or ui_font_family())
    font.setPixelSize(max(8, int(size * 0.82)))
    painter.setFont(font)
    painter.setPen(col)
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, glyph if family else "•")
    painter.end()

    result = QIcon(pix)
    _icon_cache[key] = result
    return result


def method_badge_icon(method: str, width: int = 40, height: int = 16) -> QIcon:
    method = (method or "GET").upper()
    col = QColor(method_color(method))
    key = ("method", method, col.name(), width, height)
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached

    dpr = 2.0
    pix = QPixmap(int(width * dpr), int(height * dpr))
    pix.fill(Qt.transparent)
    pix.setDevicePixelRatio(dpr)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.TextAntialiasing)
    font = QFont(ui_font_family())
    font.setPixelSize(10)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(col)
    painter.drawText(QRectF(0, 0, width, height), Qt.AlignVCenter | Qt.AlignLeft, METHOD_SHORT.get(method, method))
    painter.end()

    result = QIcon(pix)
    _icon_cache[key] = result
    return result


def app_icon() -> QIcon:
    key = ("app",)
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached

    result = QIcon()
    for size in APP_ICON_SIZES:
        result.addPixmap(render_app_icon(size))

    _icon_cache[key] = result
    return result


APP_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_app_icon(size: int) -> QPixmap:
    """Paint the app logo (orange rounded square with </>) at the given pixel size."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    gradient.setColorAt(0.0, QColor("#ff8a50"))
    gradient.setColorAt(1.0, QColor("#f0501a"))
    path = QPainterPath()
    radius = size * 0.22
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.fillPath(path, gradient)

    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(max(1.5, size * 0.085))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    s = size
    # "<" and ">" chevrons with a slash in the middle.
    painter.drawPolyline(QPointF(s * 0.34, s * 0.32), QPointF(s * 0.18, s * 0.5), QPointF(s * 0.34, s * 0.68))
    painter.drawPolyline(QPointF(s * 0.66, s * 0.32), QPointF(s * 0.82, s * 0.5), QPointF(s * 0.66, s * 0.68))
    painter.drawLine(QPointF(s * 0.56, s * 0.26), QPointF(s * 0.44, s * 0.74))
    painter.end()
    return pix


# ---------------------------------------------------------------------------
# Stylesheet assets (chevrons / check marks rendered to PNG for QSS usage)
# ---------------------------------------------------------------------------

def _asset_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "apiclient_theme_v2"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _render_asset(name: str, draw, base: int, col: QColor) -> str:
    folder = _asset_dir()
    stem = f"{name}_{col.name().lstrip('#')}"
    path_1x = folder / f"{stem}.png"
    path_2x = folder / f"{stem}@2x.png"
    for path, scale in ((path_1x, 1), (path_2x, 2)):
        if path.exists():
            continue
        size = base * scale
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(col)
        pen.setWidthF(1.4 * scale)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        draw(painter, float(size))
        painter.end()
        pix.save(str(path), "PNG")
    return path_1x.as_posix()


def _chevron_down(p: QPainter, s: float) -> None:
    p.drawPolyline(QPointF(s * 0.25, s * 0.38), QPointF(s * 0.5, s * 0.63), QPointF(s * 0.75, s * 0.38))


def _chevron_up(p: QPainter, s: float) -> None:
    p.drawPolyline(QPointF(s * 0.25, s * 0.62), QPointF(s * 0.5, s * 0.37), QPointF(s * 0.75, s * 0.62))


def _chevron_right(p: QPainter, s: float) -> None:
    p.drawPolyline(QPointF(s * 0.38, s * 0.25), QPointF(s * 0.63, s * 0.5), QPointF(s * 0.38, s * 0.75))


def _check(p: QPainter, s: float) -> None:
    pen = p.pen()
    pen.setWidthF(pen.widthF() * 1.3)
    p.setPen(pen)
    p.drawPolyline(QPointF(s * 0.22, s * 0.52), QPointF(s * 0.42, s * 0.72), QPointF(s * 0.78, s * 0.3))


def build_stylesheet() -> str:
    p = dict(PALETTES[mode()])
    muted = QColor(p["muted"])
    p["chev_down"] = _render_asset("chev_down", _chevron_down, 12, muted)
    p["chev_up"] = _render_asset("chev_up", _chevron_up, 12, muted)
    p["chev_right"] = _render_asset("chev_right", _chevron_right, 12, muted)
    p["check"] = _render_asset("check", _check, 14, QColor("#ffffff"))
    p["font"] = ui_font_family()
    p["mono"] = mono_font_family()
    p["mono_pt"] = manager().editor_font_size

    return """
    QWidget {{ color: {text}; font-family: "{font}"; font-size: 13px; }}
    QMainWindow, QDialog {{ background: {bg}; }}
    QWidget#appRoot, QWidget#workspace, QStackedWidget#workspaceStack {{ background: {bg}; }}
    QLabel {{ background: transparent; }}
    QLabel#appTitle {{ font-size: 14px; font-weight: 600; }}
    QLabel#muted {{ color: {muted}; }}
    QLabel#faint {{ color: {faint}; }}
    QLabel#sectionTitle {{ font-size: 11px; font-weight: 700; color: {muted}; letter-spacing: 0.5px; }}
    QLabel#h1 {{ font-size: 22px; font-weight: 600; }}
    QLabel#h2 {{ font-size: 15px; font-weight: 600; }}
    QLabel#breadcrumb {{ color: {muted}; }}
    QLabel#warningNote {{ color: {warning}; }}

    QToolTip {{ background: {tooltip_bg}; color: {tooltip_fg}; border: 1px solid {border_strong}; padding: 6px 8px; border-radius: 4px; }}

    QMenuBar {{ background: {surface}; border-bottom: 1px solid {border}; padding: 2px 4px; }}
    QMenuBar::item {{ background: transparent; padding: 4px 10px; border-radius: 4px; }}
    QMenuBar::item:selected {{ background: {hover}; }}
    QMenu {{ background: {menu_bg}; border: 1px solid {border_strong}; border-radius: 6px; padding: 4px; }}
    QMenu::item {{ padding: 6px 28px 6px 10px; border-radius: 4px; background: transparent; }}
    QMenu::item:selected {{ background: {hover}; }}
    QMenu::item:disabled {{ color: {faint}; }}
    QMenu::icon {{ padding-left: 8px; }}
    QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}

    QPushButton {{ background: {btn_bg}; border: 1px solid {border_strong}; border-radius: 4px; padding: 6px 14px; min-height: 18px; }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton:pressed {{ background: {selected}; }}
    QPushButton:disabled {{ color: {faint}; border-color: {border}; }}
    QPushButton#primaryButton {{ background: {primary}; border: 1px solid {primary}; color: #ffffff; font-weight: 600; padding: 6px 20px; }}
    QPushButton#primaryButton:hover {{ background: {primary_hover}; border-color: {primary_hover}; }}
    QPushButton#primaryButton:disabled {{ background: {selected}; border-color: {selected}; color: {faint}; }}
    QPushButton#accentButton {{ background: {accent}; border: 1px solid {accent}; color: #ffffff; font-weight: 600; }}
    QPushButton#accentButton:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
    QPushButton#dangerButton {{ background: transparent; border: 1px solid {danger}; color: {danger}; }}
    QPushButton#dangerButton:hover {{ background: {hover}; }}
    QPushButton#ghostButton {{ background: transparent; border: 1px solid transparent; }}
    QPushButton#ghostButton:hover {{ background: {hover}; }}
    QPushButton#linkButton {{ background: transparent; border: none; color: {link}; padding: 2px 4px; }}
    QPushButton#linkButton:hover {{ text-decoration: underline; }}
    QPushButton::menu-indicator {{ image: url({chev_down}); subcontrol-origin: padding; subcontrol-position: center right; right: 8px; width: 10px; height: 10px; }}
    QPushButton#splitButton {{ padding-right: 26px; }}

    QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 4px; padding: 4px; }}
    QToolButton:hover {{ background: {hover}; }}
    QToolButton:pressed, QToolButton:checked {{ background: {selected}; }}
    QToolButton::menu-indicator {{ image: none; width: 0px; }}
    QToolButton#railButton {{ border-radius: 6px; padding: 6px 2px 4px 2px; color: {muted}; font-size: 11px; }}
    QToolButton#railButton:hover {{ color: {text}; }}
    QToolButton#railButton:checked {{ background: {selected}; color: {text}; }}
    QToolButton#segButton {{ border-radius: 4px; padding: 3px 10px; color: {muted}; }}
    QToolButton#segButton:checked {{ background: {selected}; color: {text}; font-weight: 600; }}
    QToolButton#textButton {{ padding: 4px 8px; color: {muted}; }}
    QToolButton#textButton:hover {{ color: {text}; }}
    QToolButton#tabClose {{ padding: 0px; border-radius: 3px; }}
    QToolButton#saveButton {{ background: {btn_bg}; border: 1px solid {border_strong}; padding: 4px 24px 4px 8px; }}
    QToolButton#saveButton:hover {{ background: {hover}; }}
    QToolButton#saveButton::menu-button {{ border: none; border-left: 1px solid {border_strong}; width: 18px; }}
    QToolButton#saveButton::menu-arrow {{ image: url({chev_down}); width: 10px; height: 10px; }}

    QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QAbstractSpinBox {{
        background: {input}; border: 1px solid {border_strong}; border-radius: 4px;
        selection-background-color: {selection}; selection-color: {selection_fg};
    }}
    QLineEdit {{ padding: 5px 8px; min-height: 18px; }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QAbstractSpinBox:focus {{ border-color: {focus}; }}
    QLineEdit:disabled, QAbstractSpinBox:disabled {{ color: {faint}; background: {surface}; }}
    QLineEdit#titleEdit {{ background: transparent; border: 1px solid transparent; font-size: 14px; font-weight: 600; padding: 3px 6px; }}
    QLineEdit#titleEdit:hover {{ border-color: {border_strong}; }}
    QLineEdit#titleEdit:focus {{ border-color: {focus}; background: {input}; }}
    QLineEdit#cellEditor {{ border: 1px solid {focus}; border-radius: 0px; padding: 0px 6px; background: {input}; }}
    QAbstractSpinBox {{ padding: 4px 6px; }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; background: transparent; width: 16px; }}
    QAbstractSpinBox::up-arrow {{ image: url({chev_up}); width: 10px; height: 10px; }}
    QAbstractSpinBox::down-arrow {{ image: url({chev_down}); width: 10px; height: 10px; }}

    QFrame#editorFrame {{ background: {code_bg}; border: 1px solid {border}; border-radius: 4px; }}
    QPlainTextEdit#codeEditor {{ background: {code_bg}; border: none; border-radius: 0px; font-family: "{mono}"; font-size: {mono_pt}pt; }}
    QPlainTextEdit#bulkEditor {{ font-family: "{mono}"; font-size: {mono_pt}pt; }}

    QComboBox {{ background: {btn_bg}; border: 1px solid {border_strong}; border-radius: 4px; padding: 5px 26px 5px 8px; min-height: 18px; }}
    QComboBox:hover {{ background: {hover}; }}
    QComboBox:focus {{ border-color: {focus}; }}
    QComboBox:disabled {{ color: {faint}; }}
    QComboBox::drop-down {{ border: none; width: 22px; subcontrol-origin: padding; subcontrol-position: center right; }}
    QComboBox::down-arrow {{ image: url({chev_down}); width: 10px; height: 10px; }}
    QComboBox QAbstractItemView {{ background: {menu_bg}; border: 1px solid {border_strong}; selection-background-color: {hover}; selection-color: {text}; outline: 0px; padding: 4px; }}
    QComboBox#methodCombo {{ border: none; background: transparent; font-weight: 700; padding-left: 10px; }}
    QComboBox#methodCombo:hover {{ background: {hover}; }}
    QComboBox#flatCombo {{ background: transparent; border: 1px solid transparent; }}
    QComboBox#flatCombo:hover {{ background: {hover}; }}
    QComboBox#cellCombo {{ border: none; border-radius: 0px; background: transparent; padding: 0px 20px 0px 6px; min-height: 0px; }}
    QComboBox#envCombo {{ min-width: 190px; }}

    QFrame#urlBar {{ background: {input}; border: 1px solid {border_strong}; border-radius: 4px; }}
    QFrame#urlBar[focused="true"] {{ border-color: {focus}; }}
    QFrame#vDivider {{ background: {border_strong}; border: none; }}
    QPlainTextEdit#urlEdit {{ background: transparent; border: none; padding: 0px; }}

    QCheckBox, QRadioButton {{ spacing: 7px; background: transparent; }}
    QCheckBox::indicator, QTableView::indicator, QTreeView::indicator, QListView::indicator {{
        width: 14px; height: 14px; border: 1px solid {border_strong}; border-radius: 3px; background: {input};
    }}
    QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {focus}; }}
    QCheckBox::indicator:checked, QTableView::indicator:checked, QTreeView::indicator:checked, QListView::indicator:checked {{
        background: {accent}; border-color: {accent}; image: url({check});
    }}
    QCheckBox::indicator:disabled {{ background: {surface}; border-color: {border}; }}
    QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {border_strong}; border-radius: 8px; background: {input}; }}
    QRadioButton::indicator:hover {{ border-color: {focus}; }}
    QRadioButton::indicator:checked {{ border: 4px solid {accent}; width: 8px; height: 8px; background: #ffffff; }}

    QTabWidget::pane {{ border: none; background: transparent; }}
    QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
    QTabBar::tab {{ background: transparent; color: {muted}; padding: 8px 12px; border: none; border-bottom: 2px solid transparent; margin-right: 4px; }}
    QTabBar::tab:hover {{ color: {text}; }}
    QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {accent}; }}
    QTabWidget#requestTabs > QTabBar {{ background: {surface}; }}
    QTabWidget#requestTabs > QTabBar::tab {{
        background: {surface}; color: {muted}; padding: 9px 6px 9px 10px; min-width: 110px; max-width: 240px;
        border: none; border-right: 1px solid {border}; border-top: 2px solid transparent; border-bottom: 1px solid {border}; margin: 0px;
    }}
    QTabWidget#requestTabs > QTabBar::tab:selected {{ background: {bg}; color: {text}; border-top: 2px solid {accent}; border-bottom: 1px solid {bg}; }}
    QTabWidget#requestTabs > QTabBar::tab:hover:!selected {{ background: {hover}; }}
    QTabBar QToolButton {{ background: {surface}; border: none; border-radius: 0px; }}
    QTabBar::scroller {{ width: 26px; }}

    QHeaderView {{ background: transparent; border: none; }}
    QHeaderView::section {{ background: {surface}; color: {muted}; font-weight: 600; padding: 6px 8px; border: none; border-right: 1px solid {border}; border-bottom: 1px solid {border}; }}
    QTableView, QTableWidget {{
        background: {input}; border: 1px solid {border}; border-radius: 4px; gridline-color: {border};
        selection-background-color: {row_selected}; selection-color: {text}; outline: 0px;
    }}
    QTableView::item {{ border: none; padding: 0px 6px; }}
    QTableView::item:selected {{ background: {row_selected}; color: {text}; }}
    QTableCornerButton::section {{ background: {surface}; border: none; }}

    QTreeView, QTreeWidget, QListView, QListWidget {{ background: transparent; border: none; outline: 0px; show-decoration-selected: 1; }}
    QTreeView::item, QListView::item {{ padding: 4px 2px; border: none; }}
    QTreeView::item:hover, QListView::item:hover {{ background: {hover}; }}
    QTreeView::item:selected, QListView::item:selected {{ background: {selected}; color: {text}; }}
    QTreeView::branch {{ background: transparent; }}
    QTreeView::branch:hover {{ background: {hover}; }}
    QTreeView::branch:selected {{ background: {selected}; }}
    QTreeView::branch:has-children:!has-siblings:closed, QTreeView::branch:closed:has-children:has-siblings {{ image: url({chev_right}); border-image: none; }}
    QTreeView::branch:open:has-children:!has-siblings, QTreeView::branch:open:has-children:has-siblings {{ image: url({chev_down}); border-image: none; }}
    QTreeWidget#dataTree, QTreeWidget#consoleTree, QTreeWidget#resultsTree {{ background: {input}; border: 1px solid {border}; border-radius: 4px; }}

    QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0px; }}
    QScrollBar::handle:vertical {{ background: {scroll}; border-radius: 4px; min-height: 30px; margin: 2px 2px 2px 3px; }}
    QScrollBar::handle:vertical:hover {{ background: {scroll_hover}; }}
    QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0px; }}
    QScrollBar::handle:horizontal {{ background: {scroll}; border-radius: 4px; min-width: 30px; margin: 3px 2px 2px 2px; }}
    QScrollBar::handle:horizontal:hover {{ background: {scroll_hover}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; border: none; background: none; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:horizontal {{ width: 5px; border-left: 1px solid {border}; }}
    QSplitter::handle:vertical {{ height: 5px; border-top: 1px solid {border}; }}
    QSplitter::handle:hover {{ border-color: {accent}; }}

    QStatusBar {{ background: {surface}; border-top: 1px solid {border}; color: {muted}; min-height: 26px; }}
    QStatusBar::item {{ border: none; }}
    QStatusBar QLabel {{ color: {muted}; padding: 0px 6px; }}
    QStatusBar QToolButton {{ padding: 2px 8px; color: {muted}; font-size: 12px; }}
    QStatusBar QToolButton:hover {{ color: {text}; }}

    QGroupBox {{ border: 1px solid {border}; border-radius: 6px; margin-top: 16px; padding: 14px 12px 12px 12px; background: transparent; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0px 4px; color: {muted}; font-weight: 600; }}

    QFrame#topBar {{ background: {surface}; border-bottom: 1px solid {border}; }}
    QFrame#rail {{ background: {rail}; border-right: 1px solid {border}; }}
    QFrame#sidebar {{ background: {sidebar}; }}
    QFrame#panelHeader {{ background: transparent; }}
    QFrame#card {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; }}
    QFrame#themeCard {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; }}
    QFrame#themeCard[selected="true"] {{ border: 2px solid {accent}; }}
    QFrame#hline {{ background: {border}; border: none; max-height: 1px; min-height: 1px; }}
    QFrame#responseHeader {{ background: transparent; }}
    QFrame#searchBar {{ background: {surface}; border-bottom: 1px solid {border}; }}
    QFrame#consolePanel {{ background: {surface}; }}
    QFrame#dialogFooter {{ background: {surface}; border-top: 1px solid {border}; }}
    QFrame#infoBanner {{ background: {surface}; border: 1px solid {border}; border-radius: 6px; }}
    QFrame#quickLook {{ background: {menu_bg}; border: 1px solid {border_strong}; border-radius: 8px; }}
    QLabel#statusPill {{ border-radius: 4px; padding: 2px 8px; font-weight: 600; }}

    QListWidget#settingsNav {{ background: {surface}; border: none; border-right: 1px solid {border}; padding: 10px 8px; }}
    QListWidget#settingsNav::item {{ padding: 8px 12px; border-radius: 4px; margin: 1px 0px; }}
    QListWidget#settingsNav::item:selected {{ background: {selected}; color: {text}; font-weight: 600; }}

    QTextBrowser#markdownView {{ padding: 8px; }}
    QMessageBox, QInputDialog {{ background: {bg}; }}
    QProgressBar {{ background: {selected}; border: none; border-radius: 2px; max-height: 4px; text-align: center; }}
    QProgressBar::chunk {{ background: {accent}; border-radius: 2px; }}
    """.format(**p)


def build_palette() -> QPalette:
    p = PALETTES[mode()]
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(p["bg"]))
    pal.setColor(QPalette.WindowText, QColor(p["text"]))
    pal.setColor(QPalette.Base, QColor(p["input"]))
    pal.setColor(QPalette.AlternateBase, QColor(p["surface"]))
    pal.setColor(QPalette.Text, QColor(p["text"]))
    pal.setColor(QPalette.Button, QColor(p["btn_bg"]))
    pal.setColor(QPalette.ButtonText, QColor(p["text"]))
    pal.setColor(QPalette.Highlight, QColor(p["selection"]))
    pal.setColor(QPalette.HighlightedText, QColor(p["selection_fg"]))
    pal.setColor(QPalette.ToolTipBase, QColor(p["tooltip_bg"]))
    pal.setColor(QPalette.ToolTipText, QColor(p["tooltip_fg"]))
    pal.setColor(QPalette.Link, QColor(p["link"]))
    pal.setColor(QPalette.PlaceholderText, QColor(p["faint"]))
    pal.setColor(QPalette.Light, QColor(p["border_strong"]))
    pal.setColor(QPalette.Mid, QColor(p["border"]))
    pal.setColor(QPalette.Dark, QColor(p["border"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(p["faint"]))
    return pal


def apply_app_theme(theme_mode: str, editor_font_size: Optional[int] = None) -> None:
    mgr = manager()
    mgr.mode = "light" if str(theme_mode).lower() == "light" else "dark"
    if editor_font_size:
        mgr.editor_font_size = int(editor_font_size)
    app = QApplication.instance()
    if app is not None:
        app.setPalette(build_palette())
        app.setStyleSheet(build_stylesheet())
        # Qt does not always repolish hidden scroll-area viewports; force it so every editor switches.
        from PyQt5.QtWidgets import QAbstractScrollArea

        for widget in app.allWidgets():
            if isinstance(widget, QAbstractScrollArea):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.viewport().update()
        for widget in app.topLevelWidgets():
            if widget.isWindow() and widget.isVisible():
                apply_native_titlebar(widget)
    mgr.changed.emit(mgr.mode)


def apply_native_titlebar(widget: QWidget) -> None:
    """Match the Windows 10/11 title bar to the active theme."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1 if is_dark() else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old builds)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark)) == 0:
                break
        caption = QColor(color("surface"))
        colorref = ctypes.c_int(caption.red() | (caption.green() << 8) | (caption.blue() << 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref), ctypes.sizeof(colorref))  # DWMWA_CAPTION_COLOR
        text = QColor(color("text"))
        textref = ctypes.c_int(text.red() | (text.green() << 8) | (text.blue() << 16))
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(textref), ctypes.sizeof(textref))  # DWMWA_TEXT_COLOR
    except Exception:
        return
