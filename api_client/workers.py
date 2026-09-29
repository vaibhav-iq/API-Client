"""Background threads so the UI never blocks on the network."""
from typing import Any, Callable, Dict, Optional, Sequence

from PyQt5.QtCore import QThread, pyqtSignal

from .config_store import AppSettings
from .engine import RunResult, VariableContext, new_session, run_request
from .models import AuthConfig, RequestModel


class RequestWorker(QThread):
    finished_result = pyqtSignal(object)

    def __init__(
        self,
        model: RequestModel,
        ctx: VariableContext,
        settings: AppSettings,
        inherited_auths: Sequence[AuthConfig],
        info: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.ctx = ctx
        self.settings = settings
        self.inherited_auths = list(inherited_auths)
        self.info = info or {}
        self._cancelled = False
        self._session = None

    def cancel(self) -> None:
        self._cancelled = True
        session = self._session
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    def run(self) -> None:
        self._session = new_session(self.settings)
        try:
            result = run_request(
                self.model,
                self.ctx,
                self.settings,
                self.inherited_auths,
                session=self._session,
                cancelled=lambda: self._cancelled,
                info=self.info,
            )
        except Exception as exc:  # noqa: BLE001 - never let the thread die silently
            result = RunResult(error=f"Unexpected error: {type(exc).__name__}: {exc}", method=self.model.method, url=self.model.url)
        finally:
            try:
                self._session.close()
            except Exception:
                pass
        if self._cancelled:
            result.cancelled = True
        self.finished_result.emit(result)


class FunctionWorker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            value = self.fn()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.done.emit(value)
