// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {TaskEscrow} from "../../src/TaskEscrow.sol";

/// Test-only malicious token, outside the supported payment token assumption.
contract CallbackToken is ERC20 {
    TaskEscrow public target;
    bytes32 public job;
    bool public attacked;
    bool public reentered;
    bytes public reentryError;

    constructor() ERC20("test attack", "ATTACK") {
        _mint(msg.sender, 100);
    }

    function configure(TaskEscrow e, bytes32 j) external {
        target = e;
        job = j;
    }

    function _update(address from, address to, uint256 value) internal override {
        super._update(from, to, value);
        if (address(target) != address(0) && from == address(target) && !attacked) {
            attacked = true;
            try target.withdrawRefund(job) {
                reentered = true;
            } catch (bytes memory reason) {
                reentryError = reason;
            }
        }
    }
}

contract ReentrancyTest is Test {
    function test_SEC28_CallbackCannotReenterOrDoubleRefund() public {
        CallbackToken token = new CallbackToken();
        TaskEscrow e = new TaskEscrow(address(token), address(this));
        bytes32 job = keccak256("attack");
        token.configure(e, job);
        token.approve(address(e), 100);
        TaskEscrow.TaskConfig[] memory c = new TaskEscrow.TaskConfig[](1);
        c[0] = TaskEscrow.TaskConfig(keccak256("r"), keccak256("p"), 100, 0, 20, 1);
        e.createJob(job, address(this), 100, uint64(block.timestamp + 100), c);
        e.cancelTask(job, 0);
        e.finalizeJob(job);
        e.withdrawRefund(job);
        assertTrue(token.attacked());
        assertFalse(token.reentered());
        assertEq(bytes4(token.reentryError()), bytes4(keccak256("ReentrancyGuardReentrantCall()")));
        assertEq(token.balanceOf(address(this)), 100);
        assertEq(e.refundable(job), 0);
    }
}
