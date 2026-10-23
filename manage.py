"""Portable build and service entry point for AgentFlow Pay."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import platform
import shutil
import signal
import statistics
import subprocess
import sys
import sysconfig
import tarfile
import time
import urllib.request
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = (ROOT / os.environ.get("AGENTFLOW_BUILD_DIR", ".cache")).resolve()
PYTHON = CACHE / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
TOOLS = CACHE / "tools" / "foundry"
FOUNDRY_VERSION = "1.5.1"
WINDOWS_ARCHIVE_SHA256 = "293fedd9ad45eb9d774008815dbda820bddd3c85cab3baf55a0be3775e86d5ae"
LINUX_ARCHIVE_SHA256 = "73640b01bd9ed29fdb4965085099371f8cf0dbbec3e2086cf54564efc4dcfe88"
NODE_VERSION = "24.19.0"
PNPM_VERSION = "11.25.0"
NODE_ARCHIVE_SHA256 = {
    "win-x64.zip": "57f71ab3652e797d84acddc79c81cc9ff1c6ddb2a1974cdb83f00fee9bff4c73",
    "linux-x64.tar.gz": "f625d97cd707df4ff96254916fbc5ff014f09c09effe5a1e0ca8f6d41a8789d4",
}
PNPM_ARCHIVE_SHA512 = "XN6SW08HX3Jetx+64YpC/+eEUkeJ8ZthxzHLhyHsKKruFg4BqNWvT+2ypCzb8wDv4j2zVrDUoXtNY+EfirfJVg=="


def run(command, cwd=ROOT, **kwargs):
    return subprocess.run([str(x) for x in command], cwd=cwd, check=True, **kwargs)

def pnpm():
    """Use project-local tools, so Explorer does not depend on an editor's PATH."""
    if os.environ.get("AGENTFLOW_PNPM"):
        return os.environ["AGENTFLOW_PNPM"]
    directory = CACHE / "tools/web"
    node = directory / "node" / ("node.exe" if os.name == "nt" else "node")
    entry = directory / "pnpm/bin/pnpm.mjs"
    if not (node.is_file() and entry.is_file()):
        # CI and ordinary terminals may already have a complete toolchain.
        executable = shutil.which("pnpm")
        if executable and shutil.which("node"):
            return executable
        install_web_tools(directory)
    os.environ["PATH"] = str(node.parent) + os.pathsep + os.environ.get("PATH", "")
    # The pnpm package already occupies directory/pnpm on every platform.
    wrapper = directory / ("pnpm.cmd" if os.name == "nt" else "pnpm-run")
    if os.name == "nt":
        content = '@echo off\n"%~dp0node\\node.exe" "%~dp0pnpm\\bin\\pnpm.mjs" %*\n'
    else:
        # Shell expansion works even when PATH contains only our bundled Node.
        content = '#!/bin/sh\nexec "${0%/*}/node/node" "${0%/*}/pnpm/bin/pnpm.mjs" "$@"\n'
    wrapper.write_text(content, encoding="utf-8")
    if os.name != "nt":
        wrapper.chmod(0o755)
    return str(wrapper)


def verified_web_download(url, algorithm, expected):
    try:
        with urllib.request.urlopen(url, timeout=45) as response:
            chunks = []
            received = 0
            last_progress = time.monotonic()
            while chunk := response.read(256 * 1024):
                chunks.append(chunk)
                received += len(chunk)
                if time.monotonic() - last_progress >= 5:
                    print(f"正在下载 {url.rsplit('/', 1)[-1]}：{received / 1024 / 1024:.1f} MB", flush=True)
                    last_progress = time.monotonic()
            data = b"".join(chunks)
    except OSError as exc:
        raise RuntimeError("前端工具下载失败，请联网后重新双击启动脚本；原有项目数据未改动。") from exc
    if hashlib.new(algorithm, data).digest() != expected:
        raise RuntimeError("前端工具下载校验失败，请重新启动以重试下载。")
    return data


def install_web_tools(directory):
    """Install pinned tools without administrator rights or global PATH changes."""
    if platform.system() not in {"Windows", "Linux"} or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("此平台请先安装 Node.js 24 和 pnpm 11.25.0，再启动项目。")
    suffix = "win-x64.zip" if os.name == "nt" else "linux-x64.tar.gz"
    name = f"node-v{NODE_VERSION}-{suffix}"
    print(f"正在下载项目自用 Node.js {NODE_VERSION} 和 pnpm {PNPM_VERSION}（仅首次需要）…", flush=True)
    node_archive = verified_web_download(
        f"https://nodejs.org/dist/v{NODE_VERSION}/{name}", "sha256", bytes.fromhex(NODE_ARCHIVE_SHA256[suffix]))
    pnpm_archive = verified_web_download(
        f"https://registry.npmjs.org/pnpm/-/pnpm-{PNPM_VERSION}.tgz", "sha512", base64.b64decode(PNPM_ARCHIVE_SHA512))
    node = directory / "node" / ("node.exe" if os.name == "nt" else "node")
    if os.name == "nt":
        with zipfile.ZipFile(io.BytesIO(node_archive)) as archive:
            binary = archive.read(f"node-v{NODE_VERSION}-win-x64/node.exe")
    else:
        with tarfile.open(fileobj=io.BytesIO(node_archive), mode="r:gz") as archive:
            binary = archive.extractfile(f"node-v{NODE_VERSION}-linux-x64/bin/node").read()
    node.parent.mkdir(parents=True, exist_ok=True)
    # The entry point is published last; interrupted installs will be retried.
    with tarfile.open(fileobj=io.BytesIO(pnpm_archive), mode="r:gz") as archive:
        files = []
        for member in archive.getmembers():
            parts = member.name.split("/")
            if member.isdir():
                continue
            if not member.isfile() or parts[0] != "package" or any(p in {"", ".", ".."} or ":" in p or "\\" in p for p in parts):
                raise RuntimeError("前端工具压缩包含有不安全的路径。")
            files.append((member, directory.joinpath("pnpm", *parts[1:])))
        for member, target in sorted(files, key=lambda item: item[1].name == "pnpm.mjs"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    node.write_bytes(binary)
    if os.name != "nt":
        node.chmod(0o755)
    print("前端工具已就绪，保存在本项目 .cache/tools/web。", flush=True)


def install_web_dependencies(force=False):
    web = ROOT / "apps/web"
    fingerprint = hashlib.sha256(b"".join((web / name).read_bytes()
        for name in ("package.json", "pnpm-lock.yaml"))).hexdigest()
    marker = web / "node_modules/.agentflow-dependencies"
    if force or not marker.is_file() or marker.read_text() != fingerprint:
        executable = pnpm()
        print("正在安装或更新页面依赖…", flush=True)
        run([executable, "install", "--frozen-lockfile"], cwd=web)
        marker.write_text(fingerprint)

def source_revision() -> str:
    configured = os.environ.get("AGENTFLOW_SOURCE_COMMIT_SHA")
    if configured:
        return configured
    if (ROOT / ".git").exists() and shutil.which("git"):
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    return "unversioned"


def foundry(name):
    local = TOOLS / (name + (".exe" if os.name == "nt" else ""))
    executable = str(local) if local.exists() else shutil.which(name)
    if not executable:
        raise RuntimeError("Foundry missing. Run setup or install Foundry v1.5.1.")
    return executable

def foundry_archive_spec(system=None, machine=None, platform_id=None):
    system = system or platform.system()
    machine = (machine or platform.machine() or "").strip().lower()
    if not machine:
        platform_id = platform_id or sysconfig.get_platform()
        machine = platform_id.strip().lower().rsplit("-", 1)[-1]
    if system not in {"Windows", "Linux"} or machine not in {"amd64", "x86_64"}:
        raise RuntimeError("Install Foundry v1.5.1 for this platform, then rerun setup; see README.md")
    if system == "Windows":
        return "win32_amd64.zip", WINDOWS_ARCHIVE_SHA256
    return "linux_amd64.tar.gz", LINUX_ARCHIVE_SHA256

def install_foundry():
    forge_available = (TOOLS / ("forge.exe" if os.name == "nt" else "forge")).exists() or shutil.which("forge")
    anvil_available = (TOOLS / ("anvil.exe" if os.name == "nt" else "anvil")).exists() or shutil.which("anvil")
    if forge_available and anvil_available:
        version = run([foundry("forge"), "--version"], capture_output=True, text=True).stdout
        if "Version: 1.5.1" not in version:
            raise RuntimeError("Expected Foundry 1.5.1, found: " + version.splitlines()[0])
        return
    TOOLS.mkdir(parents=True, exist_ok=True)
    asset, checksum = foundry_archive_spec()
    archive = TOOLS.parent / ("foundry_v1.5.1_" + asset)
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
        url = "https://github.com/foundry-rs/foundry/releases/download/v1.5.1/" + archive.name
        print("Downloading pinned Foundry v1.5.1 archive", flush=True)
        with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as target:
            shutil.copyfileobj(response, target)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
        raise RuntimeError("Foundry archive SHA-256 mismatch; extraction refused")
    if os.name == "nt":
        with zipfile.ZipFile(archive) as package:
            for name in ("forge.exe", "anvil.exe", "cast.exe"):
                matches = [i for i in package.infolist() if Path(i.filename).name == name]
                if len(matches) != 1:
                    raise RuntimeError("unexpected Foundry archive contents")
                (TOOLS / name).write_bytes(package.read(matches[0]))
    else:
        with tarfile.open(archive, "r:gz") as package:
            for name in ("forge", "anvil", "cast"):
                matches = [i for i in package.getmembers() if Path(i.name).name == name and i.isfile()]
                if len(matches) != 1:
                    raise RuntimeError("unexpected Foundry archive contents")
                with package.extractfile(matches[0]) as source:
                    (TOOLS / name).write_bytes(source.read())
                (TOOLS / name).chmod(0o755)

def install_contract_libraries():
    from scripts.contract_dependencies import install
    install(ROOT)


def prepare_generated():
    """Compatibility hook: native source files are authoritative, only tests are synchronized."""
    browser_tests = ROOT / "tests/e2e/browser"
    if browser_tests.is_dir():
        shutil.copytree(browser_tests, ROOT / "apps/web/tests", dirs_exist_ok=True)


SECURITY_CASES = {
    "SEC-01": ("报价金额篡改", ["testQuotePriceTamperingIsRejected"], "签名后增加报价金额", "拒绝报价且任务保持 READY"),
    "SEC-02": ("报价执行者篡改", ["testQuoteAgentWalletTamperingIsRejected"], "签名后替换 Agent 钱包", "签名校验拒绝且不改变任务"),
    "SEC-03": ("报价任务 ID 替换", ["testQuoteTaskIdSubstitutionIsRejected"], "将 task 0 报价改用于 task 1", "签名校验拒绝跨任务使用"),
    "SEC-04": ("报价 Job ID 替换", ["testQuoteJobIdSubstitutionIsRejected"], "将已签报价改用于另一 Job", "签名校验拒绝跨 Job 使用"),
    "SEC-05": ("报价跨合约重放", ["testCrossContractQuoteReplayIsRejected"], "在另一合约域签名后提交给本合约", "EIP-712 域校验拒绝"),
    "SEC-06": ("报价跨链重放", ["testQuoteCrossChainReplayIsRejected"], "签名后切换 chainId 并提交报价", "EIP-712 域校验拒绝"),
    "SEC-07": ("过期报价使用", ["testExpiredQuoteIsRejected"], "提交 validUntil 已过期的报价", "拒绝报价"),
    "SEC-08": ("报价 nonce 重放", ["testBidNonceCannotBeReusedByTheSameAgent"], "同一 Agent 重用已消费 bidNonce", "拒绝重放"),
    "SEC-09": ("Agent 冒用服务身份", [], "服务端点冒称其他钱包且不能提供对应签名", "注册挑战必须拒绝"),
    "SEC-10": ("虚假服务能力", [], "Agent 声明技能但提交不符合规则的成果", "独立客观验证拒绝且不付款"),
    "SEC-11": ("结果内容改包", ["testResultTamperingIsRejected"], "更改结果摘要但保留原 Agent 签名", "拒绝结算"),
    "SEC-12": ("伪造结果签名", ["testForgedResultSignatureIsRejected"], "使用非执行者钱包签署结果", "拒绝结算且不付款"),
    "SEC-13": ("旧任务结果用于新任务", ["testResultFromDifferentTaskIsRejected"], "把 task 0 结果和签名提交给 task 1", "拒绝跨任务重放"),
    "SEC-14": ("旧轮次重放", ["testOldAttemptCannotSettleAfterExpiryAndReassignment"], "attempt 0 失效并重派后提交旧证明", "旧轮次不得结算"),
    "SEC-15": ("超时后的迟交", ["testOldAttemptCannotSettleAfterExpiryAndReassignment"], "expireTask 后提交旧 Agent 证明", "失效轮次不得收款"),
    "SEC-16": ("截止边界竞争", ["testSettleBeforeDeadlineWinsAndCannotExpireAfterward"], "在截止前一个区块时间完成结算并尝试失效", "只保留一个最终状态且不双付"),
    "SEC-17": ("未完成依赖提前结算", ["testDependencyCannotSettleBeforeParents"], "父任务未结算时尝试结算子任务", "依赖检查拒绝结算"),
    "SEC-18": ("非法 DAG", ["testSelfDependencyConfigurationIsRejected"], "登记自依赖节点", "配置阶段拒绝"),
    "SEC-19": ("伪造验证者", ["testUnauthorizedVerifierCannotSettle"], "未授权钱包签署验证证明", "拒绝结算"),
    "SEC-20": ("验证证明跨结果使用", ["testAttestationForDifferentResultIsRejected"], "证明 resultHash 与执行结果不一致", "拒绝结算"),
    "SEC-21": ("验证证明跨轮次使用", ["testOldAttemptAttestationIsRejectedForNewAttempt"], "将 attempt 0 验证证明用于 attempt 1", "拒绝结算"),
    "SEC-22": ("验证凭证重复使用", ["testAttestationNonceCannotBeReused", "testDuplicateSettlementCannotPayTwice"], "重用验证 nonce 或重复结算", "至多结算付款一次"),
    "SEC-23": ("未授权结算或退款", ["testStrangerCannotSettleTask", "testNonOwnerCannotWithdrawRefund"], "随机外部账户调用结算或退款", "权限检查拒绝且资金不变"),
    "SEC-24": ("未授权修改执行者", ["testNonCoordinatorCannotAssignTask"], "非 owner/coordinator 调用任务分配", "权限检查拒绝"),
    "SEC-25": ("超预算报价", ["testFuzzPriceNeverExceedsTaskCap"], "对报价金额做 256 轮 fuzz", "超过任务预算的报价全部拒绝"),
    "SEC-26": ("多任务预算超额", ["testOvercommittedBudgetsAreRejected"], "登记的任务预算总和超过托管金额", "创建 Job 失败"),
    "SEC-27": ("重复退款", ["testRefundCannotBeClaimedTwice"], "重复领取同一 Job 退款", "第二次领取拒绝且不重复转账"),
    "SEC-28": ("恶意代币回调或重入", [], "恶意 token 或回调收款器尝试重入", "本期仅支持标准无手续费 ERC-20；需独立恶意 token 用例"),
    "SEC-29": ("数据库重启后重复结算", [], "重启协调服务后重放已提交结算凭证", "链上状态仍至多付款一次；需应用层故障注入"),
    "SEC-30": ("授权验证者恶意批准坏结果", [], "有权验证者故意签署错误 verdict", "已知信任限制；P0 不宣称可自动阻断"),
}


def write_security_evidence(cases, result_path: Path, *, commit_sha=None, destination=None) -> None:
    test_results = {}
    for contract, name, case in cases:
        # Foundry JSON uses Solidity signatures (for example testCase() and
        # testFuzzCase(uint256)); SECURITY_CASES stores the function names.
        test_results[name.split("(", 1)[0]] = {"contract": contract, **case}
    commit = commit_sha or source_revision()
    destination = destination or ROOT / "experiments/raw/security"
    destination.mkdir(parents=True, exist_ok=True)
    for case_id, (title, functions, action, expected) in SECURITY_CASES.items():
        mapped = [test_results[name] for name in functions if name in test_results]
        if case_id == "SEC-30":
            status, reason = "LIMITATION", "有权限的验证者可以对主观质量作出错误判断；这是明确披露的信任边界。"
        elif not mapped:
            status, reason = "SKIP", "本次 Foundry 输出没有对应的自动化测试用例。"
        elif len(mapped) != len(functions):
            status, reason = "SKIP", "组合场景只执行了部分映射的测试函数。"
        else:
            status = "PASS" if all(item.get("status") == "Success" for item in mapped) else "FAIL"
            reason = None if status == "PASS" else "至少一个对应 Foundry 用例未通过。"
        evidence = {
            "case_id": case_id, "title": title, "scenario": "cross_org_analysis",
            "commit_sha": commit, "chain_id": None, "contract_address": None,
            "seed": next((item.get("counterexample") for item in mapped if item.get("counterexample")), None),
            "initial_state": "Foundry 隔离 EVM；具体状态由对应测试函数创建。",
            "action": action, "expected": expected, "observed": [
                {"test": name, "status": test_results.get(name, {}).get("status"),
                 "reason": test_results.get(name, {}).get("reason")} for name in functions
            ],
            "status": status, "skip_reason": reason,
            "transaction_hashes": [], "execution_environment": "Foundry unit test; no persistent chain transaction",
            "raw_log": result_path.relative_to(ROOT).as_posix(),
        }
        (destination / f"{case_id}.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_contracts():
    prepare_generated()
    result_path = ROOT / "experiments/raw/forge/forge-results.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([foundry("forge"), "test", "--json"], cwd=ROOT / "contracts",
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    result_path.write_text(result.stdout, encoding="utf-8")
    result_path.with_suffix(".stderr.txt").write_text(result.stderr, encoding="utf-8")
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Foundry returned invalid JSON; inspect " + str(result_path)) from exc
    cases = [(contract, name, case) for contract, suite in report.items()
             for name, case in suite.get("test_results", {}).items()]
    write_security_evidence(cases, result_path)
    failures = [(contract, name, case.get("reason")) for contract, name, case in cases
                if case.get("status") != "Success"]
    if result.returncode or failures:
        for contract, name, reason in failures:
            print(f"FAIL {contract}::{name}: {reason}")
        raise RuntimeError("Foundry tests failed; inspect " + str(result_path))
    print(f"Foundry contract tests passed: {len(cases)} cases; raw output saved to {result_path}")


def package_delivery():
    from scripts.package_submission import run as package
    package()


def archive_evidence(run_commit_sha: str | None = None):
    summary_path = ROOT / "experiments/results/manifest.json"
    if not summary_path.is_file():
        raise RuntimeError("Collect results before archiving evidence")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("status") != "PASS":
        raise RuntimeError("Evidence summary must report PASS before archiving")
    current_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                    capture_output=True, text=True).stdout.strip()
    if summary.get("commit_sha") != current_commit:
        raise RuntimeError("Evidence summary commit differs from HEAD; rerun collect-results")
    if run_commit_sha:
        run_commit_sha = subprocess.run(["git", "rev-parse", "--verify", run_commit_sha + "^{commit}"],
                                        cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    files = []
    for folder in (ROOT / "experiments/raw", ROOT / "experiments/results"):
        if folder.is_dir():
            for path in folder.rglob("*"):
                if path.is_symlink():
                    raise RuntimeError("Evidence archive cannot include symlinks: " + str(path))
                if path.is_file():
                    files.append(path)
    if not files:
        raise RuntimeError("No raw or summarized evidence to archive")
    checksums = {}
    e2e_run_provenance = {}
    package_buffer = io.BytesIO()
    prefix = f"{ROOT.name}-evidence-{current_commit[:12]}/"
    with zipfile.ZipFile(package_buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(files, key=lambda item: item.relative_to(ROOT).as_posix()):
            relative = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            checksums[relative] = hashlib.sha256(content).hexdigest()
            if relative.startswith("experiments/raw/e2e/") and relative.endswith("/result.json"):
                record = json.loads(content)
                e2e_run_provenance[record["run_id"]] = {
                    "source_commit_sha": record.get("source_commit_sha") or run_commit_sha,
                    "source_worktree_dirty": record.get("source_worktree_dirty")}
            info = zipfile.ZipInfo(prefix + relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(info, content)
        manifest = {"analysis_commit_sha": current_commit,
                    "declared_legacy_run_commit_sha": run_commit_sha,
                    "e2e_run_provenance": e2e_run_provenance,
                    "summary": summary, "sha256_by_path": checksums}
        info = zipfile.ZipInfo(prefix + "EVIDENCE-MANIFEST.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        package.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    destination = CACHE / "delivery"
    destination.mkdir(parents=True, exist_ok=True)
    filename = f"{ROOT.name}-evidence-{current_commit[:12]}.zip"
    archive = destination / filename
    archive.write_bytes(package_buffer.getvalue())
    digest = hashlib.sha256(package_buffer.getvalue()).hexdigest()
    (destination / "EVIDENCE-SHA256SUMS").write_text(f"{digest}  {filename}\n", encoding="utf-8")
    print(json.dumps({"evidence_archive": str(archive), "sha256": digest,
                      "files": len(files), "analysis_commit_sha": current_commit,
                      "declared_legacy_run_commit_sha": run_commit_sha}, ensure_ascii=False))


def run_e2e(scenario: str, repeat: int = 1, *, browser: bool = False) -> None:
    command = [PYTHON, ROOT / "tests/e2e/run_main_scenario.py", "--scenario", scenario,
               "--repeat", repeat]
    if browser:
        command.append("--browser")
    run(command)


def collect_results(root: Path | None = None, commit_sha: str | None = None):
    root = root or ROOT
    raw = root / "experiments/raw"
    destination = root / "experiments/results"
    e2e_rows = []
    batch_runs = {}
    e2e_root = raw / "e2e"
    if e2e_root.is_dir():
        for batch_path in sorted(e2e_root.glob("batch-*/summary.json")):
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            for item in batch.get("runs", []):
                row = {"scenario": batch.get("scenario"), "batch_id": batch.get("batch_id"),
                       "iteration": item.get("iteration"), "run_id": item.get("run_id"),
                       "variant": "browser" if batch.get("browser") and item.get("iteration") == 1 else "api",
                       "status": item.get("status"),
                       "duration_seconds": item.get("duration_seconds"),
                       "failure": item.get("failure")}
                run_id = row["run_id"]
                if run_id is not None:
                    if run_id in batch_runs:
                        raise RuntimeError("duplicate E2E run ID in batch summaries: " + str(run_id))
                    batch_runs[run_id] = row
                e2e_rows.append(row)
        for result_path in sorted(e2e_root.glob("*/result.json")):
            if result_path.parent.name.startswith("batch-"):
                continue
            item = json.loads(result_path.read_text(encoding="utf-8"))
            run_id = item.get("run_id")
            variant = "browser" if item.get("browser") or any(
                job.get("label") == "browser" for job in item.get("jobs", [])) else "api"
            if run_id in batch_runs:
                batch_row = batch_runs[run_id]
                for field in ("scenario", "status", "duration_seconds", "failure"):
                    if item.get(field) != batch_row[field]:
                        raise RuntimeError(f"E2E batch/raw mismatch for {run_id}: {field}")
                batch_row["variant"] = variant
                continue
            e2e_rows.append({"scenario": item.get("scenario"), "batch_id": None, "iteration": None,
                             "run_id": run_id, "variant": variant, "status": item.get("status"),
                             "duration_seconds": item.get("duration_seconds"),
                             "failure": item.get("failure")})
    security_rows = []
    security_root = raw / "security"
    if security_root.is_dir():
        for case_path in sorted(security_root.glob("SEC-*.json")):
            item = json.loads(case_path.read_text(encoding="utf-8"))
            security_rows.append({"case_id": item.get("case_id"), "title": item.get("title"),
                                  "status": item.get("status"), "skip_reason": item.get("skip_reason"),
                                  "commit_sha": item.get("commit_sha"), "raw_log": item.get("raw_log")})
    destination.mkdir(parents=True, exist_ok=True)
    e2e_fields = ("scenario", "batch_id", "iteration", "run_id", "variant", "status", "duration_seconds", "failure")
    security_fields = ("case_id", "title", "status", "skip_reason", "commit_sha", "raw_log")
    for filename, rows, fields in (("e2e-summary.csv", e2e_rows, e2e_fields),
                                   ("security-summary.csv", security_rows, security_fields)):
        with (destination / filename).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    commit_sha = commit_sha or source_revision()
    durations = {}
    for row in e2e_rows:
        if row["status"] == "PASS" and row["duration_seconds"] is not None:
            cohort = row["scenario"] + "/" + row["variant"]
            durations.setdefault(cohort, []).append(float(row["duration_seconds"]))
    statuses = [row["status"] for row in e2e_rows] + [row["status"] for row in security_rows]
    if not statuses:
        summary_status = "NO_DATA"
    elif any(row["status"] != "PASS" for row in e2e_rows) or any(
        row["status"] not in {"PASS", "SKIP", "LIMITATION"} for row in security_rows
    ):
        summary_status = "FAIL"
    else:
        summary_status = "PASS"
    manifest = {"status": summary_status, "commit_sha": commit_sha,
                "e2e_runs": len(e2e_rows),
                "e2e_status_counts": {status: sum(row["status"] == status for row in e2e_rows)
                                      for status in sorted({row["status"] for row in e2e_rows})},
                "e2e_duration_seconds_by_variant": {
                    cohort: {"n": len(values), "mean": round(statistics.mean(values), 3),
                               "median": round(statistics.median(values), 3),
                               "min": min(values), "max": max(values)}
                    for cohort, values in sorted(durations.items())},
                "security_cases": len(security_rows),
                "security_status_counts": {status: sum(row["status"] == status for row in security_rows)
                                            for status in sorted({row["status"] for row in security_rows})},
                "outputs": ["e2e-summary.csv", "security-summary.csv"]}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return manifest


def reset_demo(runtime: Path | None = None, cache: Path | None = None):
    cache = (cache or CACHE).resolve()
    if runtime is None:
        from app import settings_file
        runtime = settings_file().get("directory")
    demo = Path(runtime).resolve()
    if demo == cache or not demo.is_relative_to(cache):
        raise RuntimeError("refusing to reset a runtime directory outside the dedicated cache")
    backup = None
    if demo.exists():
        backup_root = cache / "demo-backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = backup_root / time.strftime("demo-%Y%m%d-%H%M%S")
        if backup.exists():
            backup = backup.with_name(backup.name + "-" + str(uuid.uuid4())[:8])
        shutil.move(str(demo), str(backup))
    demo.mkdir(parents=True, exist_ok=True)
    result = {"status": "PASS", "reset_directory": str(demo),
              "backup_directory": str(backup) if backup else None}
    print(json.dumps(result, ensure_ascii=False))
    return result


def build():
    prepare_generated()
    run([foundry("forge"), "build", "--skip", "test", "--skip", "script"], cwd=ROOT / "contracts")
    run([pnpm(), "run", "build"], cwd=ROOT / "apps/web")


def setup():
    if not ((3, 11) <= sys.version_info[:2] <= (3, 13)):
        raise RuntimeError("Use Python 3.11, 3.12 or 3.13")
    if not PYTHON.exists():
        venv.EnvBuilder(with_pip=True).create(CACHE / "venv")
    run([PYTHON, "-m", "pip", "install", "-r", ROOT / "requirements.lock"])
    install_foundry()
    install_contract_libraries()
    prepare_generated()
    install_web_dependencies(force=True)
    build()


def main():
    parser = argparse.ArgumentParser(description="AgentFlow build, configuration and business services")
    parser.add_argument("command", choices=["setup", "build", "test-contract", "test-security", "test-e2e", "test-browser-live", "run-data",
                        "run-travel", "collect-results", "reset-demo", "package", "schema", "configure",
                        "approve", "start", "archive-evidence", "check", "chain-up", "deploy", "dev", "stop-chain", "save-chain", "test-integration"], nargs="?", default="dev")
    parser.add_argument("--output", type=Path, help="schema export path, relative to the project directory")
    parser.add_argument("--config", type=Path, help="configuration path, resolved against the project directory")
    parser.add_argument("--runtime", type=Path, help="runtime path, resolved against the configuration directory")
    parser.add_argument("--amount", help="exact six-decimal token allowance for approve")
    parser.add_argument("--run-commit", help="commit used for raw E2E and Foundry runs when archiving evidence")
    args = parser.parse_args()
    if args.command in {"chain-up", "deploy", "dev", "stop-chain", "save-chain"}:
        run([PYTHON if PYTHON.is_file() else sys.executable, ROOT / "scripts/local_demo.py", args.command])
        return
    if args.command == "test-integration":
        run([PYTHON if PYTHON.is_file() else sys.executable, "-m", "pytest", "-q"])
        return
    if args.command == "setup":
        setup()
        return
    if Path(sys.executable).resolve() != PYTHON.resolve() and PYTHON.is_file():
        raise SystemExit(subprocess.call([str(PYTHON), str(ROOT / "manage.py"), *sys.argv[1:]], cwd=ROOT))
    if args.command == "build":
        build()
        return
    if args.command == "test-contract":
        test_contracts()
        return
    if args.command == "test-security":
        test_contracts()
        run([PYTHON, ROOT / "scripts/local_demo.py", "chain-up"])
        run([PYTHON, ROOT / "scripts/module_c.py", "deploy"])
        run([PYTHON, ROOT / "scripts/module_c.py", "test-security"])
        run([PYTHON, ROOT / "tests/e2e/run_platform_acceptance.py"])
        run([PYTHON, ROOT / "scripts/collect_unified_results.py"])
        return
    if args.command == "test-e2e":
        run_e2e("cross_org_analysis", repeat=10)
        return
    if args.command == "test-browser-live":
        run_e2e("cross_org_analysis", browser=True)
        return
    if args.command == "run-data":
        run_e2e("cross_org_analysis")
        return
    if args.command == "run-travel":
        run_e2e("travel_planning")
        return
    if args.command == "collect-results":
        collect_results()
        run([PYTHON, ROOT / "scripts/collect_unified_results.py"])
        return
    if args.command == "reset-demo":
        from app import settings_file
        reset_demo(settings_file(args.config, args.runtime)["directory"])
        return
    if args.command == "package":
        package_delivery()
        return
    if args.command == "archive-evidence":
        archive_evidence(args.run_commit)
        return
    if args.command == "schema":
        from app import (
            TravelInput,
            plan_schema_document,
            protocol_schema_document,
            runtime_schema_document,
        )
        destination = (ROOT / (args.output or Path(".cache/interfaces.json"))).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        document = {"plan": plan_schema_document(), "runtime": runtime_schema_document(),
                    "protocol": protocol_schema_document(), "travelInput": TravelInput.model_json_schema()}
        from services.orchestrator.planning import GoalDraft
        from services.orchestrator.templates import TaskTemplate
        from services.orchestrator.catalog import CorpusImport
        from services.orchestrator.workspace import Recipe
        document["workspace"] = {"recipe": Recipe.model_json_schema(), "goalDraft": GoalDraft.model_json_schema(),
                                 "template": TaskTemplate.model_json_schema(), "serviceCorpus": CorpusImport.model_json_schema()}
        destination.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"exported": str(destination), "protocolVersion": 1, "status": "local_proposal"}))
        return
    from app import (
        Chain,
        ServiceCluster,
        configure_deployment,
        runtime_config,
        settings_file,
        units,
    )
    settings = settings_file(args.config, args.runtime)
    if args.command == "check":
        from app import check_services
        config = runtime_config(settings, "api")
        print(json.dumps({"compatible": True, "services": check_services(config)}, ensure_ascii=False))
    elif args.command == "configure":
        deployment = configure_deployment(settings)
        print(json.dumps({"configured": True, "chainId": deployment["chainId"], "escrow": deployment["escrow"]}))
    elif args.command == "approve":
        if args.amount is None:
            parser.error("approve requires --amount; no unlimited allowance is granted automatically")
        amount = units(args.amount)
        config = runtime_config(settings, "api")
        chain = Chain(config["deployment"]["rpc"], settings["directory"], config["deployment"])
        nonce = chain.w3.eth.get_transaction_count(config["deployment"]["owner"], "pending")
        receipt = chain.tx(chain.token.functions.approve(chain.escrow.address, amount), config["signers"]["owner"],
                           f"approve:{chain.escrow.address}:{amount}:{nonce}")
        print(json.dumps({"approved": str(amount), "transactionHash": receipt["transactionHash"]}))
    else:
        def interrupted(signum, frame):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            with ServiceCluster(settings) as cluster:
                print("服务已就绪，浏览器打开：" + cluster.config["api"], flush=True)
                print("使用 " + settings["apiTokenEnv"] + " 环境变量中的访问令牌登录。", flush=True)
                print("此进程持续提供服务；在网页创建任务，在 IDE 点击停止结束。", flush=True)
                while True:
                    stopped = [role for role, process in cluster.processes.items() if process.poll() is not None]
                    if stopped:
                        raise RuntimeError("service stopped unexpectedly: " + ", ".join(stopped))
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()
