"""One local, persistent demonstration: chain -> deployment -> all managed services."""
import argparse
import json
import os
import sys
import time
import signal
import shutil
import uuid
import socket
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eth_account import Account
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError
from services.orchestrator.application import Database, ServiceCluster, artifact, configure_deployment, local_web3, settings_file
from scripts.module_c import deploy as deploy_chain
from scripts.anvil_state import AnvilManager, AnvilOwnershipError, atomic_json, validate_state

CACHE = ROOT / ".cache"
ENV = CACHE / "local-environment.json"
CONFIG = CACHE / "local-project.json"
CHAIN_PORT = CACHE / "local-chain-port.json"


def chain_manager():
    CACHE.mkdir(exist_ok=True)
    legacy = ROOT / "deployments/local-anvil-process.json"
    record = json.loads(legacy.read_text(encoding="utf-8")) if legacy.exists() else None
    saved = json.loads(CHAIN_PORT.read_text(encoding="utf-8")) if CHAIN_PORT.exists() else record
    port = int(saved["port"]) if saved else 8545
    if not saved and not (CACHE / "anvil").exists():
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
    directory = ROOT / saved["stateDirectory"] if saved and saved.get("stateDirectory") else None
    manager = AnvilManager(ROOT, port=port, directory=directory)
    if record and int(record["port"]) == port and not manager.record.exists():
        manager.record = legacy
    old_state = CACHE / "local-chain-state.json"
    if old_state.exists() and not any(p.exists() for p in (manager.state, manager.previous, manager.autosave)):
        atomic_json(manager.state, validate_state(json.loads(old_state.read_text(encoding="utf-8"))))
    return manager


def chain_up():
    manager = chain_manager()
    # Serialize endpoint selection as well as startup; a second launcher must reuse
    # the first launcher's restored chain instead of forking the same checkpoint.
    with manager.locked(CACHE / "anvil-launcher"):
        manager = chain_manager()
        for attempt in range(3):
            try:
                manager.ensure_running()
                break
            except AnvilOwnershipError:
                if attempt == 2:
                    raise
                if manager._load_path() is None and (ENV.exists() or CONFIG.exists()):
                    raise RuntimeError("Local chain port is occupied and no saved checkpoint exists; existing project data has been preserved. Restore the project's chain checkpoint before restarting.")
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                print(f"Port {manager.port} belongs to another service; restoring this project's chain on {port}.", flush=True)
                manager = AnvilManager(ROOT, port=port, directory=manager.directory,
                                       executable=manager.executable, interval=manager.interval)
        atomic_json(CHAIN_PORT, {"port": manager.port,
                    "stateDirectory": manager.directory.relative_to(ROOT).as_posix()})
        return manager.w3.provider.endpoint_uri


def validate_saved_deployment(settings, rpc=None):
    path = settings["directory"] / "deployment.json"
    if not path.exists():
        raise RuntimeError("Local deployment record is missing. Run python manage.py deploy; old application files are preserved.")
    deployment = json.loads(path.read_text(encoding="utf-8"))
    w3 = local_web3(rpc or deployment["rpc"])
    if w3.eth.chain_id != deployment["chainId"]:
        raise RuntimeError("Saved deployment belongs to a different chain; use the correct runtime configuration.")
    mismatches = [name for name in ("token", "escrow")
                  if Web3.keccak(w3.eth.get_code(deployment[name])).to_0x_hex() != deployment[name + "CodeHash"]]
    if mismatches:
        raise RuntimeError(
            "Saved contracts are missing or changed on the current chain. "
            "Old application data and keys have NOT been overwritten. "
            "If the earlier memory-only Anvil was lost before persistence was enabled, its state cannot be reconstructed. "
            "Restore an existing .cache/anvil checkpoint, or run python manage.py deploy to create a new backed-up demo. "
            "New persistent Anvil runs save and restore their chain automatically.")
    if rpc and rpc != deployment["rpc"]:
        deployment["rpc"] = rpc
        atomic_json(path, deployment)


def load_environment():
    if not ENV.is_file():
        raise RuntimeError("run python manage.py deploy first")
    os.environ.update(json.loads(ENV.read_text(encoding="utf-8")))


def available_service_ports():
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    runtime = document["runtime"]
    saved_jobs = Database(Path(runtime["directory"]) / "platform.sqlite").values("jobs")
    history = json.dumps(saved_jobs)
    moved = {}
    reservations = []
    try:
        for record, field in [(runtime, key) for key in ("api", "market", "validator")] + [(agent, "endpoint") for agent in runtime["agents"]]:
            parsed = urlparse(record[field])
            probe = socket.socket()
            reservations.append(probe)
            try:
                probe.bind(("127.0.0.1", parsed.port))
            except OSError as exc:
                # Existing signed assignments retain their endpoints. Newly added
                # merchants with no saved job references may choose a free port.
                used_by_jobs = saved_jobs and (record is runtime or
                    json.dumps(record["id"]) in history or json.dumps(record[field]) in history)
                if used_by_jobs:
                    raise RuntimeError(f"Saved service port {parsed.port} is unavailable; stop the other application and retry. Existing project history has been preserved.") from exc
                probe.bind(("127.0.0.1", 0))
                old = record[field]
                record[field] = f"http://127.0.0.1:{probe.getsockname()[1]}"
                moved[old] = record[field]
        if "allowedAgentOrigins" in runtime:
            runtime["allowedAgentOrigins"] = [moved.get(url, url) for url in runtime["allowedAgentOrigins"]]
        atomic_json(CONFIG, document)
    finally:
        for probe in reservations:
            probe.close()


def sync_demo_merchants():
    """Add bundled competitors to an existing local demo without replacing its chain or history."""
    current = json.loads(CONFIG.read_text(encoding="utf-8"))
    desired = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))["runtime"]["agents"]
    runtime = current["runtime"]
    existing = {agent["id"]: agent for agent in runtime["agents"]}
    missing = [agent for agent in desired if agent["id"] not in existing]
    if not missing:
        return
    jobs = Database(Path(runtime["directory"]) / "platform.sqlite").values("jobs")
    if any(job["status"] in {"FUNDING", "ACTIVE", "PREPARING", "INTERRUPTED"} for job in jobs):
        raise RuntimeError("请先完成或恢复已有任务，再重启以添加服务市场商家；旧任务和链状态已保留。")
    values = json.loads(ENV.read_text(encoding="utf-8"))
    rpc = values[runtime["chain"]["rpcEnv"]]
    w3 = local_web3(rpc)
    for service in missing:
        account = Account.create()
        values[service["keyEnv"]] = Web3.to_hex(account.key)
        w3.provider.make_request("anvil_setBalance", [account.address, hex(100 * 10**18)])
        runtime["agents"].append(service)
        if "managedRoles" in runtime:
            runtime["managedRoles"].append(service["id"])
        if "allowedAgentOrigins" in runtime:
            runtime["allowedAgentOrigins"].append(service["endpoint"])
    ENV.write_text(json.dumps(values, indent=2), encoding="utf-8")
    CONFIG.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    load_environment()
    print(f"Added {len(missing)} bundled demo merchants; existing deployment and projects preserved.", flush=True)


def deploy():
    rpc = chain_up()
    runtime_root = (CACHE / "runtime").resolve()
    if (runtime_root / "deployment.json").exists():
        backup = (CACHE / "demo-backups" / ("deployment-" + uuid.uuid4().hex)).resolve()
        if runtime_root.parent != CACHE.resolve() or not backup.is_relative_to(CACHE.resolve()):
            raise RuntimeError("local runtime backup escaped the project cache")
        backup.parent.mkdir(parents=True,exist_ok=True)
        shutil.move(str(runtime_root),str(backup))
        for previous in (ENV, CONFIG, CACHE / "local-access.txt"):
            if previous.exists():
                shutil.copy2(previous,backup / previous.name)
    deploy_chain(rpc, 31337)
    addresses = json.loads((ROOT / "deployments/local-addresses.json").read_text(encoding="utf-8"))
    keys = json.loads((ROOT / "deployments/local-keys.json").read_text(encoding="utf-8"))
    config = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))
    runtime = config["runtime"]
    runtime.update(directory=str(CACHE / "runtime"), inputDirectory=str(ROOT / "data"),
                   scenarioInputDirectories={"travel_planning":str(ROOT / "data/travel")})
    values = {runtime["chain"]["rpcEnv"]:rpc,
              runtime["chain"]["chainIdEnv"]:"31337",
              runtime["chain"]["tokenEnv"]:addresses["token_address"],
              runtime["chain"]["escrowEnv"]:addresses["escrow_address"],
              runtime["apiTokenEnv"]:os.urandom(32).hex()}
    for name, field in (("owner","owner"),("coordinator","coordinator"),("validator","verifier")):
        values[runtime["signers"][name]] = keys[field]
    w3 = local_web3(rpc)
    for service in runtime["agents"]:
        account = Account.create()
        values[service["keyEnv"]] = Web3.to_hex(account.key)
        w3.provider.make_request("anvil_setBalance", [account.address, hex(100 * 10**18)])
    CACHE.mkdir(exist_ok=True)
    ENV.write_text(json.dumps(values,indent=2),encoding="utf-8")
    CONFIG.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding="utf-8")
    (CACHE / "local-access.txt").write_text(values[runtime["apiTokenEnv"]]+"\n",encoding="utf-8")
    load_environment()
    configure_deployment(settings_file(CONFIG))
    chain_manager().checkpoint()
    print("Local contracts deployed. Local access token: .cache/local-access.txt",flush=True)


def stop_chain():
    chain_manager().stop()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["chain-up","deploy","dev","stop-chain","save-chain"])
    args=parser.parse_args()
    if args.command=="save-chain":
        chain_manager().checkpoint();return
    if args.command=="stop-chain":
        stop_chain();return
    if args.command=="chain-up":
        chain_up(); return
    if args.command=="deploy":
        deploy(); return
    rpc = chain_up()
    if not ENV.is_file() or not CONFIG.is_file():
        deploy()
    load_environment()
    settings=settings_file(CONFIG)
    validate_saved_deployment(settings, rpc=rpc)
    env = json.loads(ENV.read_text(encoding="utf-8"))
    env[settings["chain"]["rpcEnv"]] = rpc
    atomic_json(ENV, env)
    load_environment()
    try:
        contract = local_web3(rpc).eth.contract(address=env[settings["chain"]["escrowEnv"]], abi=artifact("TaskEscrow")["abi"])
        contract.functions.reviewMasks(bytes(32)).call()
    except (ValueError, BadFunctionCallOutput, ContractLogicError) as exc:
        raise RuntimeError("Existing contracts do not support reviewed tasks. Run python manage.py deploy to create a new backed-up demo; existing data has been preserved.") from exc
    sync_demo_merchants()
    available_service_ports()
    settings=settings_file(CONFIG)
    try:
        with ServiceCluster(settings) as cluster:
            print("Open "+cluster.config["api"]+" ; token is in .cache/local-access.txt",flush=True)
            print("Ctrl+C stops services. Stop the isolated chain with python scripts/local_demo.py stop-chain.",flush=True)
            while True:
                if any(p.poll() is not None for p in cluster.processes.values()):
                    raise RuntimeError("a managed service exited; see .cache/runtime/logs")
                time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        chain_manager().checkpoint()


if __name__=="__main__":
    main()
