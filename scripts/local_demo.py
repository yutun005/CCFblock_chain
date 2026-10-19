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
from scripts.module_c import deploy as deploy_chain, start_anvil

CACHE = ROOT / ".cache"
ENV = CACHE / "local-environment.json"
CONFIG = CACHE / "local-project.json"
CHAIN_PORT = CACHE / "local-chain-port.json"


def chain_up():
    CACHE.mkdir(exist_ok=True)
    saved = json.loads(CHAIN_PORT.read_text()) if CHAIN_PORT.exists() else None
    port = saved["port"] if saved else 8545
    chain = local_web3(f"http://127.0.0.1:{port}")
    if chain.is_connected():
        if chain.eth.chain_id != 31337:
            raise RuntimeError("configured port belongs to another chain; refusing to alter it")
    else:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
        start_anvil(port, CACHE / "local-chain-state.json")
    CHAIN_PORT.write_text(json.dumps({"port": port}))
    return f"http://127.0.0.1:{port}"


def load_environment():
    if not ENV.is_file():
        raise RuntimeError("run python manage.py deploy first")
    os.environ.update(json.loads(ENV.read_text(encoding="utf-8")))


def available_service_ports():
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    runtime = document["runtime"]
    busy_jobs = Database(Path(runtime["directory"]) / "platform.sqlite").values("jobs")
    reservations = []
    try:
        for record, field in [(runtime, key) for key in ("api", "market", "validator")] + [(agent, "endpoint") for agent in runtime["agents"]]:
            parsed = urlparse(record[field])
            probe = socket.socket()
            reservations.append(probe)
            try:
                probe.bind(("127.0.0.1", parsed.port))
            except OSError as exc:
                if busy_jobs:
                    raise RuntimeError(f"Saved service port {parsed.port} is unavailable; stop the other application and retry. Existing project history has been preserved.") from exc
                probe.bind(("127.0.0.1", 0))
                record[field] = f"http://127.0.0.1:{probe.getsockname()[1]}"
        CONFIG.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        for probe in reservations:
            probe.close()


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
    print("Local contracts deployed. Local access token: .cache/local-access.txt",flush=True)


def stop_chain():
    record=ROOT/"deployments/local-anvil-process.json"
    if not record.exists():
        print("No chain owned by this checkout was recorded");return
    pid=int(json.loads(record.read_text(encoding="utf-8"))["pid"])
    expected=(CACHE/"tools/foundry"/("anvil.exe" if os.name=="nt" else "anvil")).resolve()
    if os.name=="nt":
        import subprocess
        process=subprocess.run(["powershell","-NoProfile","-Command",
                                f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).Path"],
                               capture_output=True,text=True,check=True)
        path=process.stdout.strip()
        if not path:
            print("Recorded chain has already stopped");return
        actual=Path(path).resolve()
    else:
        executable=Path(f"/proc/{pid}/exe")
        if not executable.exists():
            print("Recorded chain has already stopped");return
        actual=executable.resolve()
    if actual!=expected:
        raise RuntimeError("recorded PID is not this checkout's Anvil; refusing to stop it")
    os.kill(pid,signal.SIGTERM)
    print("Project Anvil stopped; evidence and keys preserved")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["chain-up","deploy","dev","stop-chain"])
    args=parser.parse_args()
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
    env = json.loads(ENV.read_text(encoding="utf-8"))
    env[settings["chain"]["rpcEnv"]] = rpc
    ENV.write_text(json.dumps(env, indent=2), encoding="utf-8")
    load_environment()
    deployed = local_web3(rpc).eth.get_code(env[settings["chain"]["escrowEnv"]])
    # Immutable addresses alter the bytecode. Presence of the new ABI method is checked by a read call.
    try:
        contract = local_web3(rpc).eth.contract(address=env[settings["chain"]["escrowEnv"]], abi=artifact("TaskEscrow")["abi"])
        if not deployed:
            raise ValueError("missing deployed code")
        contract.functions.reviewMasks(bytes(32)).call()
    except (ValueError, BadFunctionCallOutput, ContractLogicError):
        print("Local contract upgrade: preserving the old runtime under .cache/demo-backups and deploying the review-capable contract.", flush=True)
        deploy()
        settings=settings_file(CONFIG)
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


if __name__=="__main__":
    main()
