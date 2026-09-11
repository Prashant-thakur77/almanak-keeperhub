"""Turn Almanak's raw ``data`` into the ``functionName`` + ``functionArgs`` + ``abi`` KeeperHub needs.

KeeperHub's contract-call endpoint has no raw-calldata write, so the selector
is looked up in an offline index built from (1) the ABI JSON files shipped
inside the ``almanak`` package and (2) a curated list in ``signatures.json``.
Anything else is refused: this package never guesses what a transaction does.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import function_signature_to_4byte_selector, to_checksum_address

from almanak_keeperhub.errors import UndecodableCalldata

_BUNDLED = Path(__file__).with_name("signatures.json")


@dataclass(frozen=True)
class FunctionSig:
    name: str
    inputs: list[dict[str, Any]]  # ABI input entries (type, name, components)

    @property
    def types(self) -> list[str]:
        return [_canonical_type(entry) for entry in self.inputs]

    @property
    def signature(self) -> str:
        return f"{self.name}({','.join(self.types)})"


@dataclass(frozen=True)
class DecodedCall:
    function_name: str
    signature: str
    function_args: list[Any]
    abi: list[dict[str, Any]]


class SelectorIndex:
    def __init__(self) -> None:
        self._by_selector: dict[str, FunctionSig] = {}

    @property
    def selectors(self) -> set[str]:
        return set(self._by_selector)

    @property
    def size(self) -> int:
        return len(self._by_selector)

    def add(self, sig: FunctionSig) -> None:
        selector = "0x" + function_signature_to_4byte_selector(sig.signature).hex()
        # A real ABI (named inputs) wins over a bare signature for the same selector.
        existing = self._by_selector.get(selector)
        if existing is None or _is_bare(existing) and not _is_bare(sig):
            self._by_selector[selector] = sig

    def add_abi(self, abi: list[Any]) -> None:
        for entry in abi:
            if isinstance(entry, dict) and entry.get("type") == "function":
                inputs = [dict(i) for i in entry.get("inputs", []) if isinstance(i, dict)]
                self.add(FunctionSig(name=str(entry["name"]), inputs=inputs))

    def lookup(self, selector: str) -> FunctionSig | None:
        return self._by_selector.get(selector.lower())

    @classmethod
    def from_signatures(cls, signatures: list[str]) -> SelectorIndex:
        index = cls()
        for text in signatures:
            index.add(_parse_signature(text))
        return index

    @classmethod
    def from_abi_directory(cls, root: Path) -> SelectorIndex:
        index = cls()
        index.merge_abi_directory(root)
        return index

    def merge_abi_directory(self, root: Path) -> None:
        for path in sorted(root.rglob("*.json")):
            try:
                document = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            abi = (
                document if isinstance(document, list) else document.get("abi") if isinstance(document, dict) else None
            )
            if isinstance(abi, list):
                self.add_abi(abi)

    def merge_signatures(self, signatures: list[str]) -> None:
        for text in signatures:
            self.add(_parse_signature(text))

    @classmethod
    @lru_cache(maxsize=1)
    def default(cls) -> SelectorIndex:
        index = cls()
        index.merge_signatures(json.loads(_BUNDLED.read_text())["signatures"])
        try:
            import almanak

            index.merge_abi_directory(Path(almanak.__file__).parent)
        except ImportError:  # pragma: no cover - almanak is a hard dependency
            pass
        return index


def decode_calldata(data: str, *, to: str, value_wei: int, index: SelectorIndex | None = None) -> DecodedCall:
    raw = bytes.fromhex(data[2:] if data.startswith("0x") else data)
    if len(raw) < 4:
        raise UndecodableCalldata(selector=data if data else "0x", to=to)
    selector = "0x" + raw[:4].hex()
    sig = (index or SelectorIndex.default()).lookup(selector)
    if sig is None:
        raise UndecodableCalldata(selector=selector, to=to)
    values = abi_decode(sig.types, raw[4:]) if sig.types else ()
    # eth_abi ignores trailing bytes; a suffix that would not survive re-encoding is refused
    # rather than silently dropped from what KeeperHub simulates and sends.
    if abi_encode(sig.types, list(values)) != raw[4:]:
        raise UndecodableCalldata(selector=f"{selector} (trailing or malformed argument bytes)", to=to)
    inputs = [_named_input(entry, f"arg{i}") for i, entry in enumerate(sig.inputs)]
    entry = {
        "type": "function",
        "name": sig.name,
        "inputs": inputs,
        "outputs": [],
        "stateMutability": "payable" if value_wei > 0 else "nonpayable",
    }
    return DecodedCall(
        function_name=sig.name,
        signature=sig.signature,
        function_args=[_json_value(value, spec) for value, spec in zip(values, inputs, strict=True)],
        abi=[entry],
    )


# --- helpers -----------------------------------------------------------------


def _is_bare(sig: FunctionSig) -> bool:
    return all(entry.get("name", "").startswith("arg") for entry in sig.inputs)


def _canonical_type(entry: dict[str, Any]) -> str:
    typ = str(entry["type"])
    if typ.startswith("tuple"):
        inner = ",".join(_canonical_type(c) for c in entry.get("components", []))
        return f"({inner}){typ[len('tuple') :]}"
    return typ


def _parse_signature(text: str) -> FunctionSig:
    match = re.fullmatch(r"\s*([A-Za-z_$][A-Za-z0-9_$]*)\((.*)\)\s*", text)
    if not match:
        raise ValueError(f"not a function signature: {text!r}")
    name, params = match.group(1), match.group(2)
    inputs = [_type_to_abi_input(t, f"arg{i}") for i, t in enumerate(_split_top_level(params))]
    return FunctionSig(name=name, inputs=inputs)


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ""
    for ch in text:
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        current += ch
    if current.strip():
        parts.append(current)
    return [p.strip() for p in parts if p.strip()]


def _type_to_abi_input(typ: str, name: str) -> dict[str, Any]:
    if typ.startswith("("):
        close = _matching_paren(typ)
        inner, suffix = typ[1:close], typ[close + 1 :]
        components = [_type_to_abi_input(t, f"c{i}") for i, t in enumerate(_split_top_level(inner))]
        return {"name": name, "type": f"tuple{suffix}", "components": components}
    return {"name": name, "type": typ}


def _matching_paren(text: str) -> int:
    depth = 0
    for i, ch in enumerate(text):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0:
            return i
    raise ValueError(f"unbalanced tuple type: {text!r}")


def _named_input(entry: dict[str, Any], fallback: str) -> dict[str, Any]:
    """Copy of an ABI input with every (nested) component named.

    KeeperHub reshapes tuple arguments into objects keyed by component name, so the ABI we
    send and the argument objects we render must agree on the names; empty names are filled.
    """
    named = dict(entry)
    if not named.get("name"):
        named["name"] = fallback
    if str(named.get("type", "")).startswith("tuple"):
        named["components"] = [
            _named_input(component, f"c{i}") for i, component in enumerate(named.get("components", []))
        ]
    return named


def _json_value(value: Any, spec: dict[str, Any]) -> Any:
    """Render one decoded value the way KeeperHub's ``functionArgs`` accepts it.

    Integers as decimal strings, bytes as hex, booleans as booleans, arrays as lists, and
    tuples as objects keyed by component name (``reshapeArgsForAbi`` / ``coerceTuple`` in
    KeeperHub's lib/abi/struct-args.ts; a nested array at the top level would be consumed
    as flat arguments).
    """
    typ = str(spec["type"])
    if typ.endswith("]"):
        base = {**spec, "type": typ[: typ.rindex("[")]}
        return [_json_value(v, base) for v in value]
    if typ == "tuple":
        components = spec.get("components", [])
        return {c["name"]: _json_value(v, c) for c, v in zip(components, value, strict=True)}
    if typ == "address":
        return to_checksum_address(value)
    if typ == "bool":
        return bool(value)
    if typ.startswith(("uint", "int")):
        return str(int(value))
    if isinstance(value, bytes | bytearray):
        return "0x" + bytes(value).hex()
    return value
