// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Fixture} from "../helpers/Fixture.sol";
import {TaskEscrow} from "../../src/TaskEscrow.sol";

contract DASCSecurityTest is Fixture {
    function test_SEC01_QuotePriceTamper() public {
        TaskEscrow.Quote memory q = _quote(0);
        bytes memory sig = _sig(AGENT_KEY, escrow.hashQuote(q));
        q.price = 21 * U;
        vm.expectRevert(TaskEscrow.InvalidSignature.selector);
        escrow.assignTask(q, sig);
        _assertAccounting();
    }

    function test_SEC02_QuoteIdentityTamper() public {
        TaskEscrow.Quote memory q = _quote(0);
        bytes memory sig = _sig(AGENT_KEY, escrow.hashQuote(q));
        q.agentWallet = backup;
        vm.expectRevert(TaskEscrow.InvalidSignature.selector);
        escrow.assignTask(q, sig);
    }

    function test_SEC03_QuoteCrossTaskReplay() public {
        TaskEscrow.Quote memory q = _quote(0);
        bytes memory sig = _sig(AGENT_KEY, escrow.hashQuote(q));
        q.taskId = 1;
        vm.expectRevert(TaskEscrow.InvalidSignature.selector);
        escrow.assignTask(q, sig);
    }

    function test_SEC04_QuoteCrossJobReplay() public {
        bytes32 other = keccak256("other");
        _create(other);
        TaskEscrow.Quote memory q = _quote(0);
        bytes memory sig = _sig(AGENT_KEY, escrow.hashQuote(q));
        q.jobId = other;
        vm.expectRevert(TaskEscrow.InvalidSignature.selector);
        escrow.assignTask(q, sig);
    }

    function test_SEC05_CrossContractReplay() public {
        TaskEscrow other = new TaskEscrow(address(token), address(this));
        TaskEscrow.Quote memory q = _quote(0);
        bytes memory sig = _sig(AGENT_KEY, other.hashQuote(q));
        vm.expectRevert(TaskEscrow.InvalidSignature.selector);
        escrow.assignTask(q, sig);
    }

    function test_SEC06_CrossChainReplay() public {
        TaskEscrow.Quote memory q = _quote(0);
        bytes32 domain = keccak256(
            abi.encode(
                keccak256(
                    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
                ),
                keccak256("DASC"),
                keccak256("1"),
                block.chainid + 1,
                address(escrow)
            )
        );
        bytes32 structure = keccak256(
            abi.encode(
                escrow.QUOTE_TYPEHASH(),
                q.jobId,
                q.taskId,
                q.attempt,
                q.agentWallet,
                q.price,
                q.requirementHash,
                q.paymentToken,
                q.validUntil,
                q.bidNonce
            )
        );
        bytes memory sig = _sig(AGENT_KEY, keccak256(abi.encodePacked("\x19\x01", domain, structure)));
        vm.expectRevert(TaskEscrow.InvalidSignature.selector);
        escrow.assignTask(q, sig);
    }

    function test_SEC07_ExpiredQuote() public {
        TaskEscrow.Quote memory q = _quote(0);
        q.validUntil = uint64(block.timestamp);
        bytes memory sig = _sig(AGENT_KEY, escrow.hashQuote(q));
        vm.expectRevert(TaskEscrow.Expired.selector);
        escrow.assignTask(q, sig);
    }

    function test_SEC08_ConsumedBidNonce() public {
        _assign(0);
        TaskEscrow.Quote memory q = _quote(1);
        q.bidNonce = 0;
        bytes memory sig = _sig(AGENT_KEY, escrow.hashQuote(q));
        vm.expectRevert(TaskEscrow.NonceUsed.selector);
        escrow.assignTask(q, sig);
    }

    function test_SEC11_ModifiedResultContentHash() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        r.resultHash = keccak256("changed");
        vm.expectRevert(TaskEscrow.InvalidSignature.selector);
        escrow.settleTask(r, rs, a, av);
    }

    function test_SEC12_ForgedAgentResultSignature() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        bytes memory rs = _sig(BACKUP_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        vm.expectRevert(TaskEscrow.InvalidSignature.selector);
        escrow.settleTask(r, rs, a, av);
    }

    function test_SEC13_CrossTaskResultReplay() public {
        _assign(0);
        _assign(1);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        r.taskId = 1;
        vm.expectRevert(TaskEscrow.InvalidSignature.selector);
        escrow.settleTask(r, rs, a, av);
    }

    function test_SEC14_OldAttemptAfterReassignment() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        escrow.revokeTask(JOB, 0);
        _assign(0);
        vm.expectRevert(TaskEscrow.InvalidResult.selector);
        escrow.settleTask(r, rs, a, av);
        assertEq(escrow.withdrawable(agent), 0);
        _settle(0);
        assertEq(escrow.withdrawable(agent), 20 * U);
    }

    function test_SEC15_LateOldAgentAfterExpiry() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        vm.warp(escrow.getTask(JOB, 0).deadline);
        escrow.expireTask(JOB, 0);
        vm.expectRevert(TaskEscrow.InvalidState.selector);
        escrow.settleTask(r, rs, a, av);
    }

    function test_SEC16_ExpiryWinsAtExactBoundary() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        a.validUntil = uint64(block.timestamp + 500);
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        vm.warp(escrow.getTask(JOB, 0).deadline);
        vm.expectRevert(TaskEscrow.Expired.selector);
        escrow.settleTask(r, rs, a, av);
        escrow.expireTask(JOB, 0);
        assertEq(escrow.withdrawable(agent), 0);
        _assertAccounting();
    }

    function test_SEC16_SettlementWinsBeforeDeadline() public {
        _assign(0);
        _settle(0);
        vm.expectRevert(TaskEscrow.InvalidState.selector);
        escrow.expireTask(JOB, 0);
        assertEq(escrow.withdrawable(agent), 20 * U);
    }

    function test_SEC17_DependencyBypass() public {
        _assign(2);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(2);
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        vm.expectRevert(TaskEscrow.DependenciesUnsettled.selector);
        escrow.settleTask(r, rs, a, av);
        assertFalse(escrow.usedAttestationNonces(verifier, 2));
        _assign(0);
        _settle(0);
        _assign(1);
        _settle(1);
        escrow.settleTask(r, rs, a, av);
    }

    function test_SEC18_SelfDependency() public {
        TaskEscrow.TaskConfig[] memory c = _configs();
        c[0].depsMask = 1;
        vm.expectRevert(TaskEscrow.InvalidConfiguration.selector);
        escrow.createJob(keccak256("invalid"), coordinator, 100 * U, uint64(block.timestamp + 500), c);
    }

    function test_SEC18_BackwardCycle() public {
        TaskEscrow.TaskConfig[] memory c = _configs();
        c[0].depsMask = 2;
        c[1].depsMask = 1;
        vm.expectRevert(TaskEscrow.InvalidConfiguration.selector);
        escrow.createJob(keccak256("cycle"), coordinator, 100 * U, uint64(block.timestamp + 500), c);
    }

    function test_SEC19_UnauthorizedVerifier() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(STRANGER_KEY, escrow.hashAttestation(a));
        vm.expectRevert(TaskEscrow.Unauthorized.selector);
        escrow.settleTask(r, rs, a, av);
    }

    function test_SEC20_AttestationForDifferentResult() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        a.resultHash = keccak256("another");
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        vm.expectRevert(TaskEscrow.InvalidAttestation.selector);
        escrow.settleTask(r, rs, a, av);
    }

    function test_SEC21_AttestationForOldAttempt() public {
        _assign(0);
        escrow.revokeTask(JOB, 0);
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        a.attempt = 0;
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        vm.expectRevert(TaskEscrow.InvalidAttestation.selector);
        escrow.settleTask(r, rs, a, av);
    }

    function test_SEC22_DoubleSettlement() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        escrow.settleTask(r, rs, a, av);
        vm.expectRevert(TaskEscrow.InvalidState.selector);
        escrow.settleTask(r, rs, a, av);
        assertEq(escrow.withdrawable(agent), 20 * U);
    }

    function test_SEC23_UnauthorizedSettlement() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        vm.prank(stranger);
        vm.expectRevert(TaskEscrow.Unauthorized.selector);
        escrow.settleTask(r, rs, a, av);
    }

    function test_SEC23_UnauthorizedRefund() public {
        escrow.cancelTask(JOB, 0);
        escrow.cancelTask(JOB, 1);
        escrow.finalizeJob(JOB);
        vm.prank(stranger);
        vm.expectRevert(TaskEscrow.Unauthorized.selector);
        escrow.withdrawRefund(JOB);
    }

    function test_SEC24_UnauthorizedReassignment() public {
        _assign(0);
        vm.prank(stranger);
        vm.expectRevert(TaskEscrow.Unauthorized.selector);
        escrow.revokeTask(JOB, 0);
        assertEq(escrow.getTask(JOB, 0).payeeWallet, agent);
    }

    function test_SEC25_OverBudgetQuote() public {
        TaskEscrow.Quote memory q = _quote(0);
        q.price = 26 * U;
        bytes memory sig = _sig(AGENT_KEY, escrow.hashQuote(q));
        vm.expectRevert(TaskEscrow.InvalidQuote.selector);
        escrow.assignTask(q, sig);
    }

    function test_SEC26_OvercommittedBudget() public {
        TaskEscrow.TaskConfig[] memory c = _configs();
        c[0].budgetCap = 26 * U;
        vm.expectRevert(TaskEscrow.InvalidConfiguration.selector);
        escrow.createJob(keccak256("over"), coordinator, 100 * U, uint64(block.timestamp + 500), c);
        assertEq(token.balanceOf(address(escrow)), 100 * U);
    }

    function test_SEC26_DuplicateJobCannotReserveTwice() public {
        vm.expectRevert(TaskEscrow.InvalidJob.selector);
        _create(JOB);
        _assertAccounting();
    }

    function test_SEC27_DoubleRefundAndDoubleWithdrawal() public {
        _assign(0);
        _settle(0);
        escrow.cancelTask(JOB, 1);
        escrow.finalizeJob(JOB);
        escrow.withdrawRefund(JOB);
        vm.expectRevert(TaskEscrow.NothingToWithdraw.selector);
        escrow.withdrawRefund(JOB);
        vm.prank(agent);
        escrow.withdraw();
        vm.prank(agent);
        vm.expectRevert(TaskEscrow.NothingToWithdraw.selector);
        escrow.withdraw();
        _assertAccounting();
    }

    function test_SEC29_DatabaseRetryCannotPayTwice() public {
        _assign(0);
        _settle(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        vm.expectRevert(TaskEscrow.InvalidState.selector);
        escrow.settleTask(r, rs, a, av);
        assertEq(escrow.withdrawable(agent), 20 * U);
        _assertAccounting();
    }

    /// LIMITATION: a malicious authorized verifier can approve semantically false data.
    function test_SEC30_LIMITATION_AuthorizedBadApprovalStillPays() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        r.resultHash = keccak256("invalid statistics");
        a.resultHash = r.resultHash;
        escrow.settleTask(
            r, _sig(AGENT_KEY, escrow.hashResult(r)), a, _sig(VERIFIER_KEY, escrow.hashAttestation(a))
        );
        assertEq(escrow.withdrawable(agent), 20 * U); // This is NOT successful defense.
    }

    function testExpiredAttestationRejected() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        a.validUntil = uint64(block.timestamp);
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        vm.expectRevert(TaskEscrow.Expired.selector);
        escrow.settleTask(r, rs, a, av);
    }

    function testFixedValidationPolicyCannotBeReplaced() public {
        _assign(0);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0);
        a.validationPolicyId = keccak256("weaker");
        bytes memory rs = _sig(AGENT_KEY, escrow.hashResult(r));
        bytes memory av = _sig(VERIFIER_KEY, escrow.hashAttestation(a));
        vm.expectRevert(TaskEscrow.InvalidAttestation.selector);
        escrow.settleTask(r, rs, a, av);
    }
}
