"""One local, persistent demonstration: chain -> deployment -> all managed services."""
import argparse
import json
import os
import sys
import time
import signal
import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eth_account import Account
from web3 import Web3
from app import Chain, ServiceCluster, configure_deployment, local_web3, settings_file
from scripts.module_c import deploy as deploy_chain, start_anvil

CACHE = ROOT / ".cache"
ENV = CACHE / "local-environment.json"
CONFIG = CACHE / "local-project.json"


def chain_up():
    chain = local_web3("http://127.0.0.1:8545")
    if chain.is_connected():
        if chain.eth.chain_id != 31337:
            raise RuntimeError("port 8545 belongs to another chain; refusing to alter it")
    else:
        start_anvil(8545)


def load_environment():
    if not ENV.is_file():
        raise RuntimeError("run python manage.py deploy first")
    os.environ.update(json.loads(ENV.read_text(encoding="utf-8")))


def deploy():
    chain_up()
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
    deploy_chain("http://127.0.0.1:8545", 31337)
    addresses = json.loads((ROOT / "deployments/local-addresses.json").read_text(encoding="utf-8"))
    keys = json.loads((ROOT / "deployments/local-keys.json").read_text(encoding="utf-8"))
    config = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))
    runtime = config["runtime"]
    runtime.update(directory=str(CACHE / "runtime"), inputDirectory=str(ROOT / "data"),
                   scenarioInputDirectories={"travel_planning":str(ROOT / "data/travel")})
    values = {runtime["chain"]["rpcEnv"]:"http://127.0.0.1:8545",
              runtime["chain"]["chainIdEnv"]:"31337",
              runtime["chain"]["tokenEnv"]:addresses["token_address"],
              runtime["chain"]["escrowEnv"]:addresses["escrow_address"],
              runtime["apiTokenEnv"]:os.urandom(32).hex()}
    for name, field in (("owner","owner"),("coordinator","coordinator"),("validator","verifier")):
        values[runtime["signers"][name]] = keys[field]
    w3 = local_web3("http://127.0.0.1:8545")
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
    chain_up()
    if not ENV.is_file() or not CONFIG.is_file():
        deploy()
    load_environment()
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
