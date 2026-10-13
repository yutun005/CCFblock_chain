"""Verify every archive checksum and extract safely into an independent reproduction directory."""

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.scripts.common import ROOT  # noqa: E402


def run(archive_path, extract_to=None):
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        manifest = archive.read("submission/SHA256SUMS.txt").decode("utf-8").splitlines()
        covered = {"submission/SHA256SUMS.txt"}
        for entry in manifest:
            checksum, relative = entry.split("  ", 1)
            name = "submission/" + relative
            assert hashlib.sha256(archive.read(name)).hexdigest() == checksum, f"checksum mismatch: {name}"
            covered.add(name)
        assert covered == set(archive.namelist()), "archive contains unmanifested or duplicate files"
        for name in archive.namelist():
            parts = Path(name).parts
            assert ".git" not in parts and ".venv" not in parts and ".tools" not in parts
            assert "local-keys.json" not in parts and not name.endswith(".key")
            assert not name.startswith("submission/src/infra/besu/generated/")
        if extract_to:
            destination = Path(extract_to).resolve()
            if destination.exists() and any(destination.iterdir()):
                raise ValueError("use a new or empty reproduction directory")
            destination.mkdir(parents=True, exist_ok=True)
            for name in archive.namelist():
                target = (destination / name).resolve()
                if not target.is_relative_to(destination):
                    raise ValueError("unsafe archive entry")
            archive.extractall(destination)
    record = {
        "archive_sha256": hashlib.sha256(Path(archive_path).read_bytes()).hexdigest(),
        "files_verified": len(covered),
        "status": "PASS",
        "all_sha256_verified": True,
        "private_runtime_files_excluded": True,
    }
    print(json.dumps(record))
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", nargs="?", default=str(ROOT.parent / "第三模块-C交付包.zip"))
    parser.add_argument("--extract-to")
    args = parser.parse_args()
    run(args.archive, args.extract_to)
