"""Install Solidity source dependencies from checksummed, immutable GitHub archives."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def source_files(content: bytes, source_directory: str) -> dict[str, bytes]:
    selected = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
                raise RuntimeError("Unsafe path in Solidity dependency archive")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise RuntimeError("Symlink in Solidity dependency archive")
            if entry.is_dir() or len(path.parts) < 2:
                continue
            relative = PurePosixPath(*path.parts[1:])
            if relative.parts[0] == source_directory or str(relative).startswith("LICENSE"):
                if str(relative) in selected:
                    raise RuntimeError("Duplicate dependency source path")
                selected[str(relative)] = archive.read(entry)
    if not selected or not any(name.endswith(".sol") for name in selected):
        raise RuntimeError("Solidity sources missing from dependency archive")
    return selected


def install(root: Path = ROOT) -> None:
    lock = json.loads((root / "toolchain-lock.json").read_text(encoding="utf-8"))
    downloads = root / ".cache/dependency-downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    for name, spec in lock["solidity_archives"].items():
        target = root / "contracts/lib" / name
        marker = target / ".agentflow-dependency.json"
        if marker.exists():
            installed = json.loads(marker.read_text(encoding="utf-8"))
            hashes = installed.get("files", {})
            if (installed.get("archive_sha256") == spec["sha256"] and hashes
                    and all((target / p).is_file()
                            and hashlib.sha256((target / p).read_bytes()).hexdigest() == digest
                            for p, digest in hashes.items())):
                print(f"Solidity dependency verified: {name} @ {spec['commit'][:12]}", flush=True)
                continue
        package = downloads / f"{name}-{spec['commit']}.zip"
        if not package.exists() or hashlib.sha256(package.read_bytes()).hexdigest() != spec["sha256"]:
            print(f"Downloading Solidity dependency: {name} @ {spec['commit'][:12]}", flush=True)
            with urllib.request.urlopen(spec["url"], timeout=60) as response:
                content = response.read()
            if hashlib.sha256(content).hexdigest() != spec["sha256"]:
                raise RuntimeError(f"SHA-256 mismatch for {name}; extraction refused")
            package.write_bytes(content)
        content = package.read_bytes()
        if hashlib.sha256(content).hexdigest() != spec["sha256"]:
            raise RuntimeError(f"SHA-256 mismatch for {name}; extraction refused")
        files = source_files(content, "contracts" if name == "openzeppelin-contracts" else "src")
        hashes = {}
        for relative, value in files.items():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists() or destination.read_bytes() != value:
                destination.write_bytes(value)
            hashes[relative] = hashlib.sha256(value).hexdigest()
        marker.write_text(json.dumps({"archive_sha256": spec["sha256"],
                                     "commit": spec["commit"], "files": hashes}, indent=2) + "\n",
                          encoding="utf-8")
        print(f"Solidity dependency installed: {name} ({len(files)} files, license included)", flush=True)


if __name__ == "__main__":
    install()
