"""Code editor with line numbers, syntax highlighting and an inline find bar."""
import json
import re
from typing import List, Optional, Tuple

from PyQt5.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QShortcut,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .theme import G

HIGHLIGHT_LIMIT = 1_500_000  # characters; larger documents are shown without highlighting


def _fmt(color_key: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(theme.qcolor(color_key))
    if bold:
        fmt.setFontWeight(QFont.Bold)
    if italic:
        fmt.setFontItalic(True)
    return fmt


_JS_KEYWORDS = (
    r"\b(?:const|let|var|function|return|if|else|for|while|new|await|async|try|catch|throw|class|import|from|export|"
    r"true|false|null|undefined|this|typeof|in|of|package|func|using|public|private|static|void|string|int|bool)\b"
)
_PY_KEYWORDS = (
    r"\b(?:def|return|if|elif|else|for|while|in|not|and|or|is|None|True|False|import|from|as|try|except|finally|"
    r"raise|with|lambda|class|pass|break|continue|global|yield|assert)\b"
)


class CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument, language: str = "json") -> None:
        super().__init__(document)
        self.language = language
        self.rules: List[Tuple["re.Pattern", QTextCharFormat, int]] = []
        self.rebuild()

    def set_language(self, language: str) -> None:
        self.language = language
        self.rebuild()

    def rebuild(self) -> None:
        lang = self.language
        rules: List[Tuple[str, QTextCharFormat, int]] = []
        if lang == "json":
            rules += [
                (r"[{}\[\]:,]", _fmt("code_punct"), 0),
                (r"\b-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\b", _fmt("code_number"), 0),
                (r"\b(?:true|false|null)\b", _fmt("code_keyword"), 0),
                (r'"(?:\\.|[^"\\])*"', _fmt("code_string"), 0),
                (r'("(?:\\.|[^"\\])*")\s*:', _fmt("code_key"), 1),
            ]
        elif lang in ("xml", "html"):
            rules += [
                (r"</?[\w:\-.]+|/?>", _fmt("code_tag"), 0),
                (r"\b[\w:\-]+(?==)", _fmt("code_attr"), 0),
                (r'"[^"]*"|\'[^\']*\'', _fmt("code_string"), 0),
                (r"<!--.*?-->", _fmt("code_comment", italic=True), 0),
            ]
        elif lang in ("javascript", "graphql"):
            rules += [
                (r"\b\d+(?:\.\d+)?\b", _fmt("code_number"), 0),
                (_JS_KEYWORDS if lang == "javascript" else r"\b(?:query|mutation|subscription|fragment|on|true|false|null)\b", _fmt("code_keyword"), 0),
                (r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`[^`]*`', _fmt("code_string"), 0),
                (r"//.*$|#.*$" if lang == "graphql" else r"//.*$", _fmt("code_comment", italic=True), 0),
            ]
        elif lang == "python":
            rules += [
                (r"\b\d+(?:\.\d+)?\b", _fmt("code_number"), 0),
                (_PY_KEYWORDS, _fmt("code_keyword"), 0),
                (r"\b(?:pm|console|json|re|time|datetime|uuid|base64|hashlib|hmac|random)\b", _fmt("code_builtin"), 0),
                (r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', _fmt("code_string"), 0),
                (r"#.*$", _fmt("code_comment", italic=True), 0),
            ]
        elif lang == "http":
            rules += [
                (r"^[\w\-]+(?=:)", _fmt("code_key"), 0),
                (r"^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE)\b", _fmt("code_keyword", bold=True), 0),
                (r"^(?:▸|▾).*$|^[A-Z][A-Za-z ]+:$", _fmt("muted", bold=True), 0),
            ]
        rules.append((r"\{\{\s*[^{}\s]+?\s*\}\}", _fmt("var_ok"), 0))
        self.rules = [(re.compile(pattern), fmt, group) for pattern, fmt, group in rules]
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        if len(text) > 20000:
            return
        for pattern, fmt, group in self.rules:
            for match in pattern.finditer(text):
                start, end = match.span(group)
                if end > start:
                    self.setFormat(start, end - start, fmt)


class _LineNumberArea(QWidget):
    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_number_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent: Optional[QWidget] = None, language: str = "json", read_only: bool = False, line_numbers: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("codeEditor")
        self.setFrameShape(QFrame.NoFrame)
        self.setReadOnly(read_only)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabChangesFocus(False)
        self._language = language
        self._show_numbers = line_numbers
        self._number_area = _LineNumberArea(self)
        self._highlighter: Optional[CodeHighlighter] = CodeHighlighter(self.document(), language)
        self._search_selections: List[QTextEdit.ExtraSelection] = []

        self.blockCountChanged.connect(self._update_margins)
        self.updateRequest.connect(self._update_number_area)
        self.cursorPositionChanged.connect(self._refresh_extra_selections)
        theme.manager().changed.connect(self.refresh_theme)
        self.refresh_theme()

    # -- appearance ---------------------------------------------------------
    def refresh_theme(self, *_args) -> None:
        font = theme.mono_font()
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 2)
        if self._highlighter is not None:
            self._highlighter.rebuild()
        self._update_margins()
        self._refresh_extra_selections()
        self._number_area.update()

    def set_language(self, language: str) -> None:
        self._language = language
        if self._highlighter is not None:
            self._highlighter.set_language(language)

    def language(self) -> str:
        return self._language

    def set_wrap(self, wrap: bool) -> None:
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth if wrap else QPlainTextEdit.NoWrap)

    def set_text_fast(self, text: str) -> None:
        """Set text, disabling highlighting for very large payloads."""
        if len(text) > HIGHLIGHT_LIMIT:
            if self._highlighter is not None:
                self._highlighter.setDocument(None)
                self._highlighter = None
        elif self._highlighter is None:
            self._highlighter = CodeHighlighter(self.document(), self._language)
        self.setPlainText(text)

    # -- line numbers ---------------------------------------------------------
    def line_number_width(self) -> int:
        if not self._show_numbers:
            return 0
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margins(self, *_args) -> None:
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _update_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._number_area.scroll(0, dy)
        else:
            self._number_area.update(0, rect.y(), self._number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margins()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_width(), cr.height()))

    def paint_line_numbers(self, event) -> None:
        if not self._show_numbers:
            return
        painter = QPainter(self._number_area)
        painter.fillRect(event.rect(), theme.qcolor("code_bg"))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        current = self.textCursor().blockNumber()
        height = self.fontMetrics().height()
        painter.setFont(self.font())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(theme.qcolor("text" if number == current else "line_no"))
                painter.drawText(0, top, self._number_area.width() - 8, height, Qt.AlignRight, str(number + 1))
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            number += 1

    # -- selections -------------------------------------------------------------
    def set_search_selections(self, selections: List[QTextEdit.ExtraSelection]) -> None:
        self._search_selections = selections
        self._refresh_extra_selections()

    def _refresh_extra_selections(self) -> None:
        extras: List[QTextEdit.ExtraSelection] = []
        if not self.isReadOnly() or self.hasFocus():
            line = QTextEdit.ExtraSelection()
            line.format.setBackground(theme.qcolor("current_line"))
            line.format.setProperty(QTextFormat.FullWidthSelection, True)
            line.cursor = self.textCursor()
            line.cursor.clearSelection()
            extras.append(line)
        extras.extend(self._search_selections)
        self.setExtraSelections(extras)

    # -- editing helpers --------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        if self.isReadOnly():
            super().keyPressEvent(event)
            return
        key = event.key()
        cursor = self.textCursor()
        if key == Qt.Key_Tab and not event.modifiers():
            if cursor.hasSelection():
                self._indent_selection(cursor, dedent=False)
            else:
                cursor.insertText("  ")
            return
        if key == Qt.Key_Backtab:
            self._indent_selection(cursor, dedent=True)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            line = cursor.block().text()[: cursor.positionInBlock()]
            indent = re.match(r"[ \t]*", line).group(0)
            stripped = line.rstrip()
            opener = stripped[-1:] in ("{", "[", "(") or (self._language == "python" and stripped.endswith(":"))
            next_char = self.document().characterAt(cursor.position())
            if opener and next_char in "}])":
                cursor.insertText("\n" + indent + "  " + "\n" + indent)
                cursor.movePosition(QTextCursor.Up)
                cursor.movePosition(QTextCursor.EndOfLine)
                self.setTextCursor(cursor)
            else:
                cursor.insertText("\n" + indent + ("  " if opener else ""))
            self.ensureCursorVisible()
            return
        pairs = {"{": "}", "[": "]", '"': '"'}
        text = event.text()
        if text in pairs and not cursor.hasSelection() and self._language in ("json", "javascript", "graphql"):
            nxt = self.document().characterAt(cursor.position())
            if text == '"' and nxt == '"':
                cursor.movePosition(QTextCursor.Right)
                self.setTextCursor(cursor)
                return
            if not nxt.strip() or nxt in "}],":
                cursor.insertText(text + pairs[text])
                cursor.movePosition(QTextCursor.Left)
                self.setTextCursor(cursor)
                return
        if text in ("}", "]") and self.document().characterAt(cursor.position()) == text:
            cursor.movePosition(QTextCursor.Right)
            self.setTextCursor(cursor)
            return
        super().keyPressEvent(event)

    def _indent_selection(self, cursor: QTextCursor, dedent: bool) -> None:
        start, end = sorted((cursor.selectionStart(), cursor.selectionEnd()))
        cursor.beginEditBlock()
        block = self.document().findBlock(start)
        last = self.document().findBlock(max(start, end - 1))
        while block.isValid():
            c = QTextCursor(block)
            if dedent:
                txt = block.text()
                remove = len(txt) - len(txt.lstrip(" ")) if txt.startswith(" ") else 0
                for _ in range(min(2, remove)):
                    c.deleteChar()
            else:
                c.insertText("  ")
            if block == last:
                break
            block = block.next()
        cursor.endEditBlock()

    def format_json(self) -> Tuple[bool, str]:
        raw = self.toPlainText().strip()
        if not raw:
            return True, ""
        placeholders = {}

        def protect(match: "re.Match") -> str:
            token = f"\"__VAR_{len(placeholders)}__\""
            placeholders[token] = match.group(0)
            return token

        # Allow bare {{var}} values (e.g. "id": {{userId}}) while formatting.
        protected = re.sub(r"(?<![\"\w])\{\{\s*[^{}\s]+?\s*\}\}(?![\"\w])", protect, raw)
        try:
            parsed = json.loads(protected)
        except json.JSONDecodeError as exc:
            return False, str(exc)
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
        for token, original in placeholders.items():
            pretty = pretty.replace(token, original)
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.Document)
        cursor.insertText(pretty)
        cursor.endEditBlock()
        return True, ""

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._refresh_extra_selections()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._refresh_extra_selections()


class SearchBar(QFrame):
    closed = pyqtSignal()

    def __init__(self, editor: CodeEditor, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchBar")
        self.editor = editor
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 6, 4)
        layout.setSpacing(4)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Find")
        self.input.setClearButtonEnabled(True)
        self.input.addAction(theme.icon(G.SEARCH, "faint", 14), QLineEdit.LeadingPosition)
        self.input.textChanged.connect(self._search)
        self.input.returnPressed.connect(self.find_next)
        self.count = QLabel("")
        self.count.setObjectName("muted")
        self.count.setMinimumWidth(70)
        self.case_btn = QToolButton()
        self.case_btn.setText("Aa")
        self.case_btn.setCheckable(True)
        self.case_btn.setToolTip("Match case")
        self.case_btn.toggled.connect(lambda _c: self._search(self.input.text()))
        self.prev_btn = QToolButton()
        self.prev_btn.setIcon(theme.icon("\uE70E", "muted", 12))
        self.prev_btn.setToolTip("Previous match (Shift+Enter)")
        self.prev_btn.clicked.connect(self.find_prev)
        self.next_btn = QToolButton()
        self.next_btn.setIcon(theme.icon(G.CHEVRON_DOWN, "muted", 12))
        self.next_btn.setToolTip("Next match (Enter)")
        self.next_btn.clicked.connect(self.find_next)
        close_btn = QToolButton()
        close_btn.setIcon(theme.icon(G.CLOSE, "muted", 12))
        close_btn.clicked.connect(self.close_bar)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.count)
        layout.addWidget(self.case_btn)
        layout.addWidget(self.prev_btn)
        layout.addWidget(self.next_btn)
        layout.addWidget(close_btn)
        QShortcut(QKeySequence("Escape"), self, activated=self.close_bar, context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Shift+Return"), self.input, activated=self.find_prev, context=Qt.WidgetShortcut)
        self._matches: List[Tuple[int, int]] = []
        self._current = -1

    def open_bar(self) -> None:
        self.show()
        selected = self.editor.textCursor().selectedText()
        if selected and "\u2029" not in selected:
            self.input.setText(selected)
        self.input.setFocus()
        self.input.selectAll()
        self._search(self.input.text())

    def close_bar(self) -> None:
        self.hide()
        self.editor.set_search_selections([])
        self.editor.setFocus()
        self.closed.emit()

    def _search(self, text: str) -> None:
        self._matches = []
        self._current = -1
        if text:
            haystack = self.editor.toPlainText()
            flags = 0 if self.case_btn.isChecked() else re.IGNORECASE
            for match in re.finditer(re.escape(text), haystack, flags):
                self._matches.append(match.span())
                if len(self._matches) >= 5000:
                    break
        self._paint()
        if self._matches:
            pos = self.editor.textCursor().selectionStart()
            self._current = next((i for i, (s, _) in enumerate(self._matches) if s >= pos), 0)
            self._goto()

    def _paint(self) -> None:
        selections = []
        for idx, (start, end) in enumerate(self._matches[:2000]):
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(theme.qcolor("accent" if idx == self._current else "search_hit"))
            if idx == self._current:
                sel.format.setForeground(QColor("#ffffff"))
            cursor = self.editor.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            sel.cursor = cursor
            selections.append(sel)
        self.editor.set_search_selections(selections)
        if self.input.text():
            total = len(self._matches)
            self.count.setText(f"{self._current + 1 if total else 0} of {total}{'+' if total >= 5000 else ''}")
        else:
            self.count.setText("")

    def _goto(self) -> None:
        if not self._matches:
            return
        start, end = self._matches[self._current]
        cursor = self.editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        self._paint()

    def find_next(self) -> None:
        if self._matches:
            self._current = (self._current + 1) % len(self._matches)
            self._goto()

    def find_prev(self) -> None:
        if self._matches:
            self._current = (self._current - 1) % len(self._matches)
            self._goto()


class EditorWithSearch(QFrame):
    """CodeEditor wrapped in a bordered frame with a Ctrl+F find bar."""

    def __init__(self, parent: Optional[QWidget] = None, language: str = "json", read_only: bool = False, line_numbers: bool = True, framed: bool = True) -> None:
        super().__init__(parent)
        if framed:
            self.setObjectName("editorFrame")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1) if framed else layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.editor = CodeEditor(self, language=language, read_only=read_only, line_numbers=line_numbers)
        self.search = SearchBar(self.editor, self)
        self.search.hide()
        layout.addWidget(self.search)
        layout.addWidget(self.editor, 1)
        QShortcut(QKeySequence.Find, self, activated=self.search.open_bar, context=Qt.WidgetWithChildrenShortcut)

    def open_search(self) -> None:
        self.search.open_bar()
