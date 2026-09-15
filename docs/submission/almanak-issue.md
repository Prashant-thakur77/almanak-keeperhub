# Issue for github.com/almanak-co/sdk

File at https://github.com/almanak-co/sdk/issues/new. Title, then the body. Attach the diff by
pasting it into the collapsed section at the end (it is `patches/almanak-execution-backend.diff`).

## Title

Proposal: let a plugin provide the execution stack for a wallet kind (`almanak.execution_backends`)

## Body

`almanak/framework/execution/interfaces.py` describes the execution layer as "enabling multiple
signing backends (local EOA, cloud KMS) and submission methods (public mempool, Flashbots, direct
RPC)", and `Signer`, `Submitter` and `Simulator` are clean ABCs. The gateway does not let anything
outside the package use them, though:

- `ExecutionServiceServicer._create_signer_from_resolved` (`almanak/gateway/services/execution_service.py`,
  main at f98b7896, line 683) knows the kinds `zodiac`, `direct` and `squads` and raises
  `Unknown wallet kind` for anything else, so a wallet registry plugin (the `almanak.wallets`
  entry-point group, which the gateway already discovers) can name a wallet but cannot say how it
  signs.
- `_get_orchestrator` (line 754) always constructs `PublicMempoolSubmitter` and the built-in
  simulator, whatever the signer is.
- The only other submitter that ships, `PrivateRelaySubmitter`, raises
  `SubmissionError("Private relay backend is not implemented in local-first stub")`.

So a signer that does not hold a private key locally (a custody API, a KMS, or an execution service
that signs, simulates and broadcasts as one unit) has no supported way in. I hit this integrating
KeeperHub as an execution backend (https://github.com/Prashant-thakur77/almanak-keeperhub): the
package implements all three ABCs, and the only way to get them into the gateway today is a
subclass of `ExecutionServiceServicer` that overrides those two methods.

### Proposal

One entry-point group, `almanak.execution_backends`, keyed by wallet kind, mirroring how
`almanak.wallets` already works. A plugin registered there exposes three factories:

```python
class MyBackend:
    def create_signer(self, wallet, *, settings) -> Signer: ...
    def create_submitter(self, signer, *, chain, rpc_url, settings) -> Submitter: ...
    def create_simulator(self, signer, *, chain, rpc_url, settings) -> Simulator: ...
```

`_create_signer_from_resolved` consults the group before raising `Unknown wallet kind`, and
`_get_orchestrator` asks the same backend for the submitter and simulator when the signer came
from one. The built-in kinds and the current simulator selection (`SIMULATION_BACKEND=rpc` or
not) are untouched when no plugin claims the kind, and a wallet kind with no plugin still fails
closed exactly as now.

The diff below applies to main at f98b7896 (`patch -p1`, no fuzz) and is what I run against
2.28.0 with the equivalent hunks. It is 76 lines, with the discovery helper being most of it.

### What it enables

With the group, a registry entry of kind `keeperhub` gets a complete execution stack: the
strategy is unchanged, `almanak strat run` compiles as today, and the plugin signs through a
custody API, dry-runs through it, broadcasts with an idempotency key, and hands back a normal
`TransactionReceipt`. The same mechanism would serve a KMS signer with the public mempool, or
a Flashbots submitter with the local signer, without either needing to subclass the servicer.

Happy to adjust the shape (a single `create_execution_stack` returning the three, or a class
attribute on the signer instead of `execution_backend`) if you prefer; the entry-point
discovery is the part that matters.

<details>
<summary>almanak-execution-backend.diff</summary>

```diff
--- a/almanak/gateway/services/execution_service.py
+++ b/almanak/gateway/services/execution_service.py
@@ -57,6 +57,28 @@
 
 logger = logging.getLogger(__name__)
 
+
+def _load_execution_backend(kind: str | None):
+    """Discover an execution backend plugin for a wallet ``kind`` via entry points.
+
+    Plugins register under the ``almanak.execution_backends`` group with the wallet kind as
+    the entry-point name and expose ``create_signer``, ``create_submitter`` and
+    ``create_simulator``. ``None`` when ``kind`` is empty or no plugin is installed, so the
+    built-in kinds (direct, zodiac, squads) keep their existing paths.
+    """
+    if not kind:
+        return None
+    try:
+        from importlib.metadata import entry_points
+
+        matches = [ep for ep in entry_points(group="almanak.execution_backends") if ep.name == kind]
+    except Exception:  # pragma: no cover - importlib.metadata is stdlib
+        return None
+    if not matches:
+        return None
+    backend = matches[0].load()
+    return backend() if isinstance(backend, type) else backend
+
 # TTL for cached compilers (5 minutes) - prevents stale price data in long-running services
 COMPILER_CACHE_TTL_SECONDS = 300
 GAS_POLICY_PRICE_MAX_AGE_SECONDS = 60
@@ -679,8 +701,12 @@
             return LocalKeySigner(private_key=pk)
         elif kind == "squads":
             raise NotImplementedError("Squads multisig wallet support is not yet implemented")
-        else:
-            raise ValueError(f"Unknown wallet kind: {kind}")
+        backend = _load_execution_backend(kind)
+        if backend is not None:
+            signer = backend.create_signer(wallet, settings=self.settings)
+            logger.info("Using %s for resolved wallet %s (kind=%s)", type(signer).__name__, wallet.account_address[:10], kind)
+            return signer
+        raise ValueError(f"Unknown wallet kind: {kind}")
 
     def _persist_execution_timeline_event(self, event: Any) -> None:
         from almanak.gateway.services.observe_service import persist_timeline_event
@@ -751,7 +777,6 @@
                 ) from e
         if signer is None:
             signer = self._create_signer(wallet_address)
-        submitter = PublicMempoolSubmitter(rpc_url=rpc_url, chain=chain)
         from almanak.framework.execution.simulator.config import SimulationConfig
 
         simulation_config = SimulationConfig.from_env()
@@ -760,13 +785,20 @@
         from almanak.core.rpc_network import Network
         from almanak.gateway.services.venue_verification_gateway import GatewayRpcVenueVerificationGateway
 
-        if simulation_config.backend == "rpc":
+        backend = _load_execution_backend(getattr(signer, "execution_backend", None))
+        if backend is not None:
+            # A pluggable backend owns submission and simulation for its signer.
+            submitter = backend.create_submitter(signer, chain=chain, rpc_url=rpc_url, settings=self.settings)
+            simulator = backend.create_simulator(signer, chain=chain, rpc_url=rpc_url, settings=self.settings)
+        elif simulation_config.backend == "rpc":
             from almanak.gateway.services.rpc_simulator import create_gateway_simulator
 
+            submitter = PublicMempoolSubmitter(rpc_url=rpc_url, chain=chain)
             simulator = create_gateway_simulator(
                 config=simulation_config, rpc_url=rpc_url, chain=chain, network=Network.parse(network)
             )
         else:
+            submitter = PublicMempoolSubmitter(rpc_url=rpc_url, chain=chain)
             simulator = create_simulator(config=simulation_config, rpc_url=rpc_url)
 
         orchestrator = ExecutionOrchestrator(
```

</details>
