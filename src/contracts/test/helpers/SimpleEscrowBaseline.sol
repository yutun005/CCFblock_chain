// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Experimental one-task baseline ONLY. Not a claim about other escrow implementations.
contract SimpleEscrowBaseline is ReentrancyGuard {
    using SafeERC20 for IERC20;
    IERC20 public immutable token;
    address public immutable owner;
    uint256 public remaining;
    uint256 public refund;
    bool public finished;
    mapping(address => uint256) public pending;

    constructor(IERC20 t) {
        token = t;
        owner = msg.sender;
    }

    function deposit(uint256 amount) external {
        require(msg.sender == owner && remaining == 0 && !finished);
        token.safeTransferFrom(owner, address(this), amount);
        remaining = amount;
    }

    function settle(address recipient, uint256 amount) external {
        require(msg.sender == owner && !finished && amount <= remaining);
        finished = true;
        remaining -= amount;
        pending[recipient] += amount;
    }

    function finalize() external {
        require(msg.sender == owner && finished);
        refund = remaining;
        remaining = 0;
    }

    function withdraw() external nonReentrant {
        uint256 amount = pending[msg.sender];
        require(amount > 0);
        pending[msg.sender] = 0;
        token.safeTransfer(msg.sender, amount);
    }

    function withdrawRefund() external nonReentrant {
        require(msg.sender == owner && refund > 0);
        uint256 amount = refund;
        refund = 0;
        token.safeTransfer(owner, amount);
    }
}
