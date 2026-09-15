# Upstream proposal for Almanak

`almanak-execution-backend.diff` applies to `almanak/gateway/services/execution_service.py`
on the `main` branch of github.com/almanak-co/sdk as of 15 Sep 2026 (commit f98b7896;
`patch -p1 --dry-run` is clean). It adds one entry-point group,
`almanak.execution_backends`, keyed by wallet kind. A plugin registered there provides
`create_signer`, `create_submitter` and `create_simulator`, so a wallet registry entry of
kind `keeperhub` (or any future backend) gets a complete execution stack instead of
failing with "Unknown wallet kind". The built-in kinds (`direct`, `zodiac`, `squads`) and
the simulator selection are untouched when no plugin claims the kind.

This package already registers the group in `pyproject.toml`:

```toml
[project.entry-points."almanak.execution_backends"]
keeperhub = "almanak_keeperhub.backend:KeeperHubBackend"
```

Almanak ignores an unknown entry-point group, so this is inert today. Once the patch
lands, `almanak_keeperhub/gateway.py` (the servicer subclass that wires the same three
objects in from the outside) becomes unnecessary.

Almanak's public repository is a mirror of a private monorepo and takes no pull
requests, so this is offered as an issue carrying the diff:
`docs/submission/almanak-issue.md`. Two bugs found on the way are in
`docs/almanak-feedback.md`; both were fixed upstream between 2.28.0 and main.
