# Security model

What an attacker, a bug, or a misbehaving agent can and cannot make this package do. Every claim
below is either enforced by code in this repository, by KeeperHub, or by Almanak, and each row says
which.

## Assets

- The organization wallet's funds (USDC and the vault position). The wallet's key never exists
  outside KeeperHub's Turnkey enclave; this package holds no key material of any kind.
- The KeeperHub API key. It is the only secret on the machine. Scope `mcp:write` broadcasts;
  `mcp:read` cannot.
- The receipts log. It is the resume table after a crash and the proof of what happened; losing
  or corrupting it is an integrity failure even when no funds move.

## Who can act, and through which gate

| Actor | Can propose | Can compile | Can sign or broadcast | Enforced by |
|---|---|---|---|---|
| The strategy (`decide()`) | yes | via Almanak's compiler | no | Almanak: intents only |
| Almanak's AI agent (`ax`) | yes | only within its policy (trade limits, allowlists) | no | Almanak policy engine |
| This package | no | no | asks KeeperHub to, with one idempotency key per piece of work | `KeeperHubSigner` (no cryptography), `KeeperHubSubmitter` |
| An MCP client on the default server | no | dry runs only | no: `run_tick` is not registered | `almanak-keeperhub mcp` without `--write` |
| An MCP client on the `--write` server | one real tick per explicit `confirm=true` | | through the same KeeperHub gates | `StrategyTools.run_tick` |
| The Telegram bot | dry runs; a real tick only from the owner chat | | same | `bot.py` owner check |
| KeeperHub | no | no | signs inside Turnkey only after its own dry run and caps pass | KeeperHub spending caps, stablecoin ceiling, simulate |

Nothing any actor above says can become a transaction without passing KeeperHub's dry run and caps,
and no actor above can widen those caps: they are organization settings on KeeperHub's side.

## Refusals this package makes itself, before KeeperHub sees anything

- Calldata whose selector is not in the local index, on a KeeperHub without raw calldata support:
  refused as a `SigningError`. With raw calldata support, the bytes go to KeeperHub, which decodes
  them against the contract's verified ABI and refuses what it cannot decode. In neither case is a
  function signature guessed.
- Calldata that would not survive a lossless re-encode (trailing bytes, an appended ERC-2771
  sender, non-canonical padding): refused, because the transaction KeeperHub sends is rebuilt from
  the decoded call, not from the bytes. Property-tested over every indexed signature.
- A transaction whose `from` is not the organization wallet: refused.
- A contract creation (`to` empty): refused.
- A dry run that reverts, or that KeeperHub could not run: the tick stops before anything is
  signed. "Could not simulate" is a failure, not a pass.

## Double spend

The incident this integration exists to prevent is a retry that broadcasts landed work again.
The idempotency key is derived from the chain, the sender, the target, the calldata, the value and
Almanak's intent id, and deliberately not from the nonce or gas, so every attempt at the same
work carries the same key and KeeperHub replays the first execution. Verified live: 50 retries
of landed approvals replayed with the same hash and zero new broadcasts; 10 processes killed after
broadcast were settled by a fresh process from the receipts log and their retries replayed
(`docs/benchmark.md`). A key reused with a different body is a `409 idempotency_conflict` and the
submitter never retries it.

The residual risk, stated in the README: a strategy that crashes before persisting its state and
decides again produces a new intent id, which is new work by construction. Neither KeeperHub nor
this package can tell that apart from a genuinely new decision.

## What a stolen API key can do

Everything the organization's KeeperHub caps allow, until it is revoked in KeeperHub. It cannot
extract the wallet's key, raise the caps, or bypass the dry run. Rotate the key in KeeperHub's
settings; nothing in this repository needs to change. The key is read from the environment only,
is never logged (the `doctor` command prints six characters), and is not written to the receipts
log or the console export.

## What this package does not protect against

- A malicious or buggy strategy that decides a bad but valid action within policy and caps.
  Almanak's policy layer and KeeperHub's caps bound the damage; they do not judge intent.
- A compromised machine that edits the receipts log to forget a broadcast. The log is a local
  file; KeeperHub's execution record is the authority, which is why `verify` asks KeeperHub.
- Front-running or reordering between the transactions of a bundle. They are separate
  transactions; the sequence dry run says so in `atomic: false`.

## Reporting

Open an issue in this repository. For anything involving KeeperHub's platform, follow
KeeperHub's own security policy.
