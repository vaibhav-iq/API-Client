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


def _proxy_selftest() -> None:
    """Headless check that the CA + intercepting proxy work in this (possibly frozen)
    build. Writes the result to %LOCALAPPDATA%/APIClient/selftest.log. Run with
    `APIClient.exe --proxy-selftest`."""
    import http.server
    import tempfile
    import threading
    import traceback

    log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "APIClient"
    log_dir.mkdir(parents=True, exist_ok=True)
    lines = []

    def log(*parts) -> None:
        lines.append(" ".join(str(p) for p in parts))

    try:
        from PyQt5.QtCore import QCoreApplication

        qapp = QCoreApplication(sys.argv)
        import requests

        from .ca import CertAuthority
        from .config_store import AppSettings
        from .proxy_server import ProxyController

        log("python", sys.version.split()[0], "frozen", getattr(sys, "frozen", False))
        import cryptography
        log("cryptography", cryptography.__version__)

        ca = CertAuthority(Path(tempfile.mkdtemp(prefix="selftest_ca_")))
        ca.generate()
        log("CA generated:", ca.exists())
        ca.context_for("127.0.0.1")
        log("leaf TLS context: OK")

        class _Origin(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":1}')

        origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Origin)
        threading.Thread(target=origin.serve_forever, daemon=True).start()
        op = origin.server_address[1]

        captured = []
        ctrl = ProxyController(ca, lambda: AppSettings())
        ctrl.captured.connect(lambda rec: captured.append(rec))
        ctrl.start(0)
        pport = ctrl._server.server_address[1]
        log("proxy listening on", pport)
        resp = requests.get(f"http://127.0.0.1:{op}/x", proxies={"http": f"http://127.0.0.1:{pport}"}, timeout=10)
        qapp.processEvents()
        log("forward status:", resp.status_code, "captured rows:", len(captured))
        ctrl.stop()
        origin.shutdown()
        log("RESULT: OK" if resp.status_code == 200 else "RESULT: FAIL (bad status)")
    except Exception:  # noqa: BLE001
        log("RESULT: FAIL")
        log(traceback.format_exc())

    (log_dir / "selftest.log").write_text("\n".join(lines), encoding="utf-8")
    sys.stderr.write("\n".join(lines) + "\n")


def main() -> None:
    if "--proxy-selftest" in sys.argv:
        _proxy_selftest()
        return
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
