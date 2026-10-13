"""Package source, report material and real evidence; exclude every generated secret and runtime."""

import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.scripts.common import ROOT  # noqa: E402

ROOT_FILES = {
    "README.md",
    "Makefile",
    "requirements.txt",
    "requirements.lock",
    "pyproject.toml",
    "toolchain-lock.json",
    ".env.example",
    ".gitignore",
    "LICENSE",
}
DIRECTORIES = {
    "contracts",
    "services",
    "packages",
    "infra",
    "scripts",
    "tests",
    "experiments",
    "docs",
    ".github",
}


def included(path):
    parts = path.relative_to(ROOT).parts
    if path.relative_to(ROOT).as_posix() == "docs/开发进度.md":
        return False  # local handoff notes contain personal paths and are not submission material
    if parts[0] not in DIRECTORIES and str(path.relative_to(ROOT)) not in ROOT_FILES:
        return False
    if any(part in {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"} for part in parts):
        return False
    if parts[:2] in (("contracts", "out"), ("contracts", "cache"), ("contracts", "broadcast")):
        return False
    if parts[:3] == ("infra", "besu", "generated"):
        return False
    return path.suffix not in {".pyc", ".sqlite3", ".key"}


def run():
    files = [p for p in ROOT.rglob("*") if p.is_file() and included(p)]
    # Do not enumerate runtime descendants in archive content; verify no local private key leaks.
    key_file = ROOT / "deployments/local-keys.json"
    forbidden = []
    if key_file.exists():
        forbidden = [
            v.removeprefix("0x").encode() for v in json.loads(key_file.read_text(encoding="utf-8")).values()
        ]
    generated = ROOT / "infra/besu/generated"
    forbidden += [p.read_text(encoding="ascii").strip().encode() for p in generated.glob("*.key")]
    manifest = []
    redactions = []
    destination = ROOT.parent / "第三模块-C交付包.zip"
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(files):
            content = path.read_bytes()
            if any(secret in content for secret in forbidden):
                raise RuntimeError(f"refusing package: private key appeared in {path.relative_to(ROOT)}")
            relative = path.relative_to(ROOT).as_posix()
            if path.name.endswith(".stderr.txt"):
                # Preserve the actual test output while removing desktop user paths from warnings.
                profile = os.environ.get("USERPROFILE", str(Path.home()))
                original_hash = hashlib.sha256(content).hexdigest()
                text = content.decode("utf-8")
                for spelling in (profile, profile.replace("\\", "\\\\"), profile.replace("\\", "/")):
                    text = text.replace(spelling, "<USERPROFILE>")
                content = text.encode("utf-8")
                if hashlib.sha256(content).hexdigest() != original_hash:
                    redactions.append(
                        {
                            "path": relative,
                            "original_sha256": original_hash,
                            "reason": "local user path in compiler/test warnings",
                        }
                    )
            target = "submission/src/" + relative
            archive.writestr(target, content)
            manifest.append(hashlib.sha256(content).hexdigest() + "  " + target.removeprefix("submission/"))
            if relative.startswith("experiments/"):
                duplicate = "submission/" + relative
            elif relative in ("docs/设计报告.md", "docs/测试报告.md"):
                duplicate = "submission/reports/" + path.name
            elif relative.startswith("docs/") or relative == "README.md":
                duplicate = "submission/" + relative
            else:
                duplicate = None
            if duplicate:
                archive.writestr(duplicate, content)
                manifest.append(
                    hashlib.sha256(content).hexdigest() + "  " + duplicate.removeprefix("submission/")
                )
        notes = json.dumps(
            {
                "scope": "Module C only; integrate A/B/D before full competition submission",
                "excluded": [
                    "private keys",
                    "node databases",
                    "tool binaries",
                    "virtualenv",
                    "Git metadata",
                    "local progress notes",
                ],
                "redactions": redactions,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        archive.writestr("submission/PACKAGE-NOTES.json", notes)
        manifest.append(hashlib.sha256(notes).hexdigest() + "  PACKAGE-NOTES.json")
        archive.writestr("submission/SHA256SUMS.txt", "\n".join(manifest) + "\n")
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert not any("local-keys" in n or "/.git/" in n or n.endswith(".key") for n in names)
        for entry in manifest:
            checksum, relative = entry.split("  ", 1)
            assert hashlib.sha256(archive.read("submission/" + relative)).hexdigest() == checksum
    (ROOT / "SHA256SUMS.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    print(f"Created {destination}: {len(files)} source/evidence files, {destination.stat().st_size} bytes")
    return destination


if __name__ == "__main__":
    run()
