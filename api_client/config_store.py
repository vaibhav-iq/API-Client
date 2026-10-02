import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict


@dataclass
class AppSettings:
    theme_mode: str = "dark"
    layout_mode: str = "vertical"
    editor_font_size: int = 10

    request_timeout_ms: int = 90000
    max_response_size_mb: int = 50
    follow_redirects: bool = True
    send_no_cache_header: bool = False
    send_postman_token_header: bool = False
    send_user_agent_header: bool = True
    ssl_verification: bool = False

    proxy_listen_port: int = 8080
    proxy_bind_host: str = "127.0.0.1"

    use_system_proxy: bool = True
    respect_env_proxy: bool = True
    use_custom_proxy: bool = False
    proxy_http_enabled: bool = True
    proxy_https_enabled: bool = True
    proxy_host: str = ""
    proxy_port: int = 8080
    proxy_auth_enabled: bool = False
    proxy_username: str = ""
    proxy_password: str = ""
    proxy_bypass: str = ""


_INT_LIMITS = {
    "request_timeout_ms": (0, 600000),
    "max_response_size_mb": (0, 2048),
    "proxy_port": (1, 65535),
    "proxy_listen_port": (1, 65535),
    "editor_font_size": (7, 24),
}


class SettingsStore:
    def __init__(self, settings_path: Path) -> None:
        self.settings_path = settings_path

    def load(self) -> AppSettings:
        defaults = AppSettings()

        if not self.settings_path.exists():
            return defaults

        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception:
            return defaults

        if not isinstance(data, dict):
            return defaults

        values: Dict[str, Any] = {}
        for f in fields(AppSettings):
            default = getattr(defaults, f.name)
            raw = data.get(f.name, default)
            if f.name == "theme_mode":
                values[f.name] = self._normalize_theme(raw)
            elif f.name == "layout_mode":
                values[f.name] = "horizontal" if str(raw).lower() == "horizontal" else "vertical"
            elif isinstance(default, bool):
                values[f.name] = bool(raw)
            elif isinstance(default, int):
                low, high = _INT_LIMITS.get(f.name, (-(2 ** 31), 2 ** 31))
                values[f.name] = self._to_int(raw, low, high, default)
            else:
                values[f.name] = str(raw) if f.name == "proxy_password" else str(raw).strip()
        return AppSettings(**values)

    def save(self, settings: AppSettings) -> None:
        payload: Dict[str, Any] = asdict(settings)
        payload["theme_mode"] = self._normalize_theme(payload.get("theme_mode"))
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _normalize_theme(theme_mode: Any) -> str:
        return "light" if str(theme_mode).lower() == "light" else "dark"

    @staticmethod
    def _to_int(value: Any, min_value: int, max_value: int, fallback: int) -> int:
        try:
            out = int(value)
        except Exception:
            return fallback
        return max(min_value, min(max_value, out))
