// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Fixture} from "../helpers/Fixture.sol";
import {SimpleEscrowBaseline} from "../helpers/SimpleEscrowBaseline.sol";
import {TaskEscrow} from "../../src/TaskEscrow.sol";

contract GasComparisonTest is Fixture {
    SimpleEscrowBaseline internal baseline;

    function setUp() public override {
        super.setUp();
        baseline = new SimpleEscrowBaseline(token);
        token.approve(address(baseline), type(uint256).max);
    }

    /// Both cases: deposit 100, pay 20 to one Agent, refund 80; deployment is excluded by setUp.
    function testGasSimpleSingleTaskLifecycle() public {
        baseline.deposit(100 * U);
        baseline.settle(agent, 20 * U);
        baseline.finalize();
        vm.prank(agent);
        baseline.withdraw();
        baseline.withdrawRefund();
        assertEq(token.balanceOf(agent), 20 * U);
    }

    function testGasDASCSingleTaskLifecycle() public {
        bytes32 job = keccak256("gas-one-task");
        TaskEscrow.TaskConfig[] memory c = new TaskEscrow.TaskConfig[](1);
        c[0] = TaskEscrow.TaskConfig(REQUIREMENT, POLICY, 25 * U, 0, 120, 3);
        escrow.createJob(job, coordinator, 100 * U, uint64(block.timestamp + 3600), c);
        TaskEscrow.Quote memory q = TaskEscrow.Quote(
            job, 0, 0, agent, 20 * U, REQUIREMENT, address(token), uint64(block.timestamp + 60), 98765
        );
        escrow.assignTask(q, _sig(AGENT_KEY, escrow.hashQuote(q)));
        TaskEscrow.Result memory r = TaskEscrow.Result(
            job, 0, 0, agent, keccak256("result"), keccak256("uri"), uint64(block.timestamp)
        );
        TaskEscrow.Attestation memory a = TaskEscrow.Attestation(
            job, 0, 0, r.resultHash, POLICY, true, uint64(block.timestamp + 60), 98765
        );
        escrow.settleTask(
            r, _sig(AGENT_KEY, escrow.hashResult(r)), a, _sig(VERIFIER_KEY, escrow.hashAttestation(a))
        );
        escrow.finalizeJob(job);
        vm.prank(agent);
        escrow.withdraw();
        escrow.withdrawRefund(job);
        assertEq(token.balanceOf(agent), 20 * U);
        _assertAccounting();
    }
}
