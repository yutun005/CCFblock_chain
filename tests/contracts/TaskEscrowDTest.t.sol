// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {MockUSDC} from "../src/MockUSDC.sol";
import {TaskEscrow} from "../src/TaskEscrow.sol";

interface VmD {
    function addr(uint256 privateKey) external pure returns (address);
    function sign(uint256 privateKey, bytes32 digest) external returns (uint8 v, bytes32 r, bytes32 s);
    function prank(address sender) external;
    function warp(uint256 timestamp) external;
    function expectRevert(bytes4 selector) external;
}

contract TaskEscrowDTest {
    VmD private constant vm = VmD(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 private constant OWNER_KEY = 0xA11CE;
    uint256 private constant COORDINATOR_KEY = 0xD00D;
    uint256 private constant AGENT_KEY = 0xB0B;
    uint256 private constant BACKUP_KEY = 0xB0C;
    uint256 private constant VERIFIER_KEY = 0xC0FFEE;
    uint256 private constant STRANGER_KEY = 0xBAD;
    uint256 private constant U = 1e6;
    bytes32 private constant JOB = keccak256("Dmodule-main-scenario");
    bytes32 private constant REQUIREMENT = keccak256("requirement-v1");
    bytes32 private constant POLICY = keccak256("sales-v1");

    MockUSDC private token;
    TaskEscrow private escrow;
    address private owner;
    address private coordinator;
    address private agent;
    address private backup;
    address private verifier;
    address private stranger;
    uint256 private nonce;

    function setUp() public {
        vm.warp(1_000_000);
        owner = vm.addr(OWNER_KEY);
        coordinator = vm.addr(COORDINATOR_KEY);
        agent = vm.addr(AGENT_KEY);
        backup = vm.addr(BACKUP_KEY);
        verifier = vm.addr(VERIFIER_KEY);
        stranger = vm.addr(STRANGER_KEY);
        token = new MockUSDC(owner);
        escrow = new TaskEscrow(address(token), owner);
        vm.prank(owner);
        escrow.grantRole(escrow.VERIFIER_ROLE(), verifier);
        vm.prank(owner);
        token.mint(owner, 10_000 * U);
        vm.prank(owner);
        token.approve(address(escrow), type(uint256).max);
        _create(JOB, 100 * U);
    }

    function testFourNodeSettlementWithdrawAndRefund() public {
        _assign(0, AGENT_KEY);
        _settle(0, AGENT_KEY);
        _assign(1, AGENT_KEY);
        _settle(1, AGENT_KEY);
        _assign(2, BACKUP_KEY);
        _settle(2, BACKUP_KEY);
        _assign(3, AGENT_KEY);
        _settle(3, AGENT_KEY);

        require(escrow.withdrawable(agent) == 60 * U, "primary payout mismatch");
        require(escrow.withdrawable(backup) == 20 * U, "backup payout mismatch");
        escrow.finalizeJob(JOB);
        require(escrow.refundable(JOB) == 20 * U, "refund mismatch");
        vm.prank(owner);
        escrow.withdrawRefund(JOB);
        vm.prank(agent);
        escrow.withdraw();
        vm.prank(backup);
        escrow.withdraw();
        require(token.balanceOf(owner) == 9_920 * U, "owner balance mismatch");
        require(token.balanceOf(agent) == 60 * U, "agent balance mismatch");
        require(token.balanceOf(backup) == 20 * U, "backup balance mismatch");
        require(token.balanceOf(address(escrow)) == 0, "escrow retains funds");
        _assertAccounting();
    }

    function testQuotePriceTamperingIsRejected() public {
        TaskEscrow.Quote memory q = _quote(0, AGENT_KEY);
        bytes memory sig = _sign(AGENT_KEY, escrow.hashQuote(q));
        q.price += 1;
        vm.prank(coordinator);
        vm.expectRevert(TaskEscrow.InvalidSignature.selector);
        escrow.assignTask(q, sig);
        require(escrow.getTask(JOB, 0).state == TaskEscrow.TaskState.READY, "tampered quote changed state");
        _assertAccounting();
    }

    function testExpiredQuoteIsRejected() public {
        TaskEscrow.Quote memory q = _quote(0, AGENT_KEY);
        q.validUntil = uint64(block.timestamp);
        vm.prank(coordinator);
        vm.expectRevert(TaskEscrow.Expired.selector);
        escrow.assignTask(q, _sign(AGENT_KEY, escrow.hashQuote(q)));
        _assertAccounting();
    }

    function testBidNonceCannotBeReusedByTheSameAgent() public {
        TaskEscrow.Quote memory first = _quote(0, AGENT_KEY);
        vm.prank(coordinator);
        escrow.assignTask(first, _sign(AGENT_KEY, escrow.hashQuote(first)));
        TaskEscrow.Quote memory second = _quote(1, AGENT_KEY);
        second.bidNonce = first.bidNonce;
        vm.prank(coordinator);
        vm.expectRevert(TaskEscrow.NonceUsed.selector);
        escrow.assignTask(second, _sign(AGENT_KEY, escrow.hashQuote(second)));
        _assertAccounting();
    }

    function testCrossContractQuoteReplayIsRejected() public {
        TaskEscrow other = new TaskEscrow(address(token), owner);
        TaskEscrow.Quote memory q = _quote(0, AGENT_KEY);
        bytes memory sig = _sign(AGENT_KEY, other.hashQuote(q));
        vm.prank(coordinator);
        vm.expectRevert(TaskEscrow.InvalidSignature.selector);
        escrow.assignTask(q, sig);
        _assertAccounting();
    }

    function testDependencyCannotSettleBeforeParents() public {
        _assign(2, AGENT_KEY);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(2, AGENT_KEY);
        bytes memory agentSig = _sign(AGENT_KEY, escrow.hashResult(r));
        bytes memory verifierSig = _sign(VERIFIER_KEY, escrow.hashAttestation(a));
        vm.prank(coordinator);
        vm.expectRevert(TaskEscrow.DependenciesUnsettled.selector);
        escrow.settleTask(r, agentSig, a, verifierSig);
        require(escrow.withdrawable(agent) == 0, "dependency bypass paid funds");
        _assign(0, AGENT_KEY);
        _settle(0, AGENT_KEY);
        _assign(1, AGENT_KEY);
        _settle(1, AGENT_KEY);
        vm.prank(coordinator);
        escrow.settleTask(r, agentSig, a, verifierSig);
        _assertAccounting();
    }

    function testOldAttemptCannotSettleAfterExpiryAndReassignment() public {
        _assign(0, AGENT_KEY);
        (TaskEscrow.Result memory stale, TaskEscrow.Attestation memory staleProof) = _proof(0, AGENT_KEY);
        bytes memory oldAgentSig = _sign(AGENT_KEY, escrow.hashResult(stale));
        bytes memory oldVerifierSig = _sign(VERIFIER_KEY, escrow.hashAttestation(staleProof));
        vm.warp(escrow.getTask(JOB, 0).deadline);
        escrow.expireTask(JOB, 0);
        _assign(0, BACKUP_KEY);
        vm.prank(coordinator);
        vm.expectRevert(TaskEscrow.InvalidResult.selector);
        escrow.settleTask(stale, oldAgentSig, staleProof, oldVerifierSig);
        require(escrow.withdrawable(agent) == 0 && escrow.withdrawable(backup) == 0, "stale round paid");
        _assertAccounting();
    }

    function testUnauthorizedVerifierCannotSettle() public {
        _assign(0, AGENT_KEY);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0, AGENT_KEY);
        vm.prank(coordinator);
        vm.expectRevert(TaskEscrow.Unauthorized.selector);
        escrow.settleTask(r, _sign(AGENT_KEY, escrow.hashResult(r)), a,
                          _sign(STRANGER_KEY, escrow.hashAttestation(a)));
        _assertAccounting();
    }

    function testResultTamperingIsRejected() public {
        _assign(0, AGENT_KEY);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0, AGENT_KEY);
        bytes memory agentSig = _sign(AGENT_KEY, escrow.hashResult(r));
        r.resultHash = keccak256("tampered-result");
        a.resultHash = r.resultHash;
        vm.prank(coordinator);
        vm.expectRevert(TaskEscrow.InvalidSignature.selector);
        escrow.settleTask(r, agentSig, a, _sign(VERIFIER_KEY, escrow.hashAttestation(a)));
        require(escrow.withdrawable(agent) == 0, "tampered result paid");
        _assertAccounting();
    }

    function testAttestationNonceCannotBeReused() public {
        _assign(0, AGENT_KEY);
        _assign(1, AGENT_KEY);
        (TaskEscrow.Result memory firstResult, TaskEscrow.Attestation memory firstProof) = _proof(0, AGENT_KEY);
        firstProof.attestationNonce = 77;
        vm.prank(coordinator);
        escrow.settleTask(firstResult, _sign(AGENT_KEY, escrow.hashResult(firstResult)), firstProof,
                          _sign(VERIFIER_KEY, escrow.hashAttestation(firstProof)));

        (TaskEscrow.Result memory secondResult, TaskEscrow.Attestation memory secondProof) = _proof(1, AGENT_KEY);
        secondProof.attestationNonce = 77;
        vm.prank(coordinator);
        vm.expectRevert(TaskEscrow.NonceUsed.selector);
        escrow.settleTask(secondResult, _sign(AGENT_KEY, escrow.hashResult(secondResult)), secondProof,
                          _sign(VERIFIER_KEY, escrow.hashAttestation(secondProof)));
        require(escrow.withdrawable(agent) == 20 * U, "replayed proof paid twice");
        _assertAccounting();
    }

    function testDuplicateSettlementCannotPayTwice() public {
        _assign(0, AGENT_KEY);
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(0, AGENT_KEY);
        bytes memory agentSig = _sign(AGENT_KEY, escrow.hashResult(r));
        bytes memory verifierSig = _sign(VERIFIER_KEY, escrow.hashAttestation(a));
        vm.prank(coordinator);
        escrow.settleTask(r, agentSig, a, verifierSig);
        vm.prank(coordinator);
        vm.expectRevert(TaskEscrow.InvalidState.selector);
        escrow.settleTask(r, agentSig, a, verifierSig);
        require(escrow.withdrawable(agent) == 20 * U, "duplicate settlement changed payout");
        _assertAccounting();
    }

    function testOvercommittedBudgetsAreRejected() public {
        TaskEscrow.TaskConfig[] memory configs = _configs();
        vm.prank(owner);
        vm.expectRevert(TaskEscrow.InvalidConfiguration.selector);
        escrow.createJob(keccak256("overcommitted"), coordinator, 50 * U,
                         uint64(block.timestamp + 1 days), configs);
        _assertAccounting();
    }

    function testRefundCannotBeClaimedTwice() public {
        // Cancel both roots; the contract cascades cancellation to their descendants.
        escrow.cancelTask(JOB, 0);
        escrow.cancelTask(JOB, 1);
        escrow.finalizeJob(JOB);
        vm.prank(owner);
        escrow.withdrawRefund(JOB);
        vm.prank(owner);
        vm.expectRevert(TaskEscrow.NothingToWithdraw.selector);
        escrow.withdrawRefund(JOB);
        require(token.balanceOf(address(escrow)) == 0, "duplicate refund changed escrow balance");
        _assertAccounting();
    }

    function testFuzzPriceNeverExceedsTaskCap(uint256 seed) public {
        uint256 price = seed % (26 * U) + 1;
        TaskEscrow.Quote memory q = _quote(0, AGENT_KEY);
        q.price = price;
        bytes memory sig = _sign(AGENT_KEY, escrow.hashQuote(q));
        vm.prank(coordinator);
        if (price > 25 * U) {
            vm.expectRevert(TaskEscrow.InvalidQuote.selector);
            escrow.assignTask(q, sig);
        } else {
            escrow.assignTask(q, sig);
            require(escrow.getTask(JOB, 0).acceptedPrice == price, "accepted price mismatch");
        }
        _assertAccounting();
    }

    function _create(bytes32 jobId, uint256 deposit) private {
        TaskEscrow.TaskConfig[] memory configs = _configs();
        vm.prank(owner);
        escrow.createJob(jobId, coordinator, deposit, uint64(block.timestamp + 1 days), configs);
    }

    function _configs() private pure returns (TaskEscrow.TaskConfig[] memory configs) {
        configs = new TaskEscrow.TaskConfig[](4);
        configs[0] = TaskEscrow.TaskConfig(REQUIREMENT, POLICY, 25 * U, 0, 120, 3);
        configs[1] = TaskEscrow.TaskConfig(REQUIREMENT, POLICY, 25 * U, 0, 120, 3);
        configs[2] = TaskEscrow.TaskConfig(REQUIREMENT, POLICY, 25 * U, 3, 120, 3);
        configs[3] = TaskEscrow.TaskConfig(REQUIREMENT, POLICY, 25 * U, 4, 120, 3);
    }

    function _quote(uint8 taskId, uint256 key) private returns (TaskEscrow.Quote memory q) {
        TaskEscrow.Task memory task = escrow.getTask(JOB, taskId);
        q = TaskEscrow.Quote(JOB, taskId, task.attempt, vm.addr(key), 20 * U, task.requirementHash,
                             address(token), uint64(block.timestamp + 60), ++nonce);
    }

    function _assign(uint8 taskId, uint256 key) private {
        TaskEscrow.Quote memory q = _quote(taskId, key);
        vm.prank(coordinator);
        escrow.assignTask(q, _sign(key, escrow.hashQuote(q)));
    }

    function _proof(uint8 taskId, uint256 key)
        private view returns (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a)
    {
        TaskEscrow.Task memory task = escrow.getTask(JOB, taskId);
        r = TaskEscrow.Result(JOB, taskId, task.attempt, vm.addr(key), keccak256(abi.encode(taskId, task.attempt)),
                              keccak256("artifact-uri"), uint64(block.timestamp));
        a = TaskEscrow.Attestation(JOB, taskId, task.attempt, r.resultHash, task.validationPolicyId,
                                   true, uint64(block.timestamp + 60), uint256(taskId) + uint256(task.attempt) * 100);
    }

    function _settle(uint8 taskId, uint256 key) private {
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(taskId, key);
        vm.prank(coordinator);
        escrow.settleTask(r, _sign(key, escrow.hashResult(r)), a,
                          _sign(VERIFIER_KEY, escrow.hashAttestation(a)));
    }

    function _sign(uint256 key, bytes32 digest) private returns (bytes memory) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
        return abi.encodePacked(r, s, v);
    }

    function _assertAccounting() private view {
        require(escrow.totalDeposited() == escrow.totalWithdrawn() + escrow.totalRefunded()
                + token.balanceOf(address(escrow)), "global funds not conserved");
        require(token.balanceOf(address(escrow)) == escrow.totalAvailable() + escrow.totalPending(),
                "liabilities exceed contract balance");
    }
}
