"""Append-only record of every KeeperHub execution and dry run this backend made.

The proof a judge asks for (execution ids, hashes, explorer links) is collected
here automatically, next to the strategy, instead of being copied out of logs.
It is also the resume table after a crash, so it must never lose an entry:
every read-modify-write holds an exclusive file lock, the new content lands
through an atomic rename, and a file that cannot be parsed is left untouched
and reported rather than replaced.
Path: ``ALMANAK_KEEPERHUB_RECEIPTS`` or ``./keeperhub-receipts.json``.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_FILENAME = "keeperhub-receipts.json"


def receipts_path() -> Path:
    configured = os.environ.get("ALMANAK_KEEPERHUB_RECEIPTS")
    return Path(configured) if configured else Path.cwd() / DEFAULT_FILENAME


def entry_key(entry: dict[str, Any]) -> str:
    """What makes an entry the same piece of work across two logs."""
    return str(entry.get("execution_id") or entry.get("tx_hash") or entry.get("recorded_at") or "")


def source_of(path: Path) -> str:
    """Which log an entry came from, named for the page: the strategy, the failure demos, or the benchmark.

    A union file (docs/all-receipts.json) is not a source; its entries keep the source they were saved with.
    """
    parent = path.resolve().parent.name
    if parent == "docs":
        return ""
    if parent == "failure_modes":
        return "failure-modes"
    if parent.startswith("metamorpho") or (path.parent / "strategy.py").exists():
        return "strategy"
    return "benchmark"


def merge_logs(*paths: Path) -> list[dict[str, Any]]:
    """The union of several receipts logs, one entry per execution, oldest first.

    A strategy, the failure-mode demos and the benchmark each keep their own log; the
    published proof is all of them together. When the same execution appears twice the
    later-updated copy wins, so a settlement recorded after the union was last built is
    not lost.
    """
    merged: dict[str, dict[str, Any]] = {}
    for path in paths:
        for entry in ReceiptLog(path)._read():
            if not entry.get("source") and (source := source_of(path)):
                entry["source"] = source
            key = entry_key(entry)
            previous = merged.get(key)
            if previous is None or str(entry.get("updated_at", "")) >= str(previous.get("updated_at", "")):
                merged[key] = entry
    return sorted(merged.values(), key=lambda e: str(e.get("recorded_at", "")))


class ReceiptLog:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or receipts_path()

    @property
    def path(self) -> Path:
        return self._path

    def record(self, execution_id: str, **fields: Any) -> None:
        self._mutate(
            lambda entries: (
                entries + [{"execution_id": execution_id, "recorded_at": datetime.now(UTC).isoformat(), **fields}]
            )
        )

    def record_simulation(self, **fields: Any) -> None:
        """A dry run, kept next to the executions so a simulate-only tick leaves a trace."""
        self._mutate(
            lambda entries: entries + [{"type": "simulation", "recorded_at": datetime.now(UTC).isoformat(), **fields}]
        )

    def update(self, execution_id: str, **fields: Any) -> None:
        def apply(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            for entry in entries:
                if entry.get("execution_id") == execution_id:
                    entry.update(fields)
                    entry["updated_at"] = datetime.now(UTC).isoformat()
            return entries

        self._mutate(apply)

    def find_by_hash(self, tx_hash: str) -> dict[str, Any] | None:
        """The entry for a transaction hash, so a new process can resume settlement."""
        wanted = tx_hash.lower()
        for entry in reversed(self._read()):
            if str(entry.get("tx_hash", "")).lower() == wanted:
                return entry
        return None

    def entries_since(self, iso_timestamp: str) -> list[dict[str, Any]]:
        return [e for e in self._read() if str(e.get("recorded_at", "")) >= iso_timestamp]

    # -- storage --------------------------------------------------------------------

    def _read(self) -> list[dict[str, Any]]:
        with self._locked(shared=True):
            return self._parse()

    def _mutate(self, apply: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked(shared=False):
            entries = apply(self._parse())
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(json.dumps(entries, indent=2) + "\n")
            os.replace(tmp, self._path)  # atomic: readers see the old file or the new one, never a torn one

    def _parse(self) -> list[dict[str, Any]]:
        try:
            text = self._path.read_text()
        except OSError:
            return []
        if not text.strip():
            return []
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ValueError(
                f"{self._path} is not valid JSON and holds proof and resume data; not overwriting it ({exc})"
            ) from exc
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []

    @contextlib.contextmanager
    def _locked(self, *, shared: bool) -> Iterator[None]:
        lock_path = self._path.with_name(self._path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
