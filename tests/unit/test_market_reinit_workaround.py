"""Workaround for an Almanak in-process gateway deadlock (see gateway.py docstring)."""

from __future__ import annotations

import pytest

from almanak_keeperhub.gateway import skip_redundant_market_reinit


class _Market:
    def __init__(self, chains: list[str]) -> None:
        self._price_aggregators = {chain: object() for chain in chains}


async def test_skips_reinit_when_chain_already_has_an_aggregator() -> None:
    calls: list[tuple] = []

    async def original(market, initialized):
        calls.append((market, initialized))

    wrapped = skip_redundant_market_reinit(original)
    await wrapped(_Market(["base"]), ["base"])

    assert calls == []


async def test_delegates_when_chain_is_not_served_yet() -> None:
    calls: list[tuple] = []

    async def original(market, initialized):
        calls.append((market, initialized))

    wrapped = skip_redundant_market_reinit(original)
    market = _Market(["ethereum"])
    await wrapped(market, ["base"])

    assert calls == [(market, ["base"])]


@pytest.mark.parametrize("initialized", [[], None])
async def test_delegates_when_nothing_initialized(initialized) -> None:
    calls: list[tuple] = []

    async def original(market, initialized):
        calls.append((market, initialized))

    await skip_redundant_market_reinit(original)(_Market(["base"]), initialized)
    assert len(calls) == 1
