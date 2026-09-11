import pytest


@pytest.fixture(autouse=True)
def _receipts_in_tmp(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the receipts log out of the working directory during tests."""
    monkeypatch.setenv("ALMANAK_KEEPERHUB_RECEIPTS", str(tmp_path / "receipts.json"))
