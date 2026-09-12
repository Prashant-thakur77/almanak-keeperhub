"""Proving who acted when the explorer shows KeeperHub's relayer as sender."""

from __future__ import annotations

from eth_abi import encode
from eth_utils import keccak

from almanak_keeperhub.verify import actor_evidence

ORG = "0x0bdf000000000000000000000000000000000001"
VAULT = "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def topic(sig: str) -> str:
    return "0x" + keccak(text=sig).hex()


def addr_topic(address: str) -> str:
    return "0x" + address[2:].lower().rjust(64, "0")


def test_approval_and_deposit_events_name_the_org_wallet() -> None:
    logs = [
        {
            "address": USDC,
            "topics": [topic("Approval(address,address,uint256)"), addr_topic(ORG), addr_topic(VAULT)],
            "data": "0x" + encode(["uint256"], [5_000_000]).hex(),
        },
        {
            "address": VAULT,
            "topics": [topic("Deposit(address,address,uint256,uint256)"), addr_topic(ORG), addr_topic(ORG)],
            "data": "0x" + encode(["uint256", "uint256"], [5_000_000, 4_900_000]).hex(),
        },
    ]

    lines = actor_evidence(logs, ORG)

    assert any("Approval" in ln and "owner" in ln and "org wallet" in ln for ln in lines)
    assert any("Deposit" in ln and "assets=5000000" in ln and "org wallet" in ln for ln in lines)


def test_events_from_other_actors_are_reported_as_such() -> None:
    other = "0x00000000000000000000000000000000deadbeef"
    logs = [
        {
            "address": USDC,
            "topics": [topic("Transfer(address,address,uint256)"), addr_topic(other), addr_topic(VAULT)],
            "data": "0x" + encode(["uint256"], [1]).hex(),
        }
    ]

    lines = actor_evidence(logs, ORG)

    assert len(lines) == 1
    assert "not the org wallet" in lines[0]


def test_unknown_events_are_skipped() -> None:
    assert actor_evidence([{"address": USDC, "topics": ["0x" + "11" * 32], "data": "0x"}], ORG) == []


def test_mint_and_burn_events_are_labelled_not_blamed() -> None:
    zero = "0x" + "00" * 20
    logs = [
        {
            "address": VAULT,
            "topics": [topic("Transfer(address,address,uint256)"), addr_topic(zero), addr_topic(ORG)],
            "data": "0x" + encode(["uint256"], [7]).hex(),
        },
        {
            "address": VAULT,
            "topics": [topic("Transfer(address,address,uint256)"), addr_topic(ORG), addr_topic(zero)],
            "data": "0x" + encode(["uint256"], [7]).hex(),
        },
    ]

    lines = actor_evidence(logs, ORG)

    assert "mint to the org wallet" in lines[0]
    assert "burn from the org wallet" in lines[1]
