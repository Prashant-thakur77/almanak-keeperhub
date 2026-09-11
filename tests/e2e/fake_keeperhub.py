"""A local stand-in for KeeperHub's Direct Execution API, for rehearsals without an account.

THIS IS A TEST DOUBLE. It mirrors the documented request/response shapes of
``docs/api/direct-execution.md`` (simulate, Idempotency-Key replay/conflict,
202 envelope, status with receipts and X-Poll-Interval-Hint, the 100 USD
stablecoin cap) and signs with a throwaway Anvil key against a local fork.
Nothing here is evidence of execution through KeeperHub; the real proof links
in docs/receipts.json come from app.keeperhub.com.

Run:  python tests/e2e/fake_keeperhub.py --rpc http://127.0.0.1:8547 --port 8790
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from eth_account import Account
from web3 import HTTPProvider, Web3

ANVIL_KEY_0 = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
STABLECOINS = {8453: {"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", 6)}}
CAP_USD = 100


class State:
    def __init__(self, rpc: str, private_key: str, chain_id: int) -> None:
        self.web3 = Web3(HTTPProvider(rpc))
        self.account = Account.from_key(private_key)
        self.chain_id = chain_id
        self.executions: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, tuple[str, str]] = {}  # key -> (body digest, execution id)
        self.lock = threading.Lock()


def _encode_call(state: State, body: dict[str, Any]) -> tuple[str, bytes, int]:
    abi = json.loads(body["abi"]) if isinstance(body.get("abi"), str) else body.get("abi")
    contract = state.web3.eth.contract(address=Web3.to_checksum_address(body["contractAddress"]), abi=abi)
    args = json.loads(body.get("functionArgs") or "[]")
    fn = contract.get_function_by_name(body["functionName"])
    typed_args = [_coerce(a, spec) for a, spec in zip(args, fn.abi["inputs"], strict=True)]
    data = contract.encode_abi(body["functionName"], args=typed_args)
    value_wei = int(Web3.to_wei(body["value"], "ether")) if body.get("value") else 0
    return body["contractAddress"], bytes.fromhex(data[2:]), value_wei


def _coerce(value: Any, spec: dict[str, Any]) -> Any:
    """Mirror KeeperHub's coerceArgsForAbi: tuples arrive as objects keyed by component name."""
    typ = str(spec["type"])
    if typ.endswith("]"):
        return [_coerce(v, {**spec, "type": typ[: typ.rindex("[")]}) for v in value]
    if typ == "tuple":
        components = spec.get("components", [])
        if isinstance(value, dict):
            return tuple(_coerce(value[c["name"]], c) for c in components)
        return tuple(_coerce(v, c) for v, c in zip(value, components, strict=True))
    if typ.startswith(("uint", "int")):
        return int(value)
    if typ.startswith("bytes") and isinstance(value, str):
        return bytes.fromhex(value[2:])
    return value


def _stablecoin_refusal(state: State, body: dict[str, Any]) -> str | None:
    token = str(body["contractAddress"]).lower()
    meta = STABLECOINS.get(state.chain_id, {}).get(token)
    if not meta or body["functionName"] not in ("transfer", "approve"):
        return None
    symbol, decimals = meta
    amount = int(json.loads(body.get("functionArgs") or "[]")[1])
    if amount > CAP_USD * 10**decimals:
        human = amount / 10**decimals
        return f"Stablecoin transfer of {human:g} {symbol} exceeds the {CAP_USD:.1f} USD per-transaction limit"
    return None


class Handler(BaseHTTPRequestHandler):
    state: State

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter
        print(f"[fake-keeperhub] {self.command} {self.path} {args[1] if len(args) > 1 else ''}")

    def _send(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def _auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer kh_"):
            self._send(401, {"error": "Invalid or missing API key"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._auth():
            return
        if self.path == "/api/user":
            self._send(
                200,
                {
                    "id": "fake-user",
                    "email": "fake@wallet.keeperhub.com",
                    "walletAddress": self.state.account.address,
                },
            )
            return
        if self.path == "/api/chains":
            self._send(
                200,
                [
                    {
                        "chainId": self.state.chain_id,
                        "name": "fake-fork",
                        "isEnabled": True,
                        "isTestnet": False,
                    }
                ],
            )
            return
        if self.path.startswith("/api/execute/") and self.path.endswith("/status"):
            execution_id = self.path.split("/")[3]
            execution = self.state.executions.get(execution_id)
            if execution is None:
                self._send(404, {"error": "execution not found"})
                return
            terminal = execution["status"] in ("completed", "failed")
            self._send(200, execution, {"X-Poll-Interval-Hint": "0" if terminal else "2"})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._auth():
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/api/execute/contract-call":
            self._send(404, {"error": "not found"})
            return
        if "simulate" in body and not isinstance(body["simulate"], bool):
            self._send(400, {"error": "simulate must be a boolean"})
            return
        try:
            to, data, value_wei = _encode_call(self.state, body)
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": f"Invalid request: {exc}"})
            return
        refusal = _stablecoin_refusal(self.state, body)
        if body.get("simulate"):
            self._simulate(to, data, value_wei, refusal)
            return
        key = self.headers.get("Idempotency-Key")
        if not key:
            self._send(400, {"error": "Idempotency-Key header required by this fake for writes"})
            return
        self._execute(key, body, to, data, value_wei, refusal)

    def _simulate(self, to: str, data: bytes, value_wei: int, refusal: str | None) -> None:
        sender = self.state.account.address
        if refusal:
            self._send(
                400,
                {
                    "success": False,
                    "status": "simulated",
                    "from": sender,
                    "to": to,
                    "failureKind": "validation",
                    "wouldRevert": True,
                    "revertReason": refusal,
                    "error": refusal,
                },
            )
            return
        tx = {"from": sender, "to": Web3.to_checksum_address(to), "data": data, "value": value_wei}
        try:
            gas = self.state.web3.eth.estimate_gas(tx)
            self.state.web3.eth.call(tx)
        except Exception as exc:  # noqa: BLE001
            reason = str(exc)
            self._send(
                400,
                {
                    "success": False,
                    "status": "simulated",
                    "from": sender,
                    "to": to,
                    "value": str(value_wei),
                    "failureKind": "revert",
                    "wouldRevert": True,
                    "revertReason": reason,
                    "error": reason,
                },
            )
            return
        self._send(
            200,
            {
                "success": True,
                "status": "simulated",
                "from": sender,
                "to": to,
                "value": str(value_wei),
                "gasEstimate": str(gas),
                "simulatedReturnValue": None,
                "wouldRevert": False,
            },
        )

    def _execute(
        self, key: str, body: dict[str, Any], to: str, data: bytes, value_wei: int, refusal: str | None
    ) -> None:
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with self.state.lock:
            seen = self.state.idempotency.get(key)
            if seen and seen[0] != digest:
                self._send(
                    409,
                    {
                        "error": "Idempotency-Key reused with a different body",
                        "code": "idempotency_conflict",
                        "retryable": False,
                        "originalExecutionId": seen[1],
                    },
                )
                return
            if seen:
                replay = dict(self.state.executions[seen[1]])
                replay["idempotentReplay"] = True
                self._send(
                    202,
                    {
                        k: replay.get(k)
                        for k in (
                            "executionId",
                            "status",
                            "transactionHash",
                            "transactionLink",
                            "error",
                            "idempotentReplay",
                        )
                    },
                )
                return
            execution_id = uuid.uuid4().hex[:21]
            self.state.idempotency[key] = (digest, execution_id)
            if refusal:
                execution = {
                    "executionId": execution_id,
                    "status": "failed",
                    "type": "contract-call",
                    "network": str(self.state.chain_id),
                    "transactionHash": None,
                    "transactionLink": None,
                    "receipts": [],
                    "error": refusal,
                    "sponsored": False,
                }
                self.state.executions[execution_id] = execution
                self._send(202, {"executionId": execution_id, "status": "failed", "error": refusal})
                return
            execution = {
                "executionId": execution_id,
                "status": "running",
                "type": "contract-call",
                "network": str(self.state.chain_id),
                "transactionHash": None,
                "transactionLink": None,
                "receipts": [],
                "error": None,
                "sponsored": False,
            }
            self.state.executions[execution_id] = execution
        web3 = self.state.web3
        sender = self.state.account.address
        tx = {
            "from": sender,
            "to": Web3.to_checksum_address(to),
            "data": data,
            "value": value_wei,
            "chainId": self.state.chain_id,
            "nonce": web3.eth.get_transaction_count(sender, "pending"),
        }
        try:
            tx["gas"] = int(web3.eth.estimate_gas(tx) * 1.3)
            tx["maxFeePerGas"] = web3.eth.gas_price * 2
            tx["maxPriorityFeePerGas"] = web3.to_wei(1, "gwei")
            signed = self.state.account.sign_transaction(tx)
            tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction).hex()
            tx_hash = tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash
        except Exception as exc:  # noqa: BLE001
            execution.update(status="failed", error=f"Contract call failed: {exc}")
            self._send(202, {"executionId": execution_id, "status": "failed", "error": execution["error"]})
            return
        execution.update(
            transactionHash=tx_hash,
            transactionLink=f"https://fake.explorer/tx/{tx_hash}",
            status="unconfirmed",
        )
        deadline = time.time() + 20
        receipt = None
        while time.time() < deadline and receipt is None:
            try:
                receipt = web3.eth.get_transaction_receipt(tx_hash)
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        if receipt is None:
            self._send(
                202,
                {
                    "executionId": execution_id,
                    "status": "unconfirmed",
                    "transactionHash": tx_hash,
                    "transactionLink": execution["transactionLink"],
                },
            )
            return
        ok = receipt["status"] == 1
        execution.update(
            status="completed" if ok else "failed",
            error=None if ok else "execution reverted",
            receipts=[
                {
                    "hash": tx_hash,
                    "chainId": self.state.chain_id,
                    "verified": True,
                    "receiptStatus": "success" if ok else "reverted",
                    "blockNumber": receipt["blockNumber"],
                    "gasUsed": str(receipt["gasUsed"]),
                }
            ],
            gasUsedWei=str(receipt["gasUsed"] * receipt["effectiveGasPrice"]),
        )
        payload = {
            "executionId": execution_id,
            "status": execution["status"],
            "transactionHash": tx_hash,
            "transactionLink": execution["transactionLink"],
        }
        if not ok:
            payload["error"] = "execution reverted"
        self._send(202, payload)


def serve(rpc: str, port: int, private_key: str, chain_id: int) -> ThreadingHTTPServer:
    Handler.state = State(rpc, private_key, chain_id)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[fake-keeperhub] listening on http://127.0.0.1:{port} wallet={Handler.state.account.address} rpc={rpc}")
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", default="http://127.0.0.1:8547")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--private-key", default=ANVIL_KEY_0)
    parser.add_argument("--chain-id", type=int, default=8453)
    args = parser.parse_args()
    serve(args.rpc, args.port, args.private_key, args.chain_id)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
