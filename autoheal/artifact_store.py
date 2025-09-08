# autoheal/artifact_store.py
from __future__ import annotations
import json
import os
from typing import Any

__all__ = [
    "ArtifactStore",
]

class ArtifactStore:
    """
    Minimal artifact store that writes into a local folder.
    Backward-compatible with both save_* and put_* call sites.
    """
    def __init__(self, base_path: str) -> None:
        self.base = os.path.abspath(base_path or "./artifacts")
        os.makedirs(self.base, exist_ok=True)

    # ---- helpers ----
    def _path(self, name: str) -> str:
        p = os.path.join(self.base, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    # ---- write APIs (json / text / bytes) ----
    def save_json(self, name: str, obj: Any, *, indent: int = 2) -> str:
        p = self._path(name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
        return p

    def save_text(self, name: str, text: str) -> str:
        p = self._path(name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text if text is not None else "")
        return p

    def save_bytes(self, name: str, data: bytes) -> str:
        p = self._path(name)
        with open(p, "wb") as f:
            f.write(data or b"")
        return p

    # ---- read APIs ----
    def load_json(self, name: str) -> Any:
        p = self._path(name)
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_text(self, name: str) -> str:
        p = self._path(name)
        with open(p, "r", encoding="utf-8") as f:
            return f.read()

    # ---- new aliases to keep CLI calls working ----
    def put_json(self, name: str, obj: Any) -> str:
        """Alias for save_json (CLI expects this)."""
        return self.save_json(name, obj)

    def put_text(self, name: str, text: str) -> str:
        """Alias for save_text."""
        return self.save_text(name, text)

    def put_bytes(self, name: str, data: bytes) -> str:
        """Alias for save_bytes."""
        return self.save_bytes(name, data)
