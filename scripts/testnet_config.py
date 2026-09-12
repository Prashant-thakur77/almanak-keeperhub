"""Write the deployed TestVault address into the Base Sepolia demo config.

    python scripts/testnet_config.py 0xYourVaultAddress
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "demos" / "metamorpho_base_sepolia" / "config.json"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].startswith("0x") or len(argv[1]) != 42:
        print(__doc__)
        return 2
    config = json.loads(CONFIG.read_text())
    config["vault_address"] = argv[1]
    CONFIG.write_text(json.dumps(config, indent=4) + "\n")
    print(f"{CONFIG}: vault_address = {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
