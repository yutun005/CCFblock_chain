// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Fixture} from "../helpers/Fixture.sol";
import {TaskEscrow} from "../../src/TaskEscrow.sol";

contract TaskEscrowUnitTest is Fixture {
    function testFourNodeFlowWithdrawalAndRefund() public {
        for (uint8 i; i < 4; ++i) {
            _assign(i);
            _settle(i);
        }
        assertEq(escrow.withdrawable(agent), 80 * U);
        escrow.finalizeJob(JOB);
        assertEq(escrow.refundable(JOB), 20 * U);
        escrow.withdrawRefund(JOB);
        assertEq(token.balanceOf(address(escrow)), 80 * U);
        vm.prank(agent);
        escrow.withdraw();
        assertEq(token.balanceOf(agent), 80 * U);
        assertEq(token.balanceOf(address(escrow)), 0);
        _assertAccounting();
    }

    function testCancelCascadesButIndependentTaskCanSettle() public {
        _assign(2);
        escrow.cancelTask(JOB, 0);
        assertEq(uint8(escrow.getTask(JOB, 2).state), uint8(TaskEscrow.TaskState.CANCELED));
        assertEq(uint8(escrow.getTask(JOB, 3).state), uint8(TaskEscrow.TaskState.CANCELED));
        _assign(1);
        _settle(1);
        escrow.finalizeJob(JOB);
        escrow.withdrawRefund(JOB);
        assertEq(escrow.withdrawable(agent), 20 * U);
        _assertAccounting();
    }

    function testPermissionlessExpireUnassignedJobThenRefund() public {
        vm.warp(escrow.getJob(JOB).deadline);
        vm.prank(stranger);
        escrow.expireJob(JOB);
        vm.prank(stranger);
        escrow.finalizeJob(JOB);
        escrow.withdrawRefund(JOB);
        assertEq(escrow.totalRefunded(), 100 * U);
        _assertAccounting();
    }

    function testValidNegativeAttestationEmitsFailureAndReleasesAttempt() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        a.verdict = false;
        escrow.settleTask(
            r, _sig(AGENT_KEY, escrow.hashResult(r)), a, _sig(VERIFIER_KEY, escrow.hashAttestation(a))
        );
        assertEq(escrow.getTask(JOB, 0).attempt, 1);
        assertEq(escrow.withdrawable(agent), 0);
        _assign(0);
        _settle(0);
        _assertAccounting();
    }

    function testRetryLimitCancelsAndCascades() public {
        for (uint8 i; i < 3; ++i) {
            _assign(0);
            vm.warp(escrow.getTask(JOB, 0).deadline);
            escrow.expireTask(JOB, 0);
        }
        assertEq(uint8(escrow.getTask(JOB, 0).state), uint8(TaskEscrow.TaskState.CANCELED));
        assertEq(uint8(escrow.getTask(JOB, 3).state), uint8(TaskEscrow.TaskState.CANCELED));
        _assertAccounting();
    }

    function testEarlyExpiryAndPrematureFinalizeRejected() public {
        _assign(0);
        vm.expectRevert(TaskEscrow.NotExpired.selector);
        escrow.expireTask(JOB, 0);
        vm.expectRevert(TaskEscrow.InvalidState.selector);
        escrow.finalizeJob(JOB);
    }

    function testAll16TasksMaskAndBudget() public {
        TaskEscrow.TaskConfig[] memory c = new TaskEscrow.TaskConfig[](16);
        for (uint8 i; i < 16; ++i) {
            c[i] = TaskEscrow.TaskConfig(REQUIREMENT, POLICY, U, 0, 120, 1);
        }
        bytes32 job = keccak256("max16");
        escrow.createJob(job, coordinator, 16 * U, uint64(block.timestamp + 1 days), c);
        for (uint8 i; i < 16; ++i) {
            escrow.cancelTask(job, i);
        }
        escrow.finalizeJob(job);
        escrow.withdrawRefund(job);
        _assertAccounting();
    }

    function testFuzzSettlementBoundedByQuoteAndCap(uint256 price) public {
        price = bound(price, 1, 25 * U);
        TaskEscrow.Quote memory q = _quote(0);
        q.price = price;
        escrow.assignTask(q, _sig(AGENT_KEY, escrow.hashQuote(q)));
        _settle(0);
        assertEq(escrow.withdrawable(agent), price);
        assertLe(price, escrow.getTask(JOB, 0).budgetCap);
        _assertAccounting();
    }

    function testFuzzTopologicalDeps(uint16 deps) public {
        TaskEscrow.TaskConfig[] memory c = _configs();
        c[2].depsMask = deps;
        if ((uint256(deps) & ~uint256(3)) != 0) {
            vm.expectRevert(TaskEscrow.InvalidConfiguration.selector);
        }
        escrow.createJob(keccak256("fuzz-deps"), coordinator, 100 * U, uint64(block.timestamp + 500), c);
        _assertAccounting();
    }
}
