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

    Measured, not claimed: 20/20 unsafe calls refused before broadcast, 10/10 dry runs
    caught, 5/5 transactions landed with a p50 of 6.88s to a verified receipt, a retry
    of already-landed work replayed rather than resent, 130 unit tests.

    Six failure modes are recorded in the repo: unknown selector, would-revert, over the
    spend cap, duplicate blocked by an idempotency key bound to intent, RPC outage, and
    a process killed mid-bundle that resumes from the receipts log without broadcasting
    twice.

    Three issues filed against KeeperHub itself, all accepted by the maintainers, all
    three open as pull requests:
      #2426 raw calldata on POST /api/execute/contract-call   -> PR #2449
      #2428 the acting wallet on sponsored executions         -> PR #2450
      #2427 simulating a sequence against carried state       -> PR open

    Repo: https://github.com/Prashant-thakur77/almanak-keeperhub
    Built for the KeeperHub Agent Economy Hackathon.

    Music: "Inspired" by Kevin MacLeod (incompetech.com)
    Licensed under Creative Commons: By Attribution 4.0
    https://creativecommons.org/licenses/by/4.0/

**Chapters** (paste into the description once the final runtime is confirmed)

    0:00 Almanak decides, KeeperHub lands it
    0:05 Where agent frameworks break
    0:21 The seam: Signer, Submitter, Simulator
    0:38 What the strategy inherits
    0:49 Live on Base Sepolia
    1:15 Judged on what goes wrong
    1:40 Receipts
    1:56 Close
