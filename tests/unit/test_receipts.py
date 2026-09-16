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


def test_an_empty_or_missing_file_starts_a_new_log(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text("")
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


def test_simulations_are_recorded_separately_from_executions(tmp_path: Path) -> None:
    log = ReceiptLog(tmp_path / "r.json")
    log.record_simulation(
        chain_id=8453, to="0xVault", function="deposit", success=False, would_revert=True, error="balance"
    )
    log.record("exec-1", chain_id=8453, function="approve", to="0xToken", tx_hash="0xabc", status="completed")

    entries = json.loads((tmp_path / "r.json").read_text())
    assert entries[0]["type"] == "simulation" and entries[0]["would_revert"] is True
    assert "execution_id" not in entries[0]
    assert [e["execution_id"] for e in log.entries_since("2000-01-01") if e.get("type") != "simulation"] == ["exec-1"]
    assert log.find_by_hash("0xabc")["execution_id"] == "exec-1"


def test_concurrent_writers_lose_nothing(tmp_path: Path) -> None:
    """Several processes append at once (bot, demos, strategy): every entry must survive."""
    import multiprocessing

    path = tmp_path / "r.json"

    def writer(n: int) -> None:
        log = ReceiptLog(path)
        for i in range(25):
            log.record(f"p{n}-{i}", chain_id=1, function="f", to="0x", tx_hash=f"0x{n}{i}", status="completed")

    procs = [multiprocessing.Process(target=writer, args=(n,)) for n in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()

    entries = json.loads(path.read_text())
    assert len(entries) == 100


def test_a_corrupt_file_is_not_silently_replaced(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text('[{"execution_id": "keep"}, {"execution_id": "half')  # a torn write
    log = ReceiptLog(path)
    with pytest.raises(ValueError):
        log.record("new", chain_id=1, function="f", to="0x", tx_hash="0x1", status="completed")
    assert path.read_text().startswith('[{"execution_id": "keep"}')  # untouched


def test_merge_logs_unions_by_execution_and_keeps_the_later_settlement(tmp_path: Path) -> None:
    from almanak_keeperhub.receipts import merge_logs

    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(
        json.dumps(
            [
                {"execution_id": "x1", "recorded_at": "2026-09-12T06:00:00+00:00", "status": "pending"},
                {"type": "simulation", "recorded_at": "2026-09-12T05:00:00+00:00", "success": True},
            ]
        )
    )
    b.write_text(
        json.dumps(
            [
                {
                    "execution_id": "x1",
                    "recorded_at": "2026-09-12T06:00:00+00:00",
                    "status": "completed",
                    "updated_at": "2026-09-12T06:00:09+00:00",
                },
                {"execution_id": "x2", "recorded_at": "2026-09-12T07:00:00+00:00", "status": "completed"},
            ]
        )
    )

    merged = merge_logs(a, b)

    assert [e.get("execution_id") or e["type"] for e in merged] == ["simulation", "x1", "x2"]
    assert merged[1]["status"] == "completed"  # the settled copy wins over the pending one


def test_merge_logs_ignores_a_missing_log(tmp_path: Path) -> None:
    from almanak_keeperhub.receipts import merge_logs

    present = tmp_path / "present.json"
    present.write_text(json.dumps([{"execution_id": "x1", "recorded_at": "2026-09-12T06:00:00+00:00"}]))

    assert [e["execution_id"] for e in merge_logs(tmp_path / "absent.json", present)] == ["x1"]


def test_merge_logs_tags_each_entry_with_its_source(tmp_path: Path) -> None:
    from almanak_keeperhub.receipts import merge_logs

    strategy = tmp_path / "metamorpho_base_sepolia" / "keeperhub-receipts.json"
    demos = tmp_path / "failure_modes" / "keeperhub-receipts.json"
    bench = tmp_path / "keeperhub-receipts.json"
    union = tmp_path / "docs" / "all-receipts.json"
    (tmp_path / "pyproject.toml").write_text("")  # the benchmark log sits at the repository root
    for i, path in enumerate((strategy, demos, bench)):
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps([{"execution_id": f"x{i}", "recorded_at": f"2026-09-12T0{i}:00:00+00:00"}]))
    union.parent.mkdir()
    union.write_text(
        json.dumps([{"execution_id": "old", "recorded_at": "2026-09-11T00:00:00+00:00", "source": "strategy"}])
    )

    merged = {e["execution_id"]: e.get("source") for e in merge_logs(union, strategy, demos, bench)}

    assert merged == {"old": "strategy", "x0": "strategy", "x1": "failure-modes", "x2": "benchmark"}
