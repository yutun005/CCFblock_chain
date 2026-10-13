// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice DASC v1. Funds move only after authorized, dependency-aware, attempt-bound settlement.
/// @dev Token is immutable and MUST be a standard, fee-free, non-rebasing test ERC20.
contract TaskEscrow is EIP712, AccessControl, ReentrancyGuard {
    using SafeERC20 for IERC20;

    bytes32 public constant VERIFIER_ROLE = keccak256("VERIFIER_ROLE");
    bytes32 public constant RELAYER_ROLE = keccak256("RELAYER_ROLE");
    bytes32 public constant QUOTE_TYPEHASH = keccak256(
        "Quote(bytes32 jobId,uint8 taskId,uint32 attempt,address agentWallet,uint256 price,bytes32 requirementHash,address paymentToken,uint64 validUntil,uint256 bidNonce)"
    );
    bytes32 public constant RESULT_TYPEHASH = keccak256(
        "Result(bytes32 jobId,uint8 taskId,uint32 attempt,address agentWallet,bytes32 resultHash,bytes32 artifactURIHash,uint64 submittedAt)"
    );
    bytes32 public constant ATTESTATION_TYPEHASH = keccak256(
        "Attestation(bytes32 jobId,uint8 taskId,uint32 attempt,bytes32 resultHash,bytes32 validationPolicyId,bool verdict,uint64 validUntil,uint256 attestationNonce)"
    );

    enum JobState {
        NONE,
        ACTIVE,
        FINALIZED
    }
    enum TaskState {
        READY,
        ASSIGNED,
        SETTLED,
        CANCELED
    }

    struct Job {
        address owner;
        address coordinator;
        address paymentToken;
        uint256 deposit;
        uint256 remaining;
        uint256 reservedBudget;
        uint64 deadline;
        uint8 taskCount;
        uint16 settledMask;
        uint16 terminalMask;
        JobState jobState;
    }

    struct TaskConfig {
        bytes32 requirementHash;
        bytes32 validationPolicyId;
        uint256 budgetCap;
        uint16 depsMask;
        uint64 executionWindow;
        uint32 maxAttempts;
    }

    struct Task {
        bytes32 requirementHash;
        uint16 depsMask;
        uint256 budgetCap;
        uint256 acceptedPrice;
        address payeeWallet;
        uint32 attempt;
        uint64 deadline;
        TaskState state;
        bytes32 quoteHash;
        bytes32 resultHash;
        bytes32 validationPolicyId;
        uint64 assignedAt;
        uint64 executionWindow;
        uint32 maxAttempts;
    }

    struct Quote {
        bytes32 jobId;
        uint8 taskId;
        uint32 attempt;
        address agentWallet;
        uint256 price;
        bytes32 requirementHash;
        address paymentToken;
        uint64 validUntil;
        uint256 bidNonce;
    }

    struct Result {
        bytes32 jobId;
        uint8 taskId;
        uint32 attempt;
        address agentWallet;
        bytes32 resultHash;
        bytes32 artifactURIHash;
        uint64 submittedAt;
    }

    struct Attestation {
        bytes32 jobId;
        uint8 taskId;
        uint32 attempt;
        bytes32 resultHash;
        bytes32 validationPolicyId;
        bool verdict;
        uint64 validUntil;
        uint256 attestationNonce;
    }

    IERC20 public immutable paymentToken;
    mapping(bytes32 => Job) private _jobs;
    mapping(bytes32 => mapping(uint8 => Task)) private _tasks;
    mapping(address => uint256) public withdrawable;
    mapping(bytes32 => uint256) public refundable;
    mapping(address => mapping(uint256 => bool)) public usedBidNonces;
    mapping(address => mapping(uint256 => bool)) public usedAttestationNonces;
    uint256 public totalDeposited;
    uint256 public totalWithdrawn;
    uint256 public totalRefunded;
    uint256 public totalAvailable;
    uint256 public totalPending;

    error InvalidJob();
    error InvalidConfiguration();
    error Unauthorized();
    error InvalidState();
    error InvalidQuote();
    error InvalidSignature();
    error NonceUsed();
    error InvalidResult();
    error InvalidAttestation();
    error Expired();
    error NotExpired();
    error DependenciesUnsettled();
    error NothingToWithdraw();
    error UnsupportedTokenBehavior();

    event JobCreated(bytes32 indexed jobId, address indexed owner, address coordinator, uint256 deposit);
    event TaskRegistered(bytes32 indexed jobId, uint8 indexed taskId, uint256 budgetCap, uint16 depsMask);
    event TaskAssigned(
        bytes32 indexed jobId,
        uint8 indexed taskId,
        uint32 attempt,
        address agentWallet,
        uint256 price,
        uint64 deadline,
        bytes32 quoteHash
    );
    event TaskSettled(
        bytes32 indexed jobId,
        uint8 indexed taskId,
        uint32 attempt,
        address agentWallet,
        uint256 amount,
        bytes32 resultHash,
        address verifier
    );
    event ValidationFailed(bytes32 indexed jobId, uint8 indexed taskId, uint32 attempt, bytes32 resultHash);
    event TaskInvalidated(bytes32 indexed jobId, uint8 indexed taskId, uint32 oldAttempt, uint32 newAttempt);
    event TaskCanceled(bytes32 indexed jobId, uint8 indexed taskId, uint32 attempt);
    event JobFinalized(bytes32 indexed jobId, address indexed owner, uint256 refund);
    event Withdrawn(address indexed agent, uint256 amount);
    event Refunded(bytes32 indexed jobId, address indexed owner, uint256 amount);

    constructor(address token, address admin) EIP712("DASC", "1") {
        if (token.code.length == 0 || admin == address(0)) revert InvalidConfiguration();
        paymentToken = IERC20(token);
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    function getJob(bytes32 jobId) external view returns (Job memory) {
        return _jobs[jobId];
    }

    function getTask(bytes32 jobId, uint8 taskId) external view returns (Task memory) {
        if (taskId >= _jobs[jobId].taskCount) revert InvalidJob();
        return _tasks[jobId][taskId];
    }

    function createJob(
        bytes32 jobId,
        address coordinator,
        uint256 deposit,
        uint64 deadline,
        TaskConfig[] calldata configs
    ) external nonReentrant {
        if (jobId == bytes32(0) || _jobs[jobId].jobState != JobState.NONE) {
            revert InvalidJob();
        }
        if (
            coordinator == address(0) || deposit == 0 || deadline <= block.timestamp || configs.length == 0
                || configs.length > 16
        ) revert InvalidConfiguration();
        uint256 budget;
        for (uint8 i; i < configs.length; ++i) {
            TaskConfig calldata c = configs[i];
            if (
                c.requirementHash == bytes32(0) || c.validationPolicyId == bytes32(0) || c.budgetCap == 0
                    || c.executionWindow == 0 || c.maxAttempts == 0
                    || (uint256(c.depsMask) & ~((uint256(1) << i) - 1)) != 0
            ) revert InvalidConfiguration();
            budget += c.budgetCap;
            _tasks[jobId][i] = Task(
                c.requirementHash,
                c.depsMask,
                c.budgetCap,
                0,
                address(0),
                0,
                0,
                TaskState.READY,
                bytes32(0),
                bytes32(0),
                c.validationPolicyId,
                0,
                c.executionWindow,
                c.maxAttempts
            );
            emit TaskRegistered(jobId, i, c.budgetCap, c.depsMask);
        }
        if (budget > deposit) revert InvalidConfiguration();
        uint256 beforeBalance = paymentToken.balanceOf(address(this));
        paymentToken.safeTransferFrom(msg.sender, address(this), deposit);
        if (paymentToken.balanceOf(address(this)) - beforeBalance != deposit) {
            revert UnsupportedTokenBehavior();
        }
        _jobs[jobId] = Job(
            msg.sender,
            coordinator,
            address(paymentToken),
            deposit,
            deposit,
            budget,
            deadline,
            uint8(configs.length),
            0,
            0,
            JobState.ACTIVE
        );
        totalDeposited += deposit;
        totalAvailable += deposit;
        emit JobCreated(jobId, msg.sender, coordinator, deposit);
    }

    function assignTask(Quote calldata q, bytes calldata signature) external {
        Job storage j = _active(q.jobId);
        _authorize(j);
        Task storage t = _task(j, q.jobId, q.taskId);
        if (block.timestamp >= j.deadline) revert Expired();
        if (t.state != TaskState.READY) revert InvalidState();
        if (
            q.attempt != t.attempt || q.agentWallet == address(0) || q.price == 0 || q.price > t.budgetCap
                || q.requirementHash != t.requirementHash || q.paymentToken != address(paymentToken)
        ) revert InvalidQuote();
        if (q.validUntil <= block.timestamp) revert Expired();
        if ((j.terminalMask & t.depsMask & ~j.settledMask) != 0) revert InvalidState();
        bytes32 digest = hashQuote(q);
        if (ECDSA.recover(digest, signature) != q.agentWallet) revert InvalidSignature();
        if (usedBidNonces[q.agentWallet][q.bidNonce]) revert NonceUsed();
        usedBidNonces[q.agentWallet][q.bidNonce] = true;
        t.state = TaskState.ASSIGNED;
        t.payeeWallet = q.agentWallet;
        t.acceptedPrice = q.price;
        t.quoteHash = digest;
        t.assignedAt = uint64(block.timestamp);
        uint256 limit = block.timestamp + t.executionWindow;
        t.deadline = limit < j.deadline ? uint64(limit) : j.deadline;
        emit TaskAssigned(q.jobId, q.taskId, t.attempt, q.agentWallet, q.price, t.deadline, digest);
    }

    function settleTask(
        Result calldata r,
        bytes calldata agentSignature,
        Attestation calldata a,
        bytes calldata verifierSignature
    ) external {
        Job storage j = _active(r.jobId);
        if (msg.sender != j.owner && msg.sender != j.coordinator && !hasRole(RELAYER_ROLE, msg.sender)) {
            revert Unauthorized();
        }
        Task storage t = _task(j, r.jobId, r.taskId);
        if (t.state != TaskState.ASSIGNED) revert InvalidState();
        if (block.timestamp >= t.deadline || block.timestamp >= j.deadline) revert Expired();
        if (
            r.attempt != t.attempt || r.agentWallet != t.payeeWallet || r.resultHash == bytes32(0)
                || r.artifactURIHash == bytes32(0) || r.submittedAt < t.assignedAt
                || r.submittedAt > block.timestamp || r.submittedAt >= t.deadline
        ) revert InvalidResult();
        if (ECDSA.recover(hashResult(r), agentSignature) != t.payeeWallet) revert InvalidSignature();
        if (
            a.jobId != r.jobId || a.taskId != r.taskId || a.attempt != r.attempt
                || a.resultHash != r.resultHash || a.validationPolicyId != t.validationPolicyId
        ) {
            revert InvalidAttestation();
        }
        if (a.validUntil <= block.timestamp) revert Expired();
        address verifier = ECDSA.recover(hashAttestation(a), verifierSignature);
        if (!hasRole(VERIFIER_ROLE, verifier)) revert Unauthorized();
        if (usedAttestationNonces[verifier][a.attestationNonce]) revert NonceUsed();
        if (a.verdict && (j.settledMask & t.depsMask) != t.depsMask) revert DependenciesUnsettled();
        usedAttestationNonces[verifier][a.attestationNonce] = true;
        if (!a.verdict) {
            emit ValidationFailed(r.jobId, r.taskId, r.attempt, r.resultHash);
            _invalidate(r.jobId, r.taskId, j, t);
            return;
        }
        uint256 amount = t.acceptedPrice;
        if (amount > j.remaining || amount > t.budgetCap) revert InvalidState();
        t.state = TaskState.SETTLED;
        t.resultHash = r.resultHash;
        uint16 bit = uint16(uint256(1) << r.taskId);
        j.settledMask |= bit;
        j.terminalMask |= bit;
        j.remaining -= amount;
        totalAvailable -= amount;
        totalPending += amount;
        withdrawable[t.payeeWallet] += amount;
        emit TaskSettled(r.jobId, r.taskId, r.attempt, t.payeeWallet, amount, r.resultHash, verifier);
    }

    function expireTask(bytes32 jobId, uint8 taskId) external {
        Job storage j = _active(jobId);
        Task storage t = _task(j, jobId, taskId);
        if (t.state != TaskState.ASSIGNED) revert InvalidState();
        if (block.timestamp < t.deadline) revert NotExpired();
        _invalidate(jobId, taskId, j, t);
    }

    function revokeTask(bytes32 jobId, uint8 taskId) external {
        Job storage j = _active(jobId);
        _authorize(j);
        Task storage t = _task(j, jobId, taskId);
        if (t.state != TaskState.ASSIGNED) revert InvalidState();
        _invalidate(jobId, taskId, j, t);
    }

    function cancelTask(bytes32 jobId, uint8 taskId) external {
        Job storage j = _active(jobId);
        _authorize(j);
        Task storage t = _task(j, jobId, taskId);
        if (t.state == TaskState.SETTLED || t.state == TaskState.CANCELED) revert InvalidState();
        _cancel(jobId, taskId, j, t);
        _cascade(jobId, j);
    }

    /// @notice Anyone can release unassigned/stalled tasks after the fixed Job deadline.
    function expireJob(bytes32 jobId) external {
        Job storage j = _active(jobId);
        if (block.timestamp < j.deadline) revert NotExpired();
        for (uint8 i; i < j.taskCount; ++i) {
            Task storage t = _tasks[jobId][i];
            if (t.state == TaskState.READY || t.state == TaskState.ASSIGNED) _cancel(jobId, i, j, t);
        }
    }

    function finalizeJob(bytes32 jobId) external {
        Job storage j = _active(jobId);
        if (uint256(j.terminalMask) != ((uint256(1) << j.taskCount) - 1)) revert InvalidState();
        j.jobState = JobState.FINALIZED;
        uint256 amount = j.remaining;
        j.remaining = 0;
        refundable[jobId] = amount;
        emit JobFinalized(jobId, j.owner, amount);
    }

    function withdraw() external nonReentrant {
        uint256 amount = withdrawable[msg.sender];
        if (amount == 0) revert NothingToWithdraw();
        withdrawable[msg.sender] = 0;
        totalPending -= amount;
        totalWithdrawn += amount;
        paymentToken.safeTransfer(msg.sender, amount);
        emit Withdrawn(msg.sender, amount);
    }

    function withdrawRefund(bytes32 jobId) external nonReentrant {
        Job storage j = _jobs[jobId];
        if (msg.sender != j.owner) revert Unauthorized();
        if (j.jobState != JobState.FINALIZED) revert InvalidState();
        uint256 amount = refundable[jobId];
        if (amount == 0) revert NothingToWithdraw();
        refundable[jobId] = 0;
        totalAvailable -= amount;
        totalRefunded += amount;
        paymentToken.safeTransfer(j.owner, amount);
        emit Refunded(jobId, j.owner, amount);
    }

    function hashQuote(Quote memory q) public view returns (bytes32) {
        return _hashTypedDataV4(
            keccak256(
                abi.encode(
                    QUOTE_TYPEHASH,
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
            )
        );
    }

    function hashResult(Result memory r) public view returns (bytes32) {
        return _hashTypedDataV4(
            keccak256(
                abi.encode(
                    RESULT_TYPEHASH,
                    r.jobId,
                    r.taskId,
                    r.attempt,
                    r.agentWallet,
                    r.resultHash,
                    r.artifactURIHash,
                    r.submittedAt
                )
            )
        );
    }

    function hashAttestation(Attestation memory a) public view returns (bytes32) {
        return _hashTypedDataV4(
            keccak256(
                abi.encode(
                    ATTESTATION_TYPEHASH,
                    a.jobId,
                    a.taskId,
                    a.attempt,
                    a.resultHash,
                    a.validationPolicyId,
                    a.verdict,
                    a.validUntil,
                    a.attestationNonce
                )
            )
        );
    }

    function _active(bytes32 jobId) internal view returns (Job storage j) {
        j = _jobs[jobId];
        if (j.jobState != JobState.ACTIVE) revert InvalidJob();
    }

    function _authorize(Job storage j) internal view {
        if (msg.sender != j.owner && msg.sender != j.coordinator) revert Unauthorized();
    }

    function _task(Job storage j, bytes32 jobId, uint8 taskId) internal view returns (Task storage t) {
        if (taskId >= j.taskCount) revert InvalidJob();
        t = _tasks[jobId][taskId];
    }

    function _invalidate(bytes32 jobId, uint8 taskId, Job storage j, Task storage t) internal {
        uint32 oldAttempt = t.attempt;
        ++t.attempt;
        t.state = TaskState.READY;
        t.acceptedPrice = 0;
        t.payeeWallet = address(0);
        t.quoteHash = bytes32(0);
        t.deadline = 0;
        t.assignedAt = 0;
        emit TaskInvalidated(jobId, taskId, oldAttempt, t.attempt);
        if (t.attempt >= t.maxAttempts || block.timestamp >= j.deadline) {
            _cancel(jobId, taskId, j, t);
            _cascade(jobId, j);
        }
    }

    function _cancel(bytes32 jobId, uint8 taskId, Job storage j, Task storage t) internal {
        // Saturation is safe: CANCELED can never be assigned or settled again.
        if (t.attempt < type(uint32).max) ++t.attempt;
        t.state = TaskState.CANCELED;
        j.terminalMask |= uint16(uint256(1) << taskId);
        emit TaskCanceled(jobId, taskId, t.attempt);
    }

    function _cascade(bytes32 jobId, Job storage j) internal {
        // Dependencies only point backwards, so one forward pass closes the cancellation graph.
        for (uint8 i; i < j.taskCount; ++i) {
            Task storage t = _tasks[jobId][i];
            if (
                t.state != TaskState.SETTLED && t.state != TaskState.CANCELED
                    && (t.depsMask & j.terminalMask & ~j.settledMask) != 0
            ) _cancel(jobId, i, j, t);
        }
    }
}
