"""Evidence of who acted, read from the receipt logs.

KeeperHub sponsors gas on Base, so the explorer shows its relayer as the
transaction sender (docs/wallet-management/onchain-appearance.md). The events
still name the organization wallet as owner, sender or spender; this module
decodes the common ones so a judge can see it without an explorer.
"""

from __future__ import annotations

import re
from typing import Any

from eth_abi import decode
from eth_utils import keccak

EVENTS: dict[str, tuple[str, list[str], list[str], list[str]]] = {
    # topic0 -> (name, indexed names, data types, data names)
    "0x" + keccak(text="Transfer(address,address,uint256)").hex(): ("Transfer", ["from", "to"], ["uint256"], ["value"]),
    "0x" + keccak(text="Approval(address,address,uint256)").hex(): (
        "Approval",
        ["owner", "spender"],
        ["uint256"],
        ["value"],
    ),
    "0x" + keccak(text="Deposit(address,address,uint256,uint256)").hex(): (
        "Deposit",
        ["sender", "owner"],
        ["uint256", "uint256"],
        ["assets", "shares"],
    ),
    "0x" + keccak(text="Withdraw(address,address,address,uint256,uint256)").hex(): (
        "Withdraw",
        ["sender", "receiver", "owner"],
        ["uint256", "uint256"],
        ["assets", "shares"],
    ),
}
ACTOR_FIELDS = ("owner", "sender", "from")
_TX_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
_EXECUTION_ID = re.compile(r"^(?!0x)[A-Za-z0-9_-]{1,64}$")


def valid_reference(reference: str) -> bool:
    """A transaction hash or a KeeperHub execution id; anything else never reaches a URL."""
    return bool(reference) and bool(_TX_HASH.match(reference) or _EXECUTION_ID.match(reference))


ZERO = "0x" + "00" * 20


def _address(topic: Any) -> str:
    raw = topic.hex() if isinstance(topic, bytes | bytearray) else str(topic)
    return "0x" + raw[-40:].lower()


def actor_evidence(logs: list[dict[str, Any]], org_wallet: str) -> list[str]:
    """One line per recognised event, saying whether the org wallet is the actor."""
    org = org_wallet.lower()
    lines: list[str] = []
    for log in logs:
        topics = log.get("topics") or []
        if not topics:
            continue
        topic0 = topics[0].hex() if isinstance(topics[0], bytes | bytearray) else str(topics[0])
        if not topic0.startswith("0x"):
            topic0 = "0x" + topic0
        spec = EVENTS.get(topic0.lower())
        if spec is None:
            continue
        name, indexed, data_types, data_names = spec
        fields = {label: _address(t) for label, t in zip(indexed, topics[1:], strict=False)}
        data = log.get("data") or "0x"
        raw = bytes.fromhex(data[2:]) if isinstance(data, str) else bytes(data)
        try:
            values = decode(data_types, raw) if raw else ()
        except Exception:  # noqa: BLE001 - malformed data: still report the indexed fields
            values = ()
        fields.update({label: str(v) for label, v in zip(data_names, values, strict=False)})
        actor = next((fields[f] for f in ACTOR_FIELDS if f in fields), None)
        if name == "Transfer" and fields.get("from") == ZERO:
            verdict = "mint to the org wallet" if fields.get("to") == org else "mint, not the org wallet"
        elif name == "Transfer" and fields.get("to") == ZERO:
            verdict = "burn from the org wallet" if actor == org else "burn, not the org wallet"
        else:
            verdict = "org wallet" if actor == org else "not the org wallet"
        rendered = " ".join(f"{k}={v}" for k, v in fields.items())
        lines.append(f"{name} at {str(log.get('address', '')).lower()}: {rendered} -> actor is {verdict}")
    return lines
