// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {MockUSDC} from "../../src/MockUSDC.sol";
import {TaskEscrow} from "../../src/TaskEscrow.sol";

abstract contract Fixture is Test {
    // Public deterministic unit-test secrets ONLY; never use on an external network.
    uint256 internal constant AGENT_KEY = 0xA11CE;
    uint256 internal constant BACKUP_KEY = 0xB0B;
    uint256 internal constant VERIFIER_KEY = 0xC0FFEE;
    uint256 internal constant STRANGER_KEY = 0xBAD;
    uint256 internal constant U = 1e6;
    bytes32 internal constant JOB = keccak256("JOB-001");
    bytes32 internal constant REQUIREMENT = keccak256("requirement-v1");
    bytes32 internal constant POLICY = keccak256("stat_v1");
    MockUSDC internal token;
    TaskEscrow internal escrow;
    address internal agent;
    address internal backup;
    address internal verifier;
    address internal coordinator = address(0xD);
    address internal stranger;

    function setUp() public virtual {
        vm.warp(1_000_000);
        agent = vm.addr(AGENT_KEY);
        backup = vm.addr(BACKUP_KEY);
        verifier = vm.addr(VERIFIER_KEY);
        stranger = vm.addr(STRANGER_KEY);
        token = new MockUSDC(address(this));
        escrow = new TaskEscrow(address(token), address(this));
        escrow.grantRole(escrow.VERIFIER_ROLE(), verifier);
        token.mint(address(this), 10_000 * U);
        token.approve(address(escrow), type(uint256).max);
        _create(JOB);
    }

    function _configs() internal pure returns (TaskEscrow.TaskConfig[] memory c) {
        c = new TaskEscrow.TaskConfig[](4);
        c[0] = TaskEscrow.TaskConfig(REQUIREMENT, POLICY, 25 * U, 0, 120, 3);
        c[1] = TaskEscrow.TaskConfig(REQUIREMENT, POLICY, 25 * U, 0, 120, 3);
        c[2] = TaskEscrow.TaskConfig(REQUIREMENT, POLICY, 25 * U, 3, 120, 3);
        c[3] = TaskEscrow.TaskConfig(REQUIREMENT, POLICY, 25 * U, 4, 120, 3);
    }

    function _create(bytes32 job) internal {
        escrow.createJob(job, coordinator, 100 * U, uint64(block.timestamp + 1 days), _configs());
    }

    function _quote(uint8 taskId) internal view returns (TaskEscrow.Quote memory q) {
        TaskEscrow.Task memory t = escrow.getTask(JOB, taskId);
        q = TaskEscrow.Quote(
            JOB,
            taskId,
            t.attempt,
            agent,
            20 * U,
            REQUIREMENT,
            address(token),
            uint64(block.timestamp + 60),
            uint256(taskId) + uint256(t.attempt) * 100
        );
    }

    function _sig(uint256 key, bytes32 digest) internal pure returns (bytes memory) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
        return abi.encodePacked(r, s, v);
    }

    function _assign(uint8 taskId) internal {
        TaskEscrow.Quote memory q = _quote(taskId);
        escrow.assignTask(q, _sig(AGENT_KEY, escrow.hashQuote(q)));
    }

    function _proof(uint8 taskId)
        internal
        view
        returns (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a)
    {
        TaskEscrow.Task memory t = escrow.getTask(JOB, taskId);
        r = TaskEscrow.Result(
            JOB,
            taskId,
            t.attempt,
            t.payeeWallet,
            keccak256("result"),
            keccak256("file:result.json"),
            uint64(block.timestamp)
        );
        a = TaskEscrow.Attestation(
            JOB,
            taskId,
            t.attempt,
            r.resultHash,
            POLICY,
            true,
            uint64(block.timestamp + 60),
            uint256(taskId) + uint256(t.attempt) * 100
        );
    }

    function _settle(uint8 taskId) internal {
        (TaskEscrow.Result memory r, TaskEscrow.Attestation memory a) = _proof(taskId);
        escrow.settleTask(
            r, _sig(AGENT_KEY, escrow.hashResult(r)), a, _sig(VERIFIER_KEY, escrow.hashAttestation(a))
        );
    }

    function _assertAccounting() internal view {
        assertEq(
            escrow.totalDeposited(),
            escrow.totalWithdrawn() + escrow.totalRefunded() + token.balanceOf(address(escrow))
        );
        assertEq(token.balanceOf(address(escrow)), escrow.totalAvailable() + escrow.totalPending());
    }
}
