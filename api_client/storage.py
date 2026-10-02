"""Workspace persistence: collections, environments, open-tab session and request history."""
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import Collection, Environment


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Keep a copy of the unreadable file instead of silently overwriting it later.
        try:
            path.replace(path.with_suffix(path.suffix + f".corrupt-{int(time.time())}"))
        except Exception:
            pass
        return default


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.collections_path = root / "collections.json"
        self.environments_path = root / "environments.json"
        self.session_path = root / "session.json"
        self.identities_path = root / "identities.json"
        self.history = HistoryStore(root / "cache.sqlite3")

    def load_collections(self) -> List[Collection]:
        data = _read_json(self.collections_path, {"collections": []})
        items = data.get("collections", []) if isinstance(data, dict) else []
        return [Collection.from_dict(item) for item in items if isinstance(item, dict)]

    def save_collections(self, collections: List[Collection]) -> None:
        _atomic_write(self.collections_path, {"version": 1, "collections": [c.to_dict() for c in collections]})

    def load_environments(self) -> Tuple[Environment, List[Environment], Optional[str]]:
        data = _read_json(self.environments_path, {})
        if not isinstance(data, dict):
            data = {}
        globals_env = Environment.from_dict(data.get("globals") or {"id": "globals", "name": "Globals"})
        globals_env.id, globals_env.name = "globals", "Globals"
        envs = [Environment.from_dict(e) for e in data.get("environments", []) if isinstance(e, dict)]
        active = data.get("active")
        if active not in {e.id for e in envs}:
            active = None
        return globals_env, envs, active

    def save_environments(self, globals_env: Environment, envs: List[Environment], active: Optional[str]) -> None:
        _atomic_write(
            self.environments_path,
            {"version": 1, "globals": globals_env.to_dict(), "environments": [e.to_dict() for e in envs], "active": active},
        )

    def load_identities(self) -> List[Dict[str, Any]]:
        data = _read_json(self.identities_path, {"identities": []})
        items = data.get("identities", []) if isinstance(data, dict) else []
        return [i for i in items if isinstance(i, dict)]

    def save_identities(self, identities: List[Dict[str, Any]]) -> None:
        _atomic_write(self.identities_path, {"version": 1, "identities": identities})

    def load_session(self) -> Dict[str, Any]:
        data = _read_json(self.session_path, {})
        return data if isinstance(data, dict) else {}

    def save_session(self, session: Dict[str, Any]) -> None:
        _atomic_write(self.session_path, session)


class HistoryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    method TEXT NOT NULL,
                    url TEXT NOT NULL,
                    status INTEGER,
                    elapsed_ms REAL,
                    request_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def add(self, method: str, url: str, status: Optional[int], elapsed_ms: Optional[float], request: Dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO history (created_at, method, url, status, elapsed_ms, request_json) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), method, url, status, elapsed_ms, json.dumps(request)),
            )
            conn.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY created_at DESC LIMIT 1000)")

    def recent(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, method, url, status, elapsed_ms FROM history ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": r[0], "created_at": r[1], "method": r[2], "url": r[3], "status": r[4], "elapsed_ms": r[5]}
            for r in rows
        ]

    def get_request(self, entry_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT request_json FROM history WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def delete(self, entry_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM history WHERE id = ?", (entry_id,))

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM history")
