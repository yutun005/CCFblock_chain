"""One local, persistent demonstration: chain -> deployment -> all managed services."""
import argparse
import hashlib
import json
import os
import sys
import time
import signal
import shutil
import uuid
import socket
import webbrowser
import subprocess
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eth_account import Account
import httpx
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError
from services.orchestrator.application import Database, ServiceCluster, artifact, check_services, configure_deployment, local_web3, service_protocol, settings_file
from scripts.module_c import deploy as deploy_chain
from scripts.anvil_state import AnvilManager, AnvilOwnershipError, atomic_json, validate_state, process_identity

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


def running_demo():
    """Reuse only a healthy, authenticated instance of the saved deployment."""
    if not CONFIG.is_file() or not ENV.is_file():
        return None
    settings = settings_file(CONFIG)
    path = settings["directory"] / "deployment.json"
    if not path.is_file():
        return None
    deployment = json.loads(path.read_text(encoding="utf-8"))
    values = json.loads(ENV.read_text(encoding="utf-8"))
    token = values[settings["apiTokenEnv"]]
    api = settings["api"]
    try:
        with httpx.Client(trust_env=False, timeout=3) as client:
            response = client.get(api + "/health")
            response.raise_for_status()
            health = response.json()
            if not isinstance(health, dict) or health.get("ok") is not True or health.get("protocol") != service_protocol(deployment):
                return None
            response = client.get(api + "/api/config", headers={"Authorization": "Bearer " + token})
            response.raise_for_status()
            public = response.json()
            if any(str(public.get(field)) != str(deployment[field]) for field in ("chainId", "escrow", "token", "owner")):
                return None
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    try:
        check_services({**settings, "deployment": deployment, "token": token})
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("本项目已运行，但有服务未就绪；请在原启动窗口停止后重新打开启动脚本。") from exc
    return api


def browser_url(api):
    """A build-specific URL cannot reuse a cached HTML document from an older build."""
    index = ROOT / "apps/web/dist/index.html"
    version = hashlib.sha256(index.read_bytes()).hexdigest()[:16] if index.is_file() else "unbuilt"
    parsed = urlparse(api)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "ui"]
    query.append(("ui", version))
    return urlunparse(parsed._replace(path=parsed.path or "/", query=urlencode(query)))


def show_demo(api, existing=False):
    if existing:
        print("Project is already running; reusing existing services.", flush=True)
    url = browser_url(api)
    print("Project: " + str(ROOT), flush=True)
    print("Open " + url + " ; token is in .cache/local-access.txt", flush=True)
    if os.environ.get("AGENTFLOW_OPEN_BROWSER") == "1":
        try:
            webbrowser.open(url)
        except OSError:
            print("Open the address above in your browser.", flush=True)


def process_arguments():
    """Inspect process arguments without using ports as proof of ownership."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
            "Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%'\" | "
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, check=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        rows = json.loads(result.stdout or "[]")
        shell = ctypes.WinDLL("shell32")
        shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        kernel = ctypes.WinDLL("kernel32")
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        for row in rows if isinstance(rows, list) else [rows]:
            if not row.get("CommandLine"):
                continue
            count = ctypes.c_int()
            argv = shell.CommandLineToArgvW(row["CommandLine"], ctypes.byref(count))
            if argv:
                try:
                    yield int(row["ProcessId"]), [argv[i] for i in range(count.value)]
                finally:
                    kernel.LocalFree(argv)
    else:
        for directory in Path("/proc").glob("[0-9]*"):
            try:
                yield int(directory.name), directory.joinpath("cmdline").read_bytes().decode().strip("\0").split("\0")
            except (OSError, UnicodeError):
                continue


def owns_service(arguments, settings):
    def normalized(value):
        return os.path.normcase(str(Path(value).resolve()))
    try:
        return (normalized(str(ROOT / "app.py")) in {normalized(arg) for arg in arguments[1:2]}
                and normalized(arguments[arguments.index("--config") + 1]) == normalized(str(CONFIG))
                and normalized(arguments[arguments.index("--runtime") + 1]) == normalized(str(settings["directory"]))
                and arguments[arguments.index("--role") + 1] in settings["managedRoles"])
    except (ValueError, IndexError, OSError):
        return False


def stop_services(settings):
    """Stop only this checkout/config/runtime's Python workers, including old Windows wrappers."""
    def still_running(pid, identity):
        if process_identity(pid) != identity:
            return False
        if os.name != "nt":
            try:
                # A terminated child remains in /proc until its parent reaps it.
                return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
            except FileNotFoundError:
                return False
        return True
    owned = [(pid, process_identity(pid)) for pid, args in process_arguments() if owns_service(args, settings)]
    owned = [(pid, identity) for pid, identity in owned if identity is not None]
    for pid, identity in owned:
        if process_identity(pid) != identity:
            continue
        if os.name == "nt":
            subprocess.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 15
    while any(still_running(pid, identity) for pid, identity in owned):
        if time.monotonic() > deadline:
            raise RuntimeError("本项目的旧服务尚未退出，已停止启动新实例；请关闭原启动窗口后重试。")
        time.sleep(.1)
    return len(owned)


def build_updated_web():
    output = ROOT / "apps/web/dist/index.html"
    sources = [ROOT / "apps/web/pnpm-lock.yaml", ROOT / "apps/web/index.html",
               *list((ROOT / "apps/web/src").rglob("*")),
               *list((ROOT / "apps/web").glob("*.json")), *list((ROOT / "apps/web").glob("*.ts"))]
    if output.exists() and all(p.stat().st_mtime <= output.stat().st_mtime for p in sources if p.is_file()):
        return
    from manage import install_web_dependencies, pnpm
    install_web_dependencies()
    print("正在更新页面文件…", flush=True)
    subprocess.run([pnpm(), "--dir", str(ROOT / "apps/web"), "build"], cwd=ROOT, check=True)


def console_action():
    if os.name == "nt" and sys.stdin.isatty():
        import msvcrt
        if msvcrt.kbhit():
            return {"r": "restart", "q": "stop"}.get(msvcrt.getwch().lower())
    return None


def run_demo_once():
    chain_ready = False
    restart = False
    generation = uuid.uuid4().hex
    controller = CACHE / "demo-launcher" / "generation.json"
    try:
        with ExitStack() as services:
            # Hold this lock through service readiness, so simultaneous double-clicks
            # cannot both pass the probe and start competing clusters.
            with chain_manager().locked(CACHE / "demo-launcher", timeout=240):
                # Failures here must leave the currently running services untouched.
                build_updated_web()
                atomic_json(controller, {"generation": generation})
                if CONFIG.is_file():
                    print("正在检查并停止本项目的旧服务…", flush=True)
                    count = stop_services(settings_file(CONFIG))
                    if count:
                        print(f"已停止 {count} 个旧服务进程，准备载入当前代码。", flush=True)
                print("正在载入本地链与已保存的项目…", flush=True)
                rpc = chain_up()
                chain_ready = True
                if not ENV.is_file() or not CONFIG.is_file():
                    deploy()
                load_environment()
                settings = settings_file(CONFIG)
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
                print("正在启动服务市场、验证器和 Agent，请保持此窗口打开…", flush=True)
                cluster = services.enter_context(ServiceCluster(settings_file(CONFIG)))
                with httpx.Client(trust_env=False, timeout=15) as client:
                    response = client.get(cluster.config["api"] + "/api/agents",
                                          headers={"Authorization": "Bearer " + cluster.config["token"]})
                    response.raise_for_status()
                print("服务已就绪，正在打开页面。", flush=True)
            show_demo(cluster.config["api"])
            print("此窗口负责运行项目。按 R 重启；按 Q 或 Ctrl+C 停止。", flush=True)
            while True:
                command = console_action()
                if command in {"restart", "stop"}:
                    restart = command == "restart"
                    print("正在重启…" if restart else "正在停止并保存状态…", flush=True)
                    break
                stopped = [role for role, process in cluster.processes.items() if process.poll() is not None]
                if stopped:
                    raise RuntimeError("服务已退出：" + ", ".join(stopped) + "。请重新打开启动脚本。")
                time.sleep(.5)
    except KeyboardInterrupt:
        print("\n正在停止并保存状态…", flush=True)
    finally:
        if chain_ready:
            with chain_manager().locked(CACHE / "demo-launcher", timeout=240):
                current = json.loads(controller.read_text(encoding="utf-8"))
                if current["generation"] == generation:
                    if CONFIG.is_file():
                        stop_services(settings_file(CONFIG))
                    manager = chain_manager()
                    manager.checkpoint()
                    if not restart:
                        manager.stop()
                    print("服务已停止，项目记录和链状态已保留。", flush=True)
                else:
                    restart = False
                    print("项目已由另一个启动窗口接管，本窗口已结束。", flush=True)
    return restart


def run_demo():
    while run_demo_once():
        pass


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
    run_demo()


if __name__=="__main__":
    main()
