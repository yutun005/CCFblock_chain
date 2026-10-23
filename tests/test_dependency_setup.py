"""Verify clean-checkout dependency restoration and reject unsafe upstream archives."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from unittest.mock import patch
import zipfile

import pytest

from scripts.contract_dependencies import install, source_files
import manage


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        for name, value in entries.items():
            package.writestr(name, value)
    return output.getvalue()


def test_fresh_dependency_restoration_is_offline_repeatable():
    content = archive({"repo/contracts/A.sol": "contract A {}", "repo/LICENSE": "MIT"})
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        spec = {"commit": "abc", "sha256": hashlib.sha256(content).hexdigest(), "url": "https://example.test/archive"}
        (root / "toolchain-lock.json").write_text(json.dumps({"solidity_archives": {"openzeppelin-contracts": spec}}))
        with patch("scripts.contract_dependencies.urllib.request.urlopen", return_value=io.BytesIO(content)) as request:
            install(root)
            assert request.call_count == 1
        destination = root / "contracts/lib/openzeppelin-contracts/contracts/A.sol"
        assert destination.read_text() == "contract A {}"
        assert (destination.parent.parent / "LICENSE").read_text() == "MIT"
        with patch("scripts.contract_dependencies.urllib.request.urlopen", side_effect=AssertionError("network")):
            install(root)
            destination.write_text("tampered")
            install(root)
        assert destination.read_text() == "contract A {}"


def test_dependency_archive_rejects_path_traversal():
    with pytest.raises(RuntimeError, match="Unsafe path"):
        source_files(archive({"repo/contracts/../../outside.sol": "bad"}), "contracts")


def test_dependency_download_rejects_wrong_checksum():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        spec = {"commit": "abc", "sha256": "0" * 64, "url": "https://example.test/archive"}
        (root / "toolchain-lock.json").write_text(json.dumps({"solidity_archives": {"openzeppelin-contracts": spec}}))
        with patch("scripts.contract_dependencies.urllib.request.urlopen", return_value=io.BytesIO(b"bad")):
            with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
                install(root)
        assert not (root / "contracts/lib/openzeppelin-contracts").exists()


def test_web_tools_bootstrap_without_editor_path_and_work_offline_afterward(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, "CACHE", tmp_path / "project with spaces/.cache")
    monkeypatch.delenv("AGENTFLOW_PNPM", raising=False)
    monkeypatch.setenv("PATH", "")
    node_name = f"node-v{manage.NODE_VERSION}-{'win-x64/node.exe' if os.name == 'nt' else 'linux-x64/bin/node'}"
    if os.name == "nt":
        node_data = archive({node_name: "node binary"})
    else:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as package:
            member = tarfile.TarInfo(node_name)
            member.size = 4
            package.addfile(member, io.BytesIO(b"node"))
        node_data = output.getvalue()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as package:
        for name in ("package/bin/pnpm.mjs", "package/dist/pnpm.cjs"):
            member = tarfile.TarInfo(name)
            member.size = 5
            package.addfile(member, io.BytesIO(b"pnpm;"))
    pnpm_data = output.getvalue()
    suffix = "win-x64.zip" if os.name == "nt" else "linux-x64.tar.gz"
    monkeypatch.setitem(manage.NODE_ARCHIVE_SHA256, suffix, hashlib.sha256(node_data).hexdigest())
    monkeypatch.setattr(manage, "PNPM_ARCHIVE_SHA512", manage.base64.b64encode(hashlib.sha512(pnpm_data).digest()).decode())
    with patch.object(manage.urllib.request, "urlopen", side_effect=[io.BytesIO(node_data), io.BytesIO(pnpm_data)]) as download:
        wrapper = Path(manage.pnpm())
        assert download.call_count == 2
    assert wrapper.is_file()
    assert str(tmp_path) not in wrapper.read_text()  # Relocatable launcher.
    assert (wrapper.parent / "pnpm/dist/pnpm.cjs").is_file()
    with patch.object(manage.urllib.request, "urlopen", side_effect=AssertionError("offline")):
        monkeypatch.setenv("PATH", "")
        assert manage.pnpm() == str(wrapper)
        assert str(wrapper.parent / "node") in os.environ["PATH"]


def test_web_tool_download_rejects_incomplete_or_modified_archive():
    with patch.object(manage.urllib.request, "urlopen", return_value=io.BytesIO(b"truncated")):
        with pytest.raises(RuntimeError, match="校验失败"):
            manage.verified_web_download("https://example.test/node.zip", "sha256", bytes(32))


@pytest.mark.skipif(os.name == "nt", reason="executes the POSIX launcher")
def test_portable_pnpm_executes_with_spaces_and_without_system_path(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, "CACHE", tmp_path / "project with spaces/.cache")
    monkeypatch.delenv("AGENTFLOW_PNPM", raising=False)
    monkeypatch.setenv("PATH", "")
    directory = manage.CACHE / "tools/web"
    node = directory / "node/node"
    node.parent.mkdir(parents=True)
    node.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    node.chmod(0o755)
    entry = directory / "pnpm/bin/pnpm.mjs"
    entry.parent.mkdir(parents=True)
    entry.write_text("// test entry")
    with patch.object(manage.urllib.request, "urlopen", side_effect=AssertionError("offline")):
        wrapper = manage.pnpm()
        result = subprocess.run([wrapper, "--dir", "path with spaces", "build"],
                                cwd=tmp_path, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == [str(entry), "--dir", "path with spaces", "build"]
    assert entry.is_file()
