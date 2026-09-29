"""Collection Runner: run every request in a collection or folder, with iterations and tests."""
import time
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .config_store import AppSettings
from .dialogs import ThemedDialog
from .engine import RunResult, VariableContext, new_session, run_request
from .models import AuthConfig, RequestModel
from .theme import G
from .widgets import format_ms

if TYPE_CHECKING:  # pragma: no cover
    from .main_window import MainWindow


class RunnerWorker(QThread):
    progress = pyqtSignal(int, int, object)  # iteration, index, RunResult
    finished_all = pyqtSignal(object)  # variable ops

    def __init__(
        self,
        items: Sequence[Tuple[RequestModel, List[AuthConfig]]],
        ctx: VariableContext,
        settings: AppSettings,
        iterations: int,
        delay_ms: int,
    ) -> None:
        super().__init__()
        self.items = list(items)
        self.ctx = ctx
        self.settings = settings
        self.iterations = iterations
        self.delay_ms = delay_ms
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def run(self) -> None:
        session = new_session(self.settings)
        try:
            for iteration in range(self.iterations):
                for index, (model, inherited) in enumerate(self.items):
                    if self._stopped:
                        break
                    try:
                        result = run_request(
                            model,
                            self.ctx,
                            self.settings,
                            inherited,
                            session=session,
                            cancelled=lambda: self._stopped,
                            info={"iteration": iteration, "requestName": model.name},
                        )
                    except Exception as exc:  # noqa: BLE001
                        result = RunResult(error=f"{type(exc).__name__}: {exc}", method=model.method, url=model.url)
                    self.progress.emit(iteration, index, result)
                    waited = 0
                    while waited < self.delay_ms and not self._stopped:
                        time.sleep(0.05)
                        waited += 50
                if self._stopped:
                    break
        finally:
            session.close()
        self.finished_all.emit(list(self.ctx.ops))


class RunnerDialog(ThemedDialog):
    def __init__(self, app: "MainWindow", collection_id: str, folder_id: Optional[str] = None) -> None:
        coll = app.find_collection(collection_id)
        title = coll.name if coll else "Collection"
        self.items = app.runner_items(collection_id, folder_id)
        if folder_id:
            node = app.find_node(collection_id, folder_id)
            if node is not None:
                title = f"{title} / {node.name}"
        super().__init__(app, f"Runner · {title}", 1100, 700)
        self.setWindowModality(Qt.NonModal)
        self.app = app
        self.collection_id = collection_id
        self.worker: Optional[RunnerWorker] = None
        self._stats = {"passed": 0, "failed": 0, "requests": 0, "errors": 0, "time": 0.0}
        self._selected: List[Tuple[RequestModel, List[AuthConfig]]] = []

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 12, 0)
        ll.setSpacing(8)
        header = QLabel(title)
        header.setObjectName("h2")
        ll.addWidget(header)
        env = app.find_environment(app.active_env_id) if app.active_env_id else None
        env_label = QLabel(f"Environment: {env.name if env else 'No Environment'}")
        env_label.setObjectName("muted")
        ll.addWidget(env_label)
        sel_row = QHBoxLayout()
        order = QLabel("RUN ORDER")
        order.setObjectName("sectionTitle")
        sel_row.addWidget(order)
        sel_row.addStretch(1)
        for text, state in (("Select all", True), ("Deselect", False)):
            btn = QPushButton(text)
            btn.setObjectName("linkButton")
            btn.clicked.connect(lambda _c=False, s=state: self._select_all(s))
            sel_row.addWidget(btn)
        ll.addLayout(sel_row)
        self.request_list = QListWidget()
        self.request_list.setIconSize(QSize(38, 16))
        self.request_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.request_list.setTextElideMode(Qt.ElideRight)
        for model, _inherited in self.items:
            item = QListWidgetItem(theme.method_badge_icon(model.method, 38, 16), model.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setToolTip(f"{model.method} {model.url}")
            self.request_list.addItem(item)
        ll.addWidget(self.request_list, 1)

        form = QFormLayout()
        form.setVerticalSpacing(8)
        self.iterations = QSpinBox()
        self.iterations.setRange(1, 1000)
        self.iterations.setValue(1)
        self.delay = QSpinBox()
        self.delay.setRange(0, 60000)
        self.delay.setSingleStep(100)
        self.delay.setSuffix(" ms")
        self.persist = QCheckBox("Persist variable changes")
        self.persist.setChecked(True)
        self.persist.setToolTip("Save values set by scripts and 'set variable' rules back to your environment/collection/globals.")
        form.addRow("Iterations", self.iterations)
        form.addRow("Delay", self.delay)
        form.addRow("", self.persist)
        ll.addLayout(form)
        self.run_btn = QPushButton(f"Run {title.split(' / ')[-1]}")
        self.run_btn.setObjectName("accentButton")
        self.run_btn.setCursor(Qt.PointingHandCursor)
        self.run_btn.setIcon(theme.icon(G.PLAY, "#ffffff", 14))
        self.run_btn.clicked.connect(self._toggle_run)
        ll.addWidget(self.run_btn)
        splitter.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 0, 0, 0)
        rl.setSpacing(8)
        self.summary = QLabel("Configure the run and press Run.")
        self.summary.setObjectName("h2")
        rl.addWidget(self.summary)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        rl.addWidget(self.progress)
        self.results = QTreeWidget()
        self.results.setObjectName("resultsTree")
        self.results.setColumnCount(4)
        self.results.setHeaderLabels(["Request", "Status", "Time", "Tests"])
        self.results.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in ((1, 150), (2, 90), (3, 90)):
            self.results.header().setSectionResizeMode(col, QHeaderView.Fixed)
            self.results.setColumnWidth(col, width)
        self.results.setIconSize(QSize(38, 16))
        rl.addWidget(self.results, 1)
        splitter.addWidget(right)
        splitter.setSizes([340, 760])
        self.body_layout.addWidget(splitter, 1)
        self.add_button("Close", self.close)
        if not self.items:
            self.summary.setText("There are no requests to run here.")
            self.run_btn.setEnabled(False)

    def _select_all(self, state: bool) -> None:
        for idx in range(self.request_list.count()):
            self.request_list.item(idx).setCheckState(Qt.Checked if state else Qt.Unchecked)

    def _toggle_run(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.run_btn.setEnabled(False)
            self.run_btn.setText("Stopping…")
            return
        self._selected = [self.items[i] for i in range(self.request_list.count()) if self.request_list.item(i).checkState() == Qt.Checked]
        if not self._selected:
            return
        self.results.clear()
        self._stats = {"passed": 0, "failed": 0, "requests": 0, "errors": 0, "time": 0.0}
        total = len(self._selected) * self.iterations.value()
        self.progress.setRange(0, total)
        self.progress.setValue(0)
        self.worker = RunnerWorker(
            self._selected,
            self.app.variable_context(self.collection_id),
            self.app.settings,
            self.iterations.value(),
            self.delay.value(),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_all.connect(self._on_finished)
        self.run_btn.setText("Stop")
        self.run_btn.setIcon(theme.icon(G.STOP, "#ffffff", 14))
        self.summary.setText("Running…")
        self.worker.start()

    def _on_progress(self, iteration: int, index: int, result: RunResult) -> None:
        model, _ = self._selected[index]
        resp = result.response
        passed = sum(1 for t in result.tests if t.passed)
        failed = len(result.tests) - passed
        self._stats["passed"] += passed
        self._stats["failed"] += failed
        self._stats["requests"] += 1
        if resp is None:
            self._stats["errors"] += 1
        else:
            self._stats["time"] += resp.elapsed_ms

        prefix = f"#{iteration + 1}  " if self.iterations.value() > 1 else ""
        status = f"{resp.status} {resp.reason}" if resp else ("Cancelled" if result.cancelled else "Error")
        item = QTreeWidgetItem([prefix + model.name, status, format_ms(resp.elapsed_ms) if resp else "—", f"{passed}/{len(result.tests)}" if result.tests else "—"])
        item.setIcon(0, theme.method_badge_icon(model.method, 38, 16))
        item.setToolTip(0, f"{result.method} {result.url}")
        item.setForeground(1, theme.qcolor("danger") if resp is None else QColor(theme.status_color(resp.status)))
        if failed:
            item.setForeground(3, theme.qcolor("danger"))
        elif result.tests:
            item.setForeground(3, theme.qcolor("success"))
        if result.error:
            child = QTreeWidgetItem([result.error.splitlines()[0]])
            child.setForeground(0, theme.qcolor("danger"))
            item.addChild(child)
        for test in result.tests:
            child = QTreeWidgetItem([("PASS  " if test.passed else "FAIL  ") + test.name + (f" — {test.message}" if test.message else "")])
            child.setForeground(0, theme.qcolor("success" if test.passed else "danger"))
            item.addChild(child)
        for level, message in result.logs:
            child = QTreeWidgetItem([f"{level.upper()}  {message}"])
            child.setForeground(0, theme.qcolor("muted"))
            item.addChild(child)
        self.results.addTopLevelItem(item)
        if failed or result.error:
            item.setExpanded(True)
        self.results.scrollToItem(item)
        self.progress.setValue(self.progress.value() + 1)
        self._render_summary(running=True)

    def _render_summary(self, running: bool) -> None:
        s = self._stats
        ok = s["requests"] - s["errors"]
        avg = s["time"] / ok if ok else 0
        self.summary.setText(
            f"{'Running' if running else 'Completed'} · {s['requests']} requests · "
            f"<span style='color:{theme.color('success')}'>{s['passed']} passed</span> · "
            f"<span style='color:{theme.color('danger')}'>{s['failed']} failed</span>"
            + (f" · {s['errors']} errors" if s["errors"] else "")
            + f" · avg {format_ms(avg)}"
        )

    def _on_finished(self, ops) -> None:
        self.worker = None
        self.run_btn.setEnabled(True)
        self.run_btn.setText("Run again")
        self.run_btn.setIcon(theme.icon(G.PLAY, "#ffffff", 14))
        self._render_summary(running=False)
        if self.persist.isChecked() and ops:
            self.app.apply_variable_ops(ops, self.collection_id)

    def closeEvent(self, event) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.app.retire_worker(self.worker)
            try:
                self.worker.finished_all.disconnect(self._on_finished)
                self.worker.progress.disconnect(self._on_progress)
            except TypeError:
                pass
            self.worker = None
        super().closeEvent(event)
