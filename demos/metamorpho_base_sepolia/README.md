# metamorpho_base_yield on Base Sepolia (the free path)

The same packaged Almanak demo strategy, run on Base Sepolia through KeeperHub with sponsored gas and faucet USDC. Two differences from `../metamorpho_base_yield`, both forced by the fact that Almanak ships no testnet chain:

1. `strategy.py`: `supported_chains=["base", "base_sepolia"]` (one line; the original declares `["base"]` and ignores the config chain when it declares exactly one).
2. `config.json`: `chain` is `base_sepolia`, the vault is `contracts/TestVault.sol` deployed on Base Sepolia (a 1:1 ERC-4626 over Circle's test USDC), the token funding entry points at test USDC, and `min_apy_floor` is 0 because there is no Morpho Blue rate to read on Sepolia (a missing APY read allows entry and holds on exit, so the exit tick is mainnet-only).

`almanak_keeperhub/testnet.py` registers `base_sepolia` (chain id 84532) as a first-class Almanak chain so every compiled transaction carries the testnet chain id; KeeperHub then executes on Base Sepolia. Deploy the vault once with `scripts/deploy_test_vault.sh` and put its address in `config.json` (or run `scripts/testnet_config.py <vault>`).
