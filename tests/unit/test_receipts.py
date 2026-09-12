"""Every broadcast is appended to a receipts file so the proof links are collected automatically."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from almanak_keeperhub.receipts import ReceiptLog, receipts_path


def test_default_path_is_in_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ALMANAK_KEEPERHUB_RECEIPTS", raising=False)
    assert receipts_path() == tmp_path / "keeperhub-receipts.json"


def test_env_overrides_the_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALMANAK_KEEPERHUB_RECEIPTS", str(tmp_path / "custom.json"))
    assert receipts_path() == tmp_path / "custom.json"


def test_record_appends_and_update_merges_by_execution_id(tmp_path: Path) -> None:
    log = ReceiptLog(tmp_path / "r.json")

    log.record("exec-1", chain_id=8453, function="approve", to="0xToken", tx_hash="0xabc", status="unconfirmed")
    log.record("exec-2", chain_id=8453, function="deposit", to="0xVault", tx_hash="0xdef", status="completed")
    log.update("exec-1", status="completed", verified=True, transaction_link="https://basescan.org/tx/0xabc")

    entries = json.loads((tmp_path / "r.json").read_text())
    assert [e["execution_id"] for e in entries] == ["exec-1", "exec-2"]
    assert entries[0]["status"] == "completed"
    assert entries[0]["verified"] is True
    assert entries[0]["transaction_link"] == "https://basescan.org/tx/0xabc"
    assert entries[0]["recorded_at"]


def test_entries_since_filters_by_time(tmp_path: Path) -> None:
    log = ReceiptLog(tmp_path / "r.json")
    log.record("old", chain_id=1, function="f", to="0x", tx_hash="0x1", status="completed")
    entries = json.loads((tmp_path / "r.json").read_text())
    entries[0]["recorded_at"] = "2000-01-01T00:00:00+00:00"
    (tmp_path / "r.json").write_text(json.dumps(entries))
    log.record("new", chain_id=1, function="f", to="0x", tx_hash="0x2", status="completed")

    assert [e["execution_id"] for e in log.entries_since("2020-01-01T00:00:00+00:00")] == ["new"]


def test_unreadable_file_does_not_break_recording(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text("{not json")
    log = ReceiptLog(path)
    log.record("exec-1", chain_id=1, function="f", to="0x", tx_hash="0x1", status="completed")
    assert json.loads(path.read_text())[0]["execution_id"] == "exec-1"


def test_find_by_hash_is_case_insensitive(tmp_path: Path) -> None:
    log = ReceiptLog(tmp_path / "r.json")
    log.record("exec-9", chain_id=8453, function="deposit", to="0xVault", tx_hash="0xABCDEF", status="unconfirmed")

    found = log.find_by_hash("0xabcdef")

    assert found is not None
    assert found["execution_id"] == "exec-9"
    assert log.find_by_hash("0x000") is None
