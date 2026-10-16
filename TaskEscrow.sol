// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

// TaskEscrow
/// @notice DASC v1; immutable task terms, fixed budgets, independent result/validation signatures.
/// @dev A valid authorized validator can approve false data. This contract cannot detect that case.
contract TaskEscrow is EIP712, ReentrancyGuard {
    using SafeERC20 for IERC20;
    enum State { UNASSIGNED, ASSIGNED, SETTLED, CANCELED }
    struct TaskConfig {
        bytes32 requirementHash;
        uint16 depsMask;
        uint256 budgetCap;
        bytes32 validationPolicyId;
        address validator;
        uint64 slaSeconds;
        uint32 maxAttempts;
    }
    struct Task {
        TaskConfig config;
        address payee;
        uint256 acceptedPrice;
        uint32 attempt;
        uint64 deadline;
        State state;
        bytes32 quoteHash;
        bytes32 resultHash;
    }
    struct Job {
        address owner;
        address coordinator;
        uint256 deposit;
        uint256 reserved;
        uint256 unallocated;
        uint256 agentCredits;
        uint256 refundCredit;
        uint256 withdrawn;
        uint256 refunded;
        uint64 deadline;
        uint8 taskCount;
        uint16 settledMask;
        uint16 terminalMask;
        bool stopping;
        bool finalized;
        bytes32 planHash;
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
        uint8 verdict;
        uint64 validUntil;
        uint256 attestationNonce;
    }

    bytes32 public constant QUOTE_TYPEHASH = keccak256("Quote(bytes32 jobId,uint8 taskId,uint32 attempt,address agentWallet,uint256 price,bytes32 requirementHash,address paymentToken,uint64 validUntil,uint256 bidNonce)");
    bytes32 public constant RESULT_TYPEHASH = keccak256("Result(bytes32 jobId,uint8 taskId,uint32 attempt,address agentWallet,bytes32 resultHash,bytes32 artifactURIHash,uint64 submittedAt)");
    bytes32 public constant ATTESTATION_TYPEHASH = keccak256("Attestation(bytes32 jobId,uint8 taskId,uint32 attempt,bytes32 resultHash,bytes32 validationPolicyId,uint8 verdict,uint64 validUntil,uint256 attestationNonce)");
    IERC20 public immutable token;
    uint256 public totalLiability;
    mapping(bytes32 => Job) private jobs;
    mapping(bytes32 => mapping(uint8 => Task)) private tasks;
    mapping(bytes32 => mapping(address => uint256)) public credits;
    mapping(address => mapping(uint256 => bool)) public usedBidNonce;
    mapping(address => mapping(uint256 => bool)) public usedAttestationNonce;

    error InvalidJob();
    error InvalidPlan();
    error Unauthorized();
    error InvalidState();
    error InvalidSignature();
    error InvalidContext();
    error Expired();
    error NotExpired();
    error BudgetExceeded();
    error DependenciesUnsettled();
    error NonceUsed();
    error NothingToClaim();

    event JobCreated(bytes32 indexed jobId, address indexed owner, uint256 deposit, bytes32 planHash);
    event TaskAssigned(bytes32 indexed jobId, uint8 indexed taskId, uint32 attempt, address indexed payee, uint256 price, uint64 deadline, bytes32 quoteHash);
    event TaskSettled(bytes32 indexed jobId, uint8 indexed taskId, uint32 attempt, address indexed payee, uint256 amount, bytes32 resultHash);
    event AttemptExpired(bytes32 indexed jobId, uint8 indexed taskId, uint32 oldAttempt, uint32 newAttempt);
    event TaskCanceled(bytes32 indexed jobId, uint8 indexed taskId);
    event StopRequested(bytes32 indexed jobId);
    event JobFinalized(bytes32 indexed jobId, uint256 refund);
    event Withdrawn(bytes32 indexed jobId, address indexed payee, uint256 amount);
    event Refunded(bytes32 indexed jobId, address indexed owner, uint256 amount);

    constructor(IERC20 paymentToken) EIP712("DASC", "1") {
        if (address(paymentToken) == address(0)) revert InvalidPlan();
        token = paymentToken;
    }

    function createJob(bytes32 id, address coordinator, uint256 deposit, uint64 deadline,
        bytes32 planHash, TaskConfig[] calldata configs) external nonReentrant {
        if (id == bytes32(0) || jobs[id].owner != address(0)) revert InvalidJob();
        if (coordinator == address(0) || deposit == 0 || deadline <= block.timestamp || configs.length == 0 || configs.length > 16) revert InvalidPlan();
        uint256 reserved = 0;
        for (uint8 i; i < configs.length; ++i) {
            TaskConfig calldata c = configs[i];
            if (c.depsMask & ~uint16((uint256(1) << i) - 1) != 0 || c.validator == address(0) || c.slaSeconds == 0 || c.maxAttempts == 0 || c.requirementHash == bytes32(0) || c.validationPolicyId == bytes32(0)) revert InvalidPlan();
            reserved += c.budgetCap;
            tasks[id][i].config = c;
        }
        if (reserved > deposit) revert BudgetExceeded();
        Job storage j = jobs[id];
        j.owner = msg.sender; j.coordinator = coordinator; j.deposit = deposit;
        j.reserved = reserved; j.unallocated = deposit - reserved;
        j.deadline = deadline; j.taskCount = uint8(configs.length); j.planHash = planHash;
        uint256 beforeBalance = token.balanceOf(address(this));
        totalLiability += deposit;
        token.safeTransferFrom(msg.sender, address(this), deposit);
        if (token.balanceOf(address(this)) - beforeBalance != deposit) revert InvalidPlan();
        emit JobCreated(id, msg.sender, deposit, planHash);
    }

    function assignTask(Quote calldata q, bytes calldata signature) external {
        Job storage j = _job(q.jobId);
        if (msg.sender != j.coordinator) revert Unauthorized();
        Task storage t = _task(q.jobId, q.taskId);
        if (j.stopping || j.finalized || t.state != State.UNASSIGNED || t.attempt >= t.config.maxAttempts) revert InvalidState();
        if (block.timestamp > q.validUntil || block.timestamp + t.config.slaSeconds > j.deadline) revert Expired();
        if (q.attempt != t.attempt || q.requirementHash != t.config.requirementHash || q.paymentToken != address(token) || q.agentWallet == address(0)) revert InvalidContext();
        if (q.price > t.config.budgetCap) revert BudgetExceeded();
        if (usedBidNonce[q.agentWallet][q.bidNonce]) revert NonceUsed();
        bytes32 digest = hashQuote(q);
        if (ECDSA.recover(digest, signature) != q.agentWallet) revert InvalidSignature();
        usedBidNonce[q.agentWallet][q.bidNonce] = true;
        t.payee = q.agentWallet; t.acceptedPrice = q.price;
        t.deadline = uint64(block.timestamp) + t.config.slaSeconds;
        t.quoteHash = digest; t.state = State.ASSIGNED;
        emit TaskAssigned(q.jobId, q.taskId, q.attempt, t.payee, q.price, t.deadline, digest);
    }

    function settleTask(Result calldata r, bytes calldata agentSignature,
        Attestation calldata a, bytes calldata validatorSignature) external {
        Job storage j = _job(r.jobId);
        if (msg.sender != j.owner && msg.sender != j.coordinator) revert Unauthorized();
        Task storage t = _task(r.jobId, r.taskId);
        if (t.state != State.ASSIGNED || j.finalized) revert InvalidState();
        if (block.timestamp > t.deadline || block.timestamp > a.validUntil) revert Expired();
        if (r.submittedAt > block.timestamp || r.submittedAt < t.deadline - t.config.slaSeconds || r.artifactURIHash == bytes32(0)) revert InvalidContext();
        if (r.jobId != a.jobId || r.taskId != a.taskId || r.attempt != t.attempt || a.attempt != t.attempt || r.agentWallet != t.payee || r.resultHash == bytes32(0) || r.resultHash != a.resultHash || a.validationPolicyId != t.config.validationPolicyId || a.verdict != 1) revert InvalidContext();
        if (j.settledMask & t.config.depsMask != t.config.depsMask) revert DependenciesUnsettled();
        if (usedAttestationNonce[t.config.validator][a.attestationNonce]) revert NonceUsed();
        if (ECDSA.recover(hashResult(r), agentSignature) != t.payee || ECDSA.recover(hashAttestation(a), validatorSignature) != t.config.validator) revert InvalidSignature();
        usedAttestationNonce[t.config.validator][a.attestationNonce] = true;
        t.state = State.SETTLED; t.resultHash = r.resultHash;
        uint16 bit = uint16(1) << r.taskId;
        j.settledMask |= bit; j.terminalMask |= bit;
        j.reserved -= t.config.budgetCap;
        j.unallocated += t.config.budgetCap - t.acceptedPrice;
        j.agentCredits += t.acceptedPrice;
        credits[r.jobId][t.payee] += t.acceptedPrice;
        emit TaskSettled(r.jobId, r.taskId, r.attempt, t.payee, t.acceptedPrice, r.resultHash);
    }

    function expireTask(bytes32 id, uint8 taskId, uint32 expectedAttempt) external {
        _job(id);
        Task storage t = _task(id, taskId);
        if (t.state != State.ASSIGNED || t.attempt != expectedAttempt) revert InvalidState();
        if (block.timestamp <= t.deadline) revert NotExpired();
        uint32 old = t.attempt;
        t.attempt += 1; t.state = State.UNASSIGNED;
        t.payee = address(0); t.acceptedPrice = 0; t.deadline = 0; t.quoteHash = bytes32(0);
        emit AttemptExpired(id, taskId, old, t.attempt);
    }

    function requestStop(bytes32 id) external {
        Job storage j = _job(id);
        if (msg.sender != j.owner) revert Unauthorized();
        if (j.finalized) revert InvalidState();
        j.stopping = true;
        emit StopRequested(id);
    }

    function cancelTask(bytes32 id, uint8 taskId) external {
        Job storage j = _job(id);
        Task storage t = _task(id, taskId);
        if (t.state != State.UNASSIGNED || j.finalized) revert InvalidState();
        uint16 canceledMask = j.terminalMask & ~j.settledMask;
        bool publiclyCancelable = block.timestamp > j.deadline || canceledMask & t.config.depsMask != 0;
        if (!publiclyCancelable && msg.sender != j.owner && msg.sender != j.coordinator) revert Unauthorized();
        t.state = State.CANCELED;
        j.terminalMask |= uint16(1) << taskId;
        j.reserved -= t.config.budgetCap; j.unallocated += t.config.budgetCap;
        emit TaskCanceled(id, taskId);
    }

    function finalizeJob(bytes32 id) external {
        Job storage j = _job(id);
        if (j.finalized || j.terminalMask != uint16((uint256(1) << j.taskCount) - 1)) revert InvalidState();
        j.finalized = true; j.refundCredit = j.unallocated; j.unallocated = 0;
        emit JobFinalized(id, j.refundCredit);
    }

    function withdraw(bytes32 id) external nonReentrant {
        Job storage j = _job(id);
        uint256 amount = credits[id][msg.sender];
        if (amount == 0) revert NothingToClaim();
        credits[id][msg.sender] = 0; j.agentCredits -= amount; j.withdrawn += amount;
        totalLiability -= amount;
        token.safeTransfer(msg.sender, amount);
        emit Withdrawn(id, msg.sender, amount);
    }

    function claimRefund(bytes32 id) external nonReentrant {
        Job storage j = _job(id);
        if (msg.sender != j.owner) revert Unauthorized();
        uint256 amount = j.refundCredit;
        if (amount == 0) revert NothingToClaim();
        j.refundCredit = 0; j.refunded += amount; totalLiability -= amount;
        token.safeTransfer(j.owner, amount);
        emit Refunded(id, j.owner, amount);
    }

    function hashQuote(Quote calldata q) public view returns (bytes32) {
        return _hashTypedDataV4(keccak256(abi.encode(QUOTE_TYPEHASH, q)));
    }
    function hashResult(Result calldata r) public view returns (bytes32) {
        return _hashTypedDataV4(keccak256(abi.encode(RESULT_TYPEHASH, r)));
    }
    function hashAttestation(Attestation calldata a) public view returns (bytes32) {
        return _hashTypedDataV4(keccak256(abi.encode(ATTESTATION_TYPEHASH, a)));
    }
    function getJob(bytes32 id) external view returns (Job memory) { return _job(id); }
    function getTask(bytes32 id, uint8 taskId) external view returns (Task memory) { return _task(id, taskId); }
    function _job(bytes32 id) private view returns (Job storage j) {
        j = jobs[id]; if (j.owner == address(0)) revert InvalidJob();
    }
    function _task(bytes32 id, uint8 taskId) private view returns (Task storage t) {
        if (taskId >= jobs[id].taskCount) revert InvalidPlan();
        t = tasks[id][taskId];
    }
}
