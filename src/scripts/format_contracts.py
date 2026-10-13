"""Format our Solidity through stdin; avoids Foundry 1.5 Windows path parsing issues."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.module_c import ROOT, tool  # noqa: E402


def run():
    for directory in ("src", "test", "script"):
        for path in (ROOT / "contracts" / directory).rglob("*.sol"):
            result = subprocess.run(
                [tool("forge"), "fmt", "-", "--raw"],
                input=path.read_text(encoding="utf-8"),
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=ROOT / "contracts",
                check=True,
            )
            path.write_text(result.stdout.rstrip() + "\n", encoding="utf-8")
    print("Module C Solidity sources formatted")


if __name__ == "__main__":
    run()
