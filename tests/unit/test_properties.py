"""Property-based checks over the whole selector index and the key derivation.

Every function signature the package can decode is exercised with randomly generated
arguments: the calldata Almanak would compile must decode losslessly, and anything that
would not survive re-encoding must be refused, because the transaction KeeperHub sends
is rebuilt from the decoded call, not from the bytes.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, localcontext
from typing import Any

import pytest
from almanak.framework.execution.interfaces import TransactionType, UnsignedTransaction
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from almanak_keeperhub.calldata import SelectorIndex, _split_top_level, decode_calldata
from almanak_keeperhub.client import ContractCall, wei_to_ether_string
from almanak_keeperhub.errors import UndecodableCalldata
from almanak_keeperhub.signer import idempotency_key_for

INDEX = SelectorIndex.default()
SIGNATURES = sorted((INDEX.lookup(sel) for sel in INDEX.selectors), key=lambda s: s.signature)  # type: ignore[union-attr]
TO = "0xd36E12a5b2926A5cbE6B4DE42a0D60Fd35d3cb04"
SENDER = "0xe7DbACbDD4Cb2ddfF5681dCD9E56Fcf488E36Ac9"


def value_for(abi_type: str) -> st.SearchStrategy[Any]:
    """A hypothesis strategy producing a valid value of one canonical ABI type."""
    if abi_type.endswith("]"):
        base, size = abi_type[: abi_type.rindex("[")], abi_type[abi_type.rindex("[") + 1 : -1]
        if size:
            return st.lists(value_for(base), min_size=int(size), max_size=int(size))
        return st.lists(value_for(base), max_size=3)
    if abi_type.startswith("("):
        return st.tuples(*(value_for(t) for t in _split_top_level(abi_type[1:-1])))
    if abi_type == "address":
        return st.binary(min_size=20, max_size=20).map(lambda b: "0x" + b.hex())
    if abi_type == "bool":
        return st.booleans()
    if abi_type == "string":
        return st.text(max_size=40)
    if abi_type == "bytes":
        return st.binary(max_size=80)
    if m := re.fullmatch(r"bytes(\d+)", abi_type):
        return st.binary(min_size=int(m.group(1)), max_size=int(m.group(1)))
    if m := re.fullmatch(r"uint(\d+)", abi_type):
        return st.integers(min_value=0, max_value=2 ** int(m.group(1)) - 1)
    if m := re.fullmatch(r"int(\d+)", abi_type):
        bits = int(m.group(1))
        return st.integers(min_value=-(2 ** (bits - 1)), max_value=2 ** (bits - 1) - 1)
    raise NotImplementedError(abi_type)


@pytest.mark.parametrize("sig", SIGNATURES, ids=lambda s: s.signature)
@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data())
def test_every_indexed_signature_decodes_losslessly(sig, data) -> None:
    args = [data.draw(value_for(t)) for t in sig.types]
    calldata = "0x" + (function_signature_to_4byte_selector(sig.signature) + encode(sig.types, args)).hex()

    decoded = decode_calldata(calldata, to=TO, value_wei=0)

    assert decoded.function_name == sig.name
    assert decoded.signature == sig.signature
    assert len(decoded.function_args) == len(sig.types)
    json.dumps(decoded.function_args)  # what KeeperHub receives as functionArgs
    assert decoded.abi[0]["name"] == sig.name and len(decoded.abi[0]["inputs"]) == len(sig.types)


@pytest.mark.parametrize("sig", [s for s in SIGNATURES if s.types][:60], ids=lambda s: s.signature)
@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data(), suffix=st.binary(min_size=1, max_size=32))
def test_trailing_bytes_are_refused_not_dropped(sig, data, suffix) -> None:
    """eth_abi would ignore the suffix; the decoder must not, since the send is rebuilt from the decode."""
    args = [data.draw(value_for(t)) for t in sig.types]
    encoded = encode(sig.types, args)
    calldata = "0x" + (function_signature_to_4byte_selector(sig.signature) + encoded + suffix).hex()
    with pytest.raises(UndecodableCalldata):
        decode_calldata(calldata, to=TO, value_wei=0)


@given(selector=st.binary(min_size=4, max_size=4), tail=st.binary(max_size=96))
@settings(max_examples=200, deadline=None)
def test_unknown_selectors_are_always_refused(selector, tail) -> None:
    if "0x" + selector.hex() in INDEX.selectors:
        return
    with pytest.raises(UndecodableCalldata):
        decode_calldata("0x" + (selector + tail).hex(), to=TO, value_wei=0)


def _tx(data: str, to: str = TO, value: int = 0, chain_id: int = 84532, nonce: int = 0, gas: int = 1) -> Any:
    return UnsignedTransaction(
        to=to,
        value=value,
        data=data,
        chain_id=chain_id,
        gas_limit=gas,
        nonce=nonce,
        tx_type=TransactionType.EIP_1559,
        from_address=SENDER,
        max_fee_per_gas=gas,
        max_priority_fee_per_gas=gas,
    )


hexdata = st.binary(min_size=4, max_size=100).map(lambda b: "0x" + b.hex())


@given(data=hexdata, work=st.text(max_size=20), nonce=st.integers(0, 10**6), gas=st.integers(1, 10**7))
@settings(max_examples=200, deadline=None)
def test_idempotency_key_identifies_the_work_not_the_attempt(data, work, nonce, gas) -> None:
    first = idempotency_key_for(_tx(data), SENDER, work)
    retry = idempotency_key_for(_tx(data, nonce=nonce, gas=gas), SENDER, work)  # a new attempt
    assert first == retry
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert idempotency_key_for(_tx(data), SENDER.lower(), work) == first  # case of the sender is not work


@given(data=hexdata, other=hexdata, work=st.text(max_size=20), other_work=st.text(max_size=20))
@settings(max_examples=200, deadline=None)
def test_different_work_gets_a_different_key(data, other, work, other_work) -> None:
    key = idempotency_key_for(_tx(data), SENDER, work)
    if other != data:
        assert idempotency_key_for(_tx(other), SENDER, work) != key
    if other_work != work:
        assert idempotency_key_for(_tx(data), SENDER, other_work) != key
    assert idempotency_key_for(_tx(data, value=1), SENDER, work) != key
    assert idempotency_key_for(_tx(data, chain_id=8453), SENDER, work) != key


@given(wei=st.integers(0, 2**128))
@settings(max_examples=300, deadline=None)
def test_ether_string_is_exact(wei) -> None:
    text = wei_to_ether_string(wei)
    assert "e" not in text.lower() and not text.endswith(".")
    with localcontext() as ctx:
        ctx.prec = 80
        assert Decimal(text) * 10**18 == wei


@given(data=hexdata, name=st.sampled_from(["", "approve", "deposit"]))
@settings(max_examples=100, deadline=None)
def test_contract_call_bodies_never_mix_the_two_spellings(data, name) -> None:
    call = ContractCall(TO, 84532, name, ["1"], [{"type": "function", "name": name}] if name else [], data=data)
    raw = call.body(raw=True)
    assert "data" in raw and "functionName" not in raw
    if call.raw_only:
        assert "functionName" not in call.body() and "abi" not in raw
    else:
        typed = call.body()
        assert "functionName" in typed and "data" not in typed
    assert call.label() == (name or data[:10])
