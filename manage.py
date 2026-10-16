"""Portable build and service entry point for AgentFlow Pay."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
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
FOUNDRY_VERSION = "1.8.5"
WINDOWS_ARCHIVE_SHA256 = "21f86b563d87404a68481f513bf3e419780ee536307b89c658215ae6f8f5aa45"
LINUX_ARCHIVE_SHA256 = "6c66ffcc55fa4249197721baa3098bc208014ea1d8aa04b2ed50ac6bccffb226"


def run(command, cwd=ROOT, **kwargs):
    return subprocess.run([str(x) for x in command], cwd=cwd, check=True, **kwargs)

def pnpm():
    executable = os.environ.get("AGENTFLOW_PNPM") or shutil.which("pnpm")
    if not executable:
        raise RuntimeError("pnpm 11.25.0 is required on PATH (npm install -g pnpm@11.25.0)")
    return executable

def foundry(name):
    local = TOOLS / (name + (".exe" if os.name == "nt" else ""))
    executable = str(local) if local.exists() else shutil.which(name)
    if not executable:
        raise RuntimeError("Foundry missing. Run setup or install Foundry v1.8.5.")
    return executable

def install_foundry():
    forge_available = (TOOLS / ("forge.exe" if os.name == "nt" else "forge")).exists() or shutil.which("forge")
    anvil_available = (TOOLS / ("anvil.exe" if os.name == "nt" else "anvil")).exists() or shutil.which("anvil")
    if forge_available and anvil_available:
        version = run([foundry("forge"), "--version"], capture_output=True, text=True).stdout
        if "Version: 1.8.5" not in version:
            raise RuntimeError("Expected Foundry 1.8.5, found: " + version.splitlines()[0])
        return
    if platform.system() not in {"Windows", "Linux"} or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Install Foundry v1.8.5 for this platform, then rerun setup; see README.md")
    TOOLS.mkdir(parents=True, exist_ok=True)
    asset = "win32_amd64.zip" if os.name == "nt" else "linux_amd64.tar.gz"
    checksum = WINDOWS_ARCHIVE_SHA256 if os.name == "nt" else LINUX_ARCHIVE_SHA256
    archive = TOOLS.parent / ("foundry_v1.8.5_" + asset)
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
        url = "https://github.com/foundry-rs/foundry/releases/download/v1.8.5/" + archive.name
        print("Downloading pinned Foundry v1.8.5 archive", flush=True)
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
    config = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))
    for name, spec in config["dependencies"].items():
        folder = "openzeppelin-contracts" if name == "openzeppelin" else name
        target = CACHE / "contracts/lib" / folder
        stamp = target / ".agentflow-pin"
        if stamp.exists() and stamp.read_text() == spec["commit"]:
            continue
        url = f"https://codeload.github.com/{spec['repository']}/zip/{spec['commit']}"
        archive = CACHE / (folder + ".zip")
        with urllib.request.urlopen(url, timeout=60) as response:
            archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != spec["sha256"]:
            raise RuntimeError("Third-party archive digest mismatch: " + name)
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                relative = Path(*Path(item.filename).parts[1:])
                output = (target / relative).resolve()
                if not output.is_relative_to(target.resolve()):
                    raise RuntimeError("Unsafe archive path")
                if item.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(package.read(item))
        stamp.write_text(spec["commit"])
        archive.unlink()


def prepare_generated():
    config = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))
    web, contracts = CACHE / "web", CACHE / "contracts"
    for directory in (web / "src", contracts / "src"):
        directory.mkdir(parents=True, exist_ok=True)
    for name, content in config["webConfig"].items():
        (web / name).write_text(content, encoding="utf-8")
    (web / "src/vite-env.d.ts").write_text('/// <reference types="vite/client" />\n', encoding="utf-8")
    (web / "package.json").write_text(json.dumps(config["webPackage"], indent=2), encoding="utf-8")
    shutil.copyfile(ROOT / "pnpm-lock.yaml", web / "pnpm-lock.yaml")
    source = (ROOT / "web.tsx").read_text(encoding="utf-8")
    code, styles = source.split("/* @agentflow-styles\n", 1)
    (web / "src/main.tsx").write_text(code, encoding="utf-8")
    (web / "src/style.css").write_text(styles.split("\n@end-agentflow-styles */", 1)[0], encoding="utf-8")
    (contracts / "foundry.toml").write_text(config["foundryConfig"], encoding="utf-8")
    shutil.copyfile(ROOT / "TaskEscrow.sol", contracts / "src/TaskEscrow.sol")
    shutil.copyfile(ROOT / "MockUSDC.sol", contracts / "src/MockUSDC.sol")
    test_target = contracts / "test/TaskEscrowDTest.t.sol"
    test_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "tests/contracts/TaskEscrowDTest.t.sol", test_target)


def test_contracts():
    prepare_generated()
    result_path = ROOT / "experiments/raw/forge/forge-results.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([foundry("forge"), "test", "--json"], cwd=CACHE / "contracts",
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    result_path.write_text(result.stdout, encoding="utf-8")
    result_path.with_suffix(".stderr.txt").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("Foundry tests failed; inspect " + str(result_path))
    print("Foundry contract tests passed; raw output saved to " + str(result_path))


def build():
    prepare_generated()
    run([foundry("forge"), "build", "--skip", "test", "--skip", "script"], cwd=CACHE / "contracts")
    run([pnpm(), "run", "build"], cwd=CACHE / "web")


def setup():
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use Python 3.12 for the pinned runtime dependencies")
    if not PYTHON.exists():
        venv.EnvBuilder(with_pip=True).create(CACHE / "venv")
    run([PYTHON, "-m", "pip", "install", "-r", ROOT / "requirements.lock"])
    install_foundry()
    install_contract_libraries()
    prepare_generated()
    run([pnpm(), "install", "--frozen-lockfile"], cwd=CACHE / "web")
    build()


def main():
    parser = argparse.ArgumentParser(description="AgentFlow build, configuration and business services")
    parser.add_argument("command", choices=["setup", "build", "test-contract", "schema", "configure", "approve", "start"], nargs="?", default="start")
    parser.add_argument("--output", type=Path, help="schema export path, relative to the project directory")
    parser.add_argument("--config", type=Path, help="configuration path, resolved against the project directory")
    parser.add_argument("--runtime", type=Path, help="runtime path, resolved against the configuration directory")
    parser.add_argument("--amount", help="exact six-decimal token allowance for approve")
    args = parser.parse_args()
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
    if args.command == "configure":
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
