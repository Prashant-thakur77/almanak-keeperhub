# Upstream proposal for Almanak

`almanak-execution-backend.diff` applies to `almanak==2.28.0`
(`almanak/gateway/services/execution_service.py`). It adds one entry-point group,
`almanak.execution_backends`, keyed by wallet kind. A plugin registered there
provides `create_signer`, `create_submitter` and `create_simulator`, so a wallet
registry entry of kind `keeperhub` (or any future backend) gets a complete
execution stack instead of failing with "Unknown wallet kind". The built-in
kinds (`direct`, `zodiac`, `squads`) are untouched.

With the patch applied, this package would register:

```toml
[project.entry-points."almanak.execution_backends"]
keeperhub = "almanak_keeperhub.backend:KeeperHubBackend"
```

and `almanak_keeperhub/gateway.py` (the servicer subclass) becomes unnecessary.

Almanak's public repository (github.com/almanak-co/sdk) is a mirror of a private
monorepo with no pull requests, so this is offered as a diff plus an issue rather
than a PR. Two related bugs are described in `docs/almanak-feedback.md`.
