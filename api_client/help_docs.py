"""In-app documentation viewer: renders the bundled docs/*.md as Markdown."""
import sys
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices, QFont, QTextBlockFormat, QTextCursor
from PyQt5.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QTextBrowser, QWidget

from . import theme
from .dialogs import ThemedDialog


def _markdown_css() -> str:
    c = theme.color
    return f"""
    a {{ color: {c('link')}; }}
    h1, h2, h3, h4, h5 {{ color: {c('text')}; font-weight: 600; }}
    code {{ background-color: {c('code_bg')}; color: {c('code_string')}; }}
    pre {{ background-color: {c('code_bg')}; color: {c('text')}; }}
    blockquote {{ color: {c('muted')}; }}
    table {{ border-color: {c('border')}; }}
    th {{ background-color: {c('surface')}; border: 1px solid {c('border')}; padding: 5px 9px; }}
    td {{ border: 1px solid {c('border')}; padding: 5px 9px; }}
    """


def docs_dir() -> Optional[Path]:
    """Locate the docs/ folder when running from source or from a bundled EXE."""
    candidates: List[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "docs")
        candidates.append(Path(sys.executable).resolve().parent / "docs")
    candidates.append(Path(__file__).resolve().parent.parent / "docs")
    for path in candidates:
        if path.is_dir():
            return path
    return None


def _nav_sort_key(path: Path):
    return (0, "") if path.name.lower() == "readme.md" else (1, path.name.lower())


def _title(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
    except Exception:  # noqa: BLE001
        pass
    return path.stem


class DocumentationDialog(ThemedDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, "Documentation", 1060, 720)
        self.setWindowModality(False)
        # Allow maximize / restore (and minimize) from the title bar, next to Close.
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint | Qt.WindowMinimizeButtonHint)
        self.dir = docs_dir()
        self.files: List[Path] = []
        self._current: Optional[Path] = None

        row = QHBoxLayout()
        row.setSpacing(12)
        self.nav = QListWidget()
        self.nav.setObjectName("settingsNav")
        self.nav.setFixedWidth(240)
        self.nav.currentRowChanged.connect(self._on_nav)
        row.addWidget(self.nav)

        self.view = QTextBrowser()
        self.view.setObjectName("markdownView")
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.anchorClicked.connect(self._on_anchor)
        doc = self.view.document()
        doc.setDefaultStyleSheet(_markdown_css())
        doc.setDefaultFont(QFont(theme.ui_font_family(), 11))
        doc.setDocumentMargin(28)
        row.addWidget(self.view, 1)
        self.body_layout.addLayout(row, 1)
        self.add_button("Close", self.accept, primary=True)
        self._populate()

    def _populate(self) -> None:
        if self.dir is None:
            self.nav.hide()
            self.view.setMarkdown("# Documentation\n\nThe `docs/` folder could not be found next to the app.")
            return
        self.files = sorted(self.dir.glob("*.md"), key=_nav_sort_key)
        for path in self.files:
            self.nav.addItem(QListWidgetItem(_title(path)))
        if self.files:
            self.nav.setCurrentRow(0)
        else:
            self.view.setMarkdown("# Documentation\n\nNo Markdown files found in the docs folder.")

    def _on_nav(self, index: int) -> None:
        if 0 <= index < len(self.files):
            self._load(self.files[index])

    def _load(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            text = f"# Error\n\nCould not open `{path.name}`:\n\n`{exc}`"
        self._current = path
        search = [str(path.parent)]
        if self.dir and str(self.dir) not in search:
            search.append(str(self.dir))
        self.view.setSearchPaths(search)
        self.view.setMarkdown(text)
        self._apply_typography()
        self.view.verticalScrollBar().setValue(0)

    def _apply_typography(self) -> None:
        """Qt's Markdown renderer packs lines tightly; give it VSCode-like spacing."""
        doc = self.view.document()
        block = doc.firstBlock()
        while block.isValid():
            cursor = QTextCursor(block)
            fmt = block.blockFormat()
            fmt.setLineHeight(150, QTextBlockFormat.ProportionalHeight)
            level = fmt.headingLevel()
            if level == 1:
                fmt.setTopMargin(20.0)
                fmt.setBottomMargin(10.0)
            elif level == 2:
                fmt.setTopMargin(18.0)
                fmt.setBottomMargin(8.0)
            elif level >= 3:
                fmt.setTopMargin(14.0)
                fmt.setBottomMargin(6.0)
            else:
                fmt.setTopMargin(6.0)
                fmt.setBottomMargin(6.0)
            cursor.setBlockFormat(fmt)
            block = block.next()

    def _on_anchor(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        target_str = url.toString().split("#", 1)[0]
        fragment = url.fragment()
        if not target_str:
            if fragment:
                self.view.scrollToAnchor(fragment)
            return
        base = self._current.parent if self._current else (self.dir or Path.cwd())
        target = (base / target_str).resolve()
        if target.suffix.lower() == ".md" and target.exists():
            selected = next((i for i, f in enumerate(self.files) if f.resolve() == target), -1)
            if selected >= 0:
                self.nav.setCurrentRow(selected)  # triggers _load
            else:
                self._load(target)
            if fragment:
                self.view.scrollToAnchor(fragment)
        elif target.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
