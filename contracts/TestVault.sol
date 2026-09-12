// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title TestVault: a minimal ERC-4626 vault over any ERC-20, for Base Sepolia rehearsals.
/// @notice Stands in for the Moonwell Flagship USDC MetaMorpho vault the Almanak demo
///         strategy targets on Base mainnet. Shares are 1:1 with assets (no yield), which is
///         all the deposit/redeem lifecycle needs. Not audited; test networks only.
interface IERC20 {
    function totalSupply() external view returns (uint256);
    function balanceOf(address) external view returns (uint256);
    function transfer(address, uint256) external returns (bool);
    function transferFrom(address, address, uint256) external returns (bool);
    function decimals() external view returns (uint8);
}

contract TestVault {
    string public name;
    string public symbol;
    uint8 public immutable decimals;
    IERC20 public immutable asset_;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event Deposit(address indexed sender, address indexed owner, uint256 assets, uint256 shares);
    event Withdraw(address indexed sender, address indexed receiver, address indexed owner, uint256 assets, uint256 shares);

    constructor(address underlying, string memory name_, string memory symbol_) {
        asset_ = IERC20(underlying);
        name = name_;
        symbol = symbol_;
        decimals = IERC20(underlying).decimals();
    }

    // ---- ERC-20 (shares) ----
    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transfer(address to, uint256 value) external returns (bool) {
        _move(msg.sender, to, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) allowance[from][msg.sender] = allowed - value;
        _move(from, to, value);
        return true;
    }

    // ---- ERC-4626 ----
    function asset() external view returns (address) { return address(asset_); }
    function totalAssets() public view returns (uint256) { return asset_.balanceOf(address(this)); }

    function convertToShares(uint256 assets) public view returns (uint256) {
        uint256 supply = totalSupply;
        return supply == 0 ? assets : assets * supply / totalAssets();
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        uint256 supply = totalSupply;
        return supply == 0 ? shares : shares * totalAssets() / supply;
    }

    function maxDeposit(address) external pure returns (uint256) { return type(uint256).max; }
    function maxMint(address) external pure returns (uint256) { return type(uint256).max; }
    function maxWithdraw(address owner) external view returns (uint256) { return convertToAssets(balanceOf[owner]); }
    function maxRedeem(address owner) external view returns (uint256) { return balanceOf[owner]; }
    function previewDeposit(uint256 assets) external view returns (uint256) { return convertToShares(assets); }
    function previewMint(uint256 shares) external view returns (uint256) { return convertToAssets(shares); }
    function previewWithdraw(uint256 assets) external view returns (uint256) { return convertToShares(assets); }
    function previewRedeem(uint256 shares) external view returns (uint256) { return convertToAssets(shares); }

    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        shares = convertToShares(assets);
        require(shares != 0, "zero shares");
        require(asset_.transferFrom(msg.sender, address(this), assets), "transfer failed");
        _mint(receiver, shares);
        emit Deposit(msg.sender, receiver, assets, shares);
    }

    function mint(uint256 shares, address receiver) external returns (uint256 assets) {
        assets = convertToAssets(shares);
        require(asset_.transferFrom(msg.sender, address(this), assets), "transfer failed");
        _mint(receiver, shares);
        emit Deposit(msg.sender, receiver, assets, shares);
    }

    function withdraw(uint256 assets, address receiver, address owner) external returns (uint256 shares) {
        shares = convertToShares(assets);
        _spendAllowance(owner, shares);
        _burn(owner, shares);
        require(asset_.transfer(receiver, assets), "transfer failed");
        emit Withdraw(msg.sender, receiver, owner, assets, shares);
    }

    function redeem(uint256 shares, address receiver, address owner) external returns (uint256 assets) {
        assets = convertToAssets(shares);
        require(assets != 0, "zero assets");
        _spendAllowance(owner, shares);
        _burn(owner, shares);
        require(asset_.transfer(receiver, assets), "transfer failed");
        emit Withdraw(msg.sender, receiver, owner, assets, shares);
    }

    // ---- internals ----
    function _spendAllowance(address owner, uint256 shares) internal {
        if (msg.sender != owner) {
            uint256 allowed = allowance[owner][msg.sender];
            if (allowed != type(uint256).max) allowance[owner][msg.sender] = allowed - shares;
        }
    }

    function _mint(address to, uint256 shares) internal {
        totalSupply += shares;
        balanceOf[to] += shares;
        emit Transfer(address(0), to, shares);
    }

    function _burn(address from, uint256 shares) internal {
        balanceOf[from] -= shares;
        totalSupply -= shares;
        emit Transfer(from, address(0), shares);
    }

    function _move(address from, address to, uint256 value) internal {
        balanceOf[from] -= value;
        balanceOf[to] += value;
        emit Transfer(from, to, value);
    }
}
