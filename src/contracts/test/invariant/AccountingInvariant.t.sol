// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {StdInvariant} from "forge-std/StdInvariant.sol";
import {MockUSDC} from "../../src/MockUSDC.sol";
import {TaskEscrow} from "../../src/TaskEscrow.sol";

contract LifecycleHandler is Test {
    uint256 public constant AGENT_KEY = 0xA11CE;
    uint256 public constant BACKUP_KEY = 0xB0B;
    uint256 public constant VERIFIER_KEY = 0xC0FFEE;
    TaskEscrow public immutable escrow;
    MockUSDC public immutable token;
    bytes32[] public jobs;
    uint256 public nonce;
    uint256 public successes;
    mapping(bytes32 => mapping(uint8 => uint256)) public settlementCount;
    mapping(bytes32 => mapping(uint8 => uint32)) public lastAttempt;
    uint256 public unauthorizedSuccesses;
    bool public attemptRegression;

    constructor(TaskEscrow e, MockUSDC t) {
        escrow = e;
        token = t;
        t.approve(address(e), type(uint256).max);
    }

    function create(uint256 salt) public {
        if (jobs.length >= 8) return;
        bytes32 id = keccak256(abi.encode(salt, jobs.length));
        TaskEscrow.TaskConfig[] memory c = new TaskEscrow.TaskConfig[](4);
        for (uint8 i; i < 4; ++i) {
            c[i] = TaskEscrow.TaskConfig(
                keccak256("r"), keccak256("p"), 25e6, i == 2 ? 3 : (i == 3 ? 4 : 0), 120, 3
            );
        }
        escrow.createJob(id, address(this), 100e6, uint64(block.timestamp + 1 days), c);
        jobs.push(id);
        ++successes;
    }

    function assign(uint256 seed, uint8 task, uint256 price, bool useBackup) external {
        if (jobs.length == 0) return;
        bytes32 id = jobs[seed % jobs.length];
        task %= 4;
        TaskEscrow.Task memory t = escrow.getTask(id, task);
        uint256 key = useBackup ? BACKUP_KEY : AGENT_KEY;
        TaskEscrow.Quote memory q = TaskEscrow.Quote(
            id,
            task,
            t.attempt,
            vm.addr(key),
            bound(price, 1, 30e6),
            t.requirementHash,
            address(token),
            uint64(block.timestamp + 100),
            ++nonce
        );
        try escrow.assignTask(q, _sign(key, escrow.hashQuote(q))) {
            ++successes;
        } catch {}
        _observe(id, task);
    }

    function settle(uint256 seed, uint8 task, bool verdict, bool corruptAttempt) external {
        if (jobs.length == 0) return;
        bytes32 id = jobs[seed % jobs.length];
        task %= 4;
        TaskEscrow.Task memory t = escrow.getTask(id, task);
        uint256 key = t.payeeWallet == vm.addr(BACKUP_KEY) ? BACKUP_KEY : AGENT_KEY;
        TaskEscrow.Result memory r = TaskEscrow.Result(
            id,
            task,
            corruptAttempt && t.attempt > 0 ? t.attempt - 1 : t.attempt,
            t.payeeWallet,
            keccak256("result"),
            keccak256("uri"),
            uint64(block.timestamp)
        );
        TaskEscrow.Attestation memory a = TaskEscrow.Attestation(
            id,
            task,
            r.attempt,
            r.resultHash,
            t.validationPolicyId,
            verdict,
            uint64(block.timestamp + 60),
            ++nonce
        );
        try escrow.settleTask(
            r, _sign(key, escrow.hashResult(r)), a, _sign(VERIFIER_KEY, escrow.hashAttestation(a))
        ) {
            ++successes;
            if (verdict) ++settlementCount[id][task];
        } catch {}
        _observe(id, task);
    }

    function expire(uint256 seed, uint8 task, uint64 elapsed) external {
        vm.warp(block.timestamp + bound(elapsed, 0, 200));
        if (jobs.length == 0) return;
        bytes32 id = jobs[seed % jobs.length];
        task %= 4;
        try escrow.expireTask(id, task) {
            ++successes;
        } catch {}
        _observe(id, task);
    }

    function cancel(uint256 seed, uint8 task) external {
        if (jobs.length == 0) return;
        bytes32 id = jobs[seed % jobs.length];
        task %= 4;
        try escrow.cancelTask(id, task) {
            ++successes;
        } catch {}
        _observe(id, task);
    }

    function finalizeAndRefund(uint256 seed) external {
        if (jobs.length == 0) return;
        bytes32 id = jobs[seed % jobs.length];
        try escrow.finalizeJob(id) {
            ++successes;
        } catch {}
        try escrow.withdrawRefund(id) {
            ++successes;
        } catch {}
    }

    function withdraw(bool useBackup) external {
        vm.prank(vm.addr(useBackup ? BACKUP_KEY : AGENT_KEY));
        try escrow.withdraw() {
            ++successes;
        } catch {}
    }

    function unauthorized(uint256 seed, uint8 task) external {
        if (jobs.length == 0) return;
        bytes32 id = jobs[seed % jobs.length];
        task %= 4;
        vm.prank(address(0xBAD));
        try escrow.cancelTask(id, task) {
            ++unauthorizedSuccesses;
        } catch {}
    }

    function count() external view returns (uint256) {
        return jobs.length;
    }

    function _sign(uint256 key, bytes32 digest) internal pure returns (bytes memory) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
        return abi.encodePacked(r, s, v);
    }

    function _observe(bytes32 id, uint8 task) internal {
        uint32 current = escrow.getTask(id, task).attempt;
        if (current < lastAttempt[id][task]) attemptRegression = true;
        lastAttempt[id][task] = current;
    }
}

contract AccountingInvariantTest is StdInvariant, Test {
    TaskEscrow internal escrow;
    MockUSDC internal token;
    LifecycleHandler internal handler;

    function setUp() public {
        vm.warp(1_000_000);
        token = new MockUSDC(address(this));
        escrow = new TaskEscrow(address(token), address(this));
        handler = new LifecycleHandler(escrow, token);
        token.mint(address(handler), 1000e6);
        escrow.grantRole(escrow.VERIFIER_ROLE(), vm.addr(handler.VERIFIER_KEY()));
        handler.create(0);
        // Seed a settled task and a refund, ensuring the invariant cannot pass by only rejecting actions.
        handler.assign(0, 0, 20e6, false);
        handler.settle(0, 0, true, false);
        handler.cancel(0, 1);
        handler.finalizeAndRefund(0);
        handler.create(1);
        bytes4[] memory selectors = new bytes4[](8);
        selectors[0] = handler.create.selector;
        selectors[1] = handler.assign.selector;
        selectors[2] = handler.settle.selector;
        selectors[3] = handler.expire.selector;
        selectors[4] = handler.cancel.selector;
        selectors[5] = handler.finalizeAndRefund.selector;
        selectors[6] = handler.withdraw.selector;
        selectors[7] = handler.unauthorized.selector;
        targetSelector(FuzzSelector(address(handler), selectors));
        targetContract(address(handler));
    }

    function invariant_INV01_GlobalConservation() public view {
        assertEq(
            escrow.totalDeposited(),
            escrow.totalWithdrawn() + escrow.totalRefunded() + token.balanceOf(address(escrow))
        );
        assertEq(token.balanceOf(address(escrow)), escrow.totalAvailable() + escrow.totalPending());
        assertGt(handler.successes(), 0);
    }

    function invariant_INV02_AtMostOneSettlement() public view {
        for (uint256 n; n < handler.count(); ++n) {
            for (uint8 i; i < 4; ++i) {
                assertLe(handler.settlementCount(handler.jobs(n), i), 1);
            }
        }
    }

    function invariant_INV03_PriceWithinCap() public view {
        for (uint256 n; n < handler.count(); ++n) {
            for (uint8 i; i < 4; ++i) {
                TaskEscrow.Task memory t = escrow.getTask(handler.jobs(n), i);
                assertLe(t.acceptedPrice, t.budgetCap);
            }
        }
    }

    function invariant_INV04_ReservedBudgetWithinDeposit() public view {
        for (uint256 n; n < handler.count(); ++n) {
            TaskEscrow.Job memory j = escrow.getJob(handler.jobs(n));
            assertLe(j.reservedBudget, j.deposit);
        }
    }

    function invariant_INV05_AttemptsNeverDecrease() public view {
        assertFalse(handler.attemptRegression());
        for (uint256 n; n < handler.count(); ++n) {
            for (uint8 i; i < 4; ++i) {
                assertGe(escrow.getTask(handler.jobs(n), i).attempt, handler.lastAttempt(handler.jobs(n), i));
            }
        }
    }

    function invariant_INV06_DependenciesSettled() public view {
        for (uint256 n; n < handler.count(); ++n) {
            bytes32 id = handler.jobs(n);
            TaskEscrow.Job memory j = escrow.getJob(id);
            for (uint8 i; i < 4; ++i) {
                TaskEscrow.Task memory t = escrow.getTask(id, i);
                if (t.state == TaskEscrow.TaskState.SETTLED) {
                    assertEq(j.settledMask & t.depsMask, t.depsMask);
                }
            }
        }
    }

    function invariant_INV07_RefundDoesNotSpendPendingRewards() public view {
        uint256 available;
        for (uint256 n; n < handler.count(); ++n) {
            bytes32 id = handler.jobs(n);
            available += escrow.getJob(id).remaining + escrow.refundable(id);
        }
        assertEq(available, escrow.totalAvailable());
        uint256 pending = escrow.withdrawable(vm.addr(handler.AGENT_KEY()))
            + escrow.withdrawable(vm.addr(handler.BACKUP_KEY()));
        assertEq(pending, escrow.totalPending());
        assertGe(token.balanceOf(address(escrow)), pending);
    }

    function invariant_INV08_UnauthorizedChangesRejected() public view {
        assertEq(handler.unauthorizedSuccesses(), 0);
    }
}
