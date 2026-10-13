"""Record actual runtime versions and hardware without hostname or user identity."""

import ctypes
import json
import os
import platform
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.scripts.common import ROOT, load_deployment, metadata, write_json  # noqa: E402
from scripts.module_c import tool  # noqa: E402


def output(command):
    try:
        return subprocess.check_output(
            command, stderr=subprocess.STDOUT, text=True, encoding="utf-8", timeout=15
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return {"unavailable": type(exc).__name__}


def run():
    ram = None
    if os.name == "nt":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong),
                ("available_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            ram = status.total_physical
    record = {
        **metadata(),
        "os": platform.platform(),
        "architecture": platform.machine(),
        "cpu": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "ram_bytes": ram,
        "python": platform.python_version(),
        "foundry": output([tool("forge"), "--version"]),
        "dependencies": {
            name: version(name) for name in ("web3", "eth-account", "pydantic", "pytest", "ruff")
        },
        "docker": output(["docker", "version", "--format", "{{json .}}"]),
        "besu_image_digest": output(
            ["docker", "image", "inspect", "hyperledger/besu:25.10.0", "--format", "{{json .RepoDigests}}"]
        ),
        "deployment": load_deployment(),
    }
    # Docker's version JSON contains version facts, not desktop user paths or private credentials.
    if isinstance(record["docker"], str):
        record["docker"] = json.loads(record["docker"])
    write_json(ROOT / "experiments/raw/environment.json", record)
    print("Actual environment recorded; no private keys or hostname included")
    return record


if __name__ == "__main__":
    run()
