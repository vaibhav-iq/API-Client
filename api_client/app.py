import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QGuiApplication
from PyQt5.QtWidgets import QApplication

from . import theme
from .main_window import MainWindow


def _set_windows_app_id() -> None:
    """Give the process its own taskbar identity so our icon is shown instead of python.exe's."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("eClinicalWorks.APIClient.2")
    except Exception:
        pass


def _install_exception_hook() -> None:
    """Log unexpected exceptions instead of letting PyQt abort the whole process."""
    log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "APIClient"

    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(text)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "error.log", "a", encoding="utf-8") as handle:
                handle.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n{text}")
        except Exception:
            pass
        app = QApplication.instance()
        window = getattr(app, "main_window", None) if app else None
        if window is not None:
            try:
                window.toast(f"Unexpected error: {exc_type.__name__}: {exc} (details in error.log)", error=True)
            except Exception:
                pass

    sys.excepthook = hook


def main() -> None:
    _install_exception_hook()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    if hasattr(QGuiApplication, "setHighDpiScaleFactorRoundingPolicy"):
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    _set_windows_app_id()

    app = QApplication(sys.argv)
    app.setApplicationName("API Client")
    app.setOrganizationName("APIClient")
    app.setStyle("Fusion")
    app.setFont(QFont(theme.ui_font_family(), 10))
    app.setWindowIcon(theme.app_icon())

    window = MainWindow()
    app.main_window = window
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
