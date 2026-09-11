"""Calldata is decoded offline against a selector index and refused when unknown."""

import json
from pathlib import Path

import pytest
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector, to_checksum_address

from almanak_keeperhub.calldata import DecodedCall, SelectorIndex, decode_calldata
from almanak_keeperhub.errors import UndecodableCalldata

VAULT = "0xc1256Ae5FF1cf2719D4937adb3bbCCab2E00A2Ca"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
OWNER = to_checksum_address("0x0bdf000000000000000000000000000000000001")


def _calldata(signature: str, types: list[str], args: list) -> str:
    return "0x" + (function_signature_to_4byte_selector(signature) + encode(types, args)).hex()


def test_erc20_approve_decodes_from_bundled_table() -> None:
    data = _calldata("approve(address,uint256)", ["address", "uint256"], [VAULT, 5_000_000])

    call = decode_calldata(data, to=USDC, value_wei=0)

    assert isinstance(call, DecodedCall)
    assert call.function_name == "approve"
    assert call.signature == "approve(address,uint256)"
    assert call.function_args == [VAULT, "5000000"]
    entry = call.abi[0]
    assert entry["type"] == "function" and entry["name"] == "approve"
    assert [i["type"] for i in entry["inputs"]] == ["address", "uint256"]
    assert entry["stateMutability"] == "nonpayable"
    # The ERC-20 ABI shipped inside almanak wins over the bare signature, so inputs are named.
    assert [i["name"] for i in entry["inputs"]] == ["spender", "amount"]


def test_bare_signature_synthesises_a_minimal_abi_entry() -> None:
    index = SelectorIndex.from_signatures(["approve(address,uint256)"])
    data = _calldata("approve(address,uint256)", ["address", "uint256"], [VAULT, 1])

    call = decode_calldata(data, to=USDC, value_wei=0, index=index)

    assert call.abi == [
        {
            "type": "function",
            "name": "approve",
            "inputs": [{"name": "arg0", "type": "address"}, {"name": "arg1", "type": "uint256"}],
            "outputs": [],
            "stateMutability": "nonpayable",
        }
    ]


def test_erc4626_deposit_selector_matches_almanak_morpho_vault_sdk() -> None:
    # almanak/connectors/morpho_vault/sdk.py: DEPOSIT_SELECTOR = "0x6e553f65"  # deposit(uint256,address)
    data = _calldata("deposit(uint256,address)", ["uint256", "address"], [5_000_000, OWNER])
    assert data.startswith("0x6e553f65")

    call = decode_calldata(data, to=VAULT, value_wei=0)

    assert call.function_name == "deposit"
    assert call.function_args == ["5000000", OWNER]


def test_native_value_marks_function_payable() -> None:
    data = _calldata("deposit()", [], [])
    call = decode_calldata(data, to="0x4200000000000000000000000000000000000006", value_wei=10**15)
    assert call.abi[0]["stateMutability"] == "payable"
    assert call.function_args == []


def test_unknown_selector_is_refused_with_the_selector_named() -> None:
    data = "0xdeadbeef" + "00" * 64
    with pytest.raises(UndecodableCalldata) as excinfo:
        decode_calldata(data, to=VAULT, value_wei=0)
    assert excinfo.value.selector == "0xdeadbeef"
    assert "0xdeadbeef" in str(excinfo.value)


def test_empty_calldata_is_refused() -> None:
    with pytest.raises(UndecodableCalldata):
        decode_calldata("0x", to=VAULT, value_wei=10**15)


def test_bytes_bools_and_arrays_are_json_friendly() -> None:
    index = SelectorIndex.from_signatures(["multi(bytes,bool,uint256[],address[2])"])
    data = _calldata(
        "multi(bytes,bool,uint256[],address[2])",
        ["bytes", "bool", "uint256[]", "address[2]"],
        [b"\x01\x02", True, [1, 2], [VAULT, USDC]],
    )

    call = decode_calldata(data, to=VAULT, value_wei=0, index=index)

    assert call.function_args == ["0x0102", True, ["1", "2"], [VAULT, USDC]]
    assert json.dumps(call.function_args)


def test_tuple_arguments_become_objects_keyed_by_component_name() -> None:
    signature = "supply((address,address,address,address,uint256),uint256,uint256,address,bytes)"
    index = SelectorIndex.from_signatures([signature])
    market = (USDC, VAULT, OWNER, OWNER, 860000000000000000)
    data = _calldata(
        signature,
        ["(address,address,address,address,uint256)", "uint256", "uint256", "address", "bytes"],
        [market, 1000, 0, OWNER, b""],
    )

    call = decode_calldata(data, to=VAULT, value_wei=0, index=index)

    # KeeperHub's reshapeArgsForAbi/coerceTuple want a tuple as an object keyed by component
    # name (a nested array at the top level would be consumed as flat args). The names must
    # match the ABI entry we send alongside.
    first_input = call.abi[0]["inputs"][0]
    assert first_input["type"] == "tuple"
    assert [c["type"] for c in first_input["components"]] == ["address", "address", "address", "address", "uint256"]
    names = [c["name"] for c in first_input["components"]]
    assert all(names)
    assert call.function_args[0] == dict(zip(names, [USDC, VAULT, OWNER, OWNER, "860000000000000000"], strict=True))
    assert call.function_args[4] == "0x"


def test_named_abi_tuple_uses_the_abi_component_names(tmp_path: Path) -> None:
    abi = [
        {
            "type": "function",
            "name": "supply",
            "stateMutability": "nonpayable",
            "inputs": [
                {
                    "name": "marketParams",
                    "type": "tuple",
                    "components": [{"name": "loanToken", "type": "address"}, {"name": "lltv", "type": "uint256"}],
                },
                {"name": "assets", "type": "uint256"},
            ],
            "outputs": [],
        }
    ]
    (tmp_path / "morpho.json").write_text(json.dumps(abi))
    index = SelectorIndex.from_abi_directory(tmp_path)
    data = _calldata("supply((address,uint256),uint256)", ["(address,uint256)", "uint256"], [(USDC, 5), 7])

    call = decode_calldata(data, to=VAULT, value_wei=0, index=index)

    assert call.function_args == [{"loanToken": USDC, "lltv": "5"}, "7"]


def test_arrays_of_tuples_are_lists_of_objects() -> None:
    signature = "aggregate3((address,bool,bytes)[])"
    index = SelectorIndex.from_signatures([signature])
    data = _calldata(signature, ["(address,bool,bytes)[]"], [[(USDC, True, b"\x01"), (VAULT, False, b"")]])

    call = decode_calldata(data, to=VAULT, value_wei=0, index=index)

    components = call.abi[0]["inputs"][0]["components"]
    keys = [c["name"] for c in components]
    assert call.function_args == [
        [dict(zip(keys, [USDC, True, "0x01"], strict=True)), dict(zip(keys, [VAULT, False, "0x"], strict=True))]
    ]


def test_index_loads_abi_json_files_from_a_directory(tmp_path: Path) -> None:
    abi = [
        {
            "type": "function",
            "name": "swapExactTokensForTokens",
            "stateMutability": "nonpayable",
            "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "amountOutMin", "type": "uint256"}],
            "outputs": [],
        },
        {"type": "event", "name": "Swap", "inputs": []},
    ]
    (tmp_path / "router.json").write_text(json.dumps(abi))
    (tmp_path / "wrapped.json").write_text(json.dumps({"abi": abi}))

    index = SelectorIndex.from_abi_directory(tmp_path)
    data = _calldata("swapExactTokensForTokens(uint256,uint256)", ["uint256", "uint256"], [1, 2])
    call = decode_calldata(data, to=VAULT, value_wei=0, index=index)

    assert call.function_name == "swapExactTokensForTokens"
    # Named inputs from a real ABI are preserved instead of arg0/arg1.
    assert [i["name"] for i in call.abi[0]["inputs"]] == ["amountIn", "amountOutMin"]


def test_default_index_includes_almanak_connector_abis() -> None:
    index = SelectorIndex.default()
    # aerodrome/abis/router.json ships with the almanak package.
    selector = "0x" + function_signature_to_4byte_selector("approve(address,uint256)").hex()
    assert selector in index.selectors
    assert index.size > 200


def test_calldata_with_trailing_bytes_is_refused() -> None:
    data = _calldata("approve(address,uint256)", ["address", "uint256"], [VAULT, 1]) + "ff"
    with pytest.raises(UndecodableCalldata) as excinfo:
        decode_calldata(data, to=USDC, value_wei=0)
    assert "trailing" in str(excinfo.value)
