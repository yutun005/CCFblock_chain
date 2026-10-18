// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;
import {Fixture, TaskEscrow} from "../helpers/Fixture.sol";

contract IntegratedBindingsTest is Fixture {
    bytes32 internal constant BOUND = keccak256("BOUND");
    bytes32 internal constant PLAN = keccak256("complete frozen plan");

    function _bound() internal {
        escrow.createBoundJob(BOUND, coordinator, 100 * U, uint64(block.timestamp + 1 days),
                              PLAN, verifier, _configs());
    }

    function testBoundPlanAndValidatorCannotBeOverwritten() public {
        _bound();
        assertEq(escrow.planHashes(BOUND), PLAN);
        assertEq(escrow.jobValidators(BOUND), verifier);
        vm.expectRevert(TaskEscrow.InvalidJob.selector);
        _bound();
    }

    function testAnotherAuthorizedVerifierCannotApproveBoundJob() public {
        _bound();
        escrow.grantRole(escrow.VERIFIER_ROLE(), stranger);
        TaskEscrow.Quote memory q = _quote(0);
        q.jobId = BOUND;
        escrow.assignTask(q, _sig(AGENT_KEY, escrow.hashQuote(q)));
        TaskEscrow.Result memory r = TaskEscrow.Result(BOUND, 0, 0, agent, keccak256("result"),
                                                      keccak256("uri"), uint64(block.timestamp));
        TaskEscrow.Attestation memory a = TaskEscrow.Attestation(BOUND, 0, 0, r.resultHash, POLICY,
                                                                true, uint64(block.timestamp + 60), 1234);
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory vs = _sig(STRANGER_KEY, escrow.hashAttestation(a));
        vm.expectRevert(TaskEscrow.Unauthorized.selector);
        escrow.settleTask(r, rs, a, vs);
        assertEq(escrow.credits(BOUND, agent), 0);
    }

    function testStopPreventsNewDelegationAndAllowsRefund() public {
        _bound();
        escrow.requestStop(BOUND);
        TaskEscrow.Quote memory q = _quote(0);
        q.jobId = BOUND;
        bytes memory signature = _sig(AGENT_KEY, escrow.hashQuote(q));
        vm.expectRevert(TaskEscrow.InvalidState.selector);
        escrow.assignTask(q, signature);
        escrow.cancelTask(BOUND, 0);
        escrow.cancelTask(BOUND, 1);
        escrow.finalizeJob(BOUND);
        escrow.withdrawRefund(BOUND);
        assertEq(escrow.jobRefunded(BOUND), 100 * U);
        _assertAccounting();
    }

    function testJobWithdrawalDoesNotConsumeAnotherJobsCredit() public {
        _assign(0); _settle(0);
        _bound();
        TaskEscrow.Quote memory q = _quote(1);
        q.jobId = BOUND; q.taskId = 0; q.bidNonce = 12345;
        escrow.assignTask(q, _sig(AGENT_KEY, escrow.hashQuote(q)));
        TaskEscrow.Result memory r = TaskEscrow.Result(BOUND, 0, 0, agent, keccak256("r"),
                                                      keccak256("uri"), uint64(block.timestamp));
        TaskEscrow.Attestation memory a = TaskEscrow.Attestation(BOUND, 0, 0, r.resultHash, POLICY,
                                                                true, uint64(block.timestamp + 60), 12345);
        escrow.settleTask(r, _sig(AGENT_KEY, escrow.hashResult(r)), a,
                          _sig(VERIFIER_KEY, escrow.hashAttestation(a)));
        vm.prank(agent); escrow.withdraw(JOB);
        assertEq(escrow.credits(JOB, agent), 0);
        assertEq(escrow.credits(BOUND, agent), 20 * U);
        assertEq(escrow.withdrawable(agent), 20 * U);
        vm.prank(agent); escrow.withdraw();
        assertEq(escrow.credits(BOUND, agent), 0);
        assertEq(escrow.jobWithdrawn(BOUND), 20 * U);
        assertEq(escrow.jobWithdrawn(JOB), 20 * U);
        _assertAccounting();
    }
}
