# YouTube upload

**Visibility:** Unlisted (the DoraHacks form takes the link; unlisted keeps it off search
until judging).

**Title**

    Almanak strategies, executed by KeeperHub - Agent Economy Hackathon

**Description**

    Almanak decides. KeeperHub lands it. The strategy code never changes.

    Almanak's execution layer is three abstract classes - Signer, Submitter and
    Simulator. This project implements all three against KeeperHub, so an unmodified
    Almanak strategy executes through KeeperHub's enclave wallet, spending caps,
    simulation and audit log, with gas sponsored and no private key in the strategy
    process. The only change to Almanak's own demo strategy is one line adding Base
    Sepolia to its supported chains.

    Everything shown is a real run on Base Sepolia:

    approve  0x29dd40a6db7016bf0b...  exec az13hw7qn9y9dhs52s4rg  verified, sponsored
    deposit  0x70b453be43f4b8c4d4...  exec au5z8vtzv8s9xm811z93j  verified, sponsored
    redeem   0x91777e39d4fc1748f6...  exec 7rshlqcgwoxkia3iz052b  verified, sponsored
    vault    0xd36E12a5b2926A5cbE6B4DE42a0D60Fd35d3cb04

    Measured, not claimed: 50/50 impossible deposits refused before broadcast, 200/200
    executions landed and verified with a p50 of 7.8s to a verified receipt, 50/50 retries
    of landed work replayed by idempotency key with zero double broadcasts, 10/10 processes
    killed after broadcast and settled by a fresh process, 576 unit and property tests, and
    11 documented API behaviours checked against production every six hours.

    Seven failure modes are recorded in the repo: unknown selector, would-revert, over the
    spend cap, duplicate blocked by an idempotency key bound to intent, RPC outage, a
    process killed mid-bundle that resumes from the receipts log without broadcasting
    twice, and a stale exit KeeperHub refuses after re-reading the position.

    The proof keeps itself current: a GitHub workflow runs a full lifecycle through
    KeeperHub every six hours and republishes the console, and three real Claude sessions
    over the project's MCP server are recorded unedited.
      Console: https://prashant-thakur77.github.io/almanak-keeperhub/

    Four issues filed against KeeperHub itself, all accepted by the maintainers, plus three of
    their own accepted issues taken from the backlog. Seven pull requests, three merged and live
    on production:
      #2426 raw calldata on POST /api/execute/contract-call   -> PR #2449, merged
      #2427 simulating a sequence against carried state       -> PR #2452, merged
      #2428 the acting wallet on sponsored executions         -> PR #2450, merged
      #2519 workflow preflight against carried state          -> PR #2531, in review
      #2367 approve hint without a redundancy claim           -> PR #2533, in review
      #2497 the EVM chain runbook and a seed guard            -> PR #2532, in review
      #2496 fixed-point aggregation in the math plugin        -> PR #2534, in review

    Repo: https://github.com/Prashant-thakur77/almanak-keeperhub
    Built for the KeeperHub Agent Economy Hackathon.

    Music: "Inspired" by Kevin MacLeod (incompetech.com)
    Licensed under Creative Commons: By Attribution 4.0
    https://creativecommons.org/licenses/by/4.0/

**Chapters** (final runtime 4:20)

    0:00 Almanak decides, KeeperHub lands it
    0:05 Where agent frameworks break
    0:22 The seam: Signer, Submitter, Simulator
    0:38 What the strategy inherits
    0:49 Live on Base Sepolia
    1:18 From the operator's phone
    2:43 A stale exit, refused
    2:59 The guarded exit
    3:23 Judged on what goes wrong
    3:47 The numbers
    4:15 Close
