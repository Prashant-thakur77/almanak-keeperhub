"""Append-only record of every KeeperHub execution this process broadcast.

The proof a judge asks for (execution ids, hashes, explorer links) is collected
here automatically, next to the strategy, instead of being copied out of logs.
Path: ``ALMANAK_KEEPERHUB_RECEIPTS`` or ``./keeperhub-receipts.json``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_FILENAME = "keeperhub-receipts.json"


def receipts_path() -> Path:
    configured = os.environ.get("ALMANAK_KEEPERHUB_RECEIPTS")
    return Path(configured) if configured else Path.cwd() / DEFAULT_FILENAME


class ReceiptLog:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or receipts_path()

    @property
    def path(self) -> Path:
        return self._path

    def record(self, execution_id: str, **fields: Any) -> None:
        entries = self._read()
        entries.append({"execution_id": execution_id, "recorded_at": datetime.now(UTC).isoformat(), **fields})
        self._write(entries)

    def update(self, execution_id: str, **fields: Any) -> None:
        entries = self._read()
        for entry in entries:
            if entry.get("execution_id") == execution_id:
                entry.update(fields)
                entry["updated_at"] = datetime.now(UTC).isoformat()
        self._write(entries)

    def entries_since(self, iso_timestamp: str) -> list[dict[str, Any]]:
        return [e for e in self._read() if str(e.get("recorded_at", "")) >= iso_timestamp]

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return []
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []

    def _write(self, entries: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(entries, indent=2) + "\n")
