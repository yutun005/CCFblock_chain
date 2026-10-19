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
import sysconfig
import tarfile
import time
import urllib.request
import uuid
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


def run(command, cwd=ROOT, **kwargs):
    return subprocess.run([str(x) for x in command], cwd=cwd, check=True, **kwargs)

def pnpm():
    executable = os.environ.get("AGENTFLOW_PNPM") or shutil.which("pnpm")
    if not executable:
        raise RuntimeError("pnpm 11.25.0 is required on PATH (npm install -g pnpm@11.25.0)")
    return executable

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
    run([pnpm(), "install", "--frozen-lockfile"], cwd=ROOT / "apps/web")
    build()


def main():
    parser = argparse.ArgumentParser(description="AgentFlow build, configuration and business services")
    parser.add_argument("command", choices=["setup", "build", "reset-demo", "schema", "configure", "approve", "start", "check", "chain-up", "deploy", "dev", "stop-chain"], nargs="?", default="dev")
    parser.add_argument("--output", type=Path, help="schema export path, relative to the project directory")
    parser.add_argument("--config", type=Path, help="configuration path, resolved against the project directory")
    parser.add_argument("--runtime", type=Path, help="runtime path, resolved against the configuration directory")
    parser.add_argument("--amount", help="exact six-decimal token allowance for approve")
    args = parser.parse_args()
    if args.command in {"chain-up", "deploy", "dev", "stop-chain"}:
        run([PYTHON if PYTHON.is_file() else sys.executable, ROOT / "scripts/local_demo.py", args.command])
        return
    if args.command == "setup":
        setup()
        return
    if Path(sys.executable).resolve() != PYTHON.resolve() and PYTHON.is_file():
        raise SystemExit(subprocess.call([str(PYTHON), str(ROOT / "manage.py"), *sys.argv[1:]], cwd=ROOT))
    if args.command == "build":
        build()
        return
    if args.command == "reset-demo":
        from app import settings_file
        reset_demo(settings_file(args.config, args.runtime)["directory"])
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
