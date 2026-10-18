"""Verify clean-checkout dependency restoration and reject unsafe upstream archives."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import zipfile

import pytest

from scripts.contract_dependencies import install, source_files


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
