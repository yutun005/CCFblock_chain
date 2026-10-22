"""Exercise disclosure prevention, especially forced staging and stale indexed content."""
import json
from pathlib import Path
import subprocess

import pytest

from scripts.check_public_source import check, inspect, local_secrets, private_path


@pytest.mark.parametrize('name', ['planner.local.json', 'nested/.env.production', '.cache/local-access.txt',
                                 'deployments/local-keys.json', 'wallet.pem', 'platform.sqlite3',
                                 'infra/besu/data/state.json'])
def test_private_files_blocked_even_without_key_contents(name):
    assert private_path(name)


@pytest.mark.parametrize('name', ['planner.example.json', '.env.example', 'services/orchestrator/planning.py',
                                 'contracts/abi/MockUSDC.json'])
def test_shareable_sources_allowed(name):
    assert not private_path(name)


def test_recognizes_credentials_without_echoing_them():
    fake = b'sk-' + b'abcd1234' * 5
    assert 'provider-api-key' in inspect('notes.txt', fake, set())
    assert 'local-credential' in inspect('notes.txt', b'prefix:' + b'fictional-access-value', {b'fictional-access-value'})
    assert inspect('config.json', b'{"apiKey":""}', set()) == []
    assert inspect('result.json', b'{"token_address":"0x' + b'ab' * 20 + b'"}', set()) == []


def test_loads_private_values_but_not_chain_addresses(tmp_path, monkeypatch):
    monkeypatch.delenv('AGENTFLOW_PLANNER_API_KEY', raising=False)
    (tmp_path / 'planner.local.json').write_text(json.dumps({'apiKey': 'fictional-planner-credential'}))
    (tmp_path / '.cache').mkdir()
    (tmp_path / '.cache/local-environment.json').write_text(json.dumps({
        'AGENTFLOW_OWNER_KEY': '0x' + '34' * 32,
        'AGENTFLOW_TOKEN_ADDRESS': '0x' + 'ab' * 20,
    }))
    secrets = local_secrets(tmp_path)
    assert b'fictional-planner-credential' in secrets
    assert ('34' * 32).encode() in secrets
    # Contract addresses are public; do not treat them as private token values.
    assert ('0x' + 'ab' * 20).encode() not in secrets


def test_staging_cannot_hide_private_file_or_indexed_secret(tmp_path):
    def git(*args):
        return subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)
    git('init', '-q')
    (tmp_path / '.gitignore').write_text('planner.local.json\n')
    (tmp_path / 'planner.local.json').write_text('{"apiKey":""}')
    (tmp_path / 'notes.txt').write_text('sk-' + 'fictionaltest1234' * 3)
    git('add', '.gitignore', 'notes.txt')
    git('add', '-f', 'planner.local.json')
    # Clean working file does not mean the staged blob is clean.
    (tmp_path / 'notes.txt').write_text('clean replacement')
    _, findings = check(tmp_path, staged=True)
    assert dict(findings)['planner.local.json'] == ['private-file']
    assert 'provider-api-key' in dict(findings)['notes.txt']


def test_scan_errors_do_not_include_malformed_json_contents(tmp_path, capsys, monkeypatch):
    import scripts.check_public_source as guard
    (tmp_path / 'planner.local.json').write_text('{not json: fictional-sensitive-value}')
    monkeypatch.setattr(guard, 'ROOT', tmp_path)
    monkeypatch.setattr(guard, 'git', lambda *args: b'')
    monkeypatch.setattr('sys.argv', ['check_public_source.py'])
    assert guard.main() == 2
    assert 'fictional-sensitive-value' not in capsys.readouterr().out


def test_package_rejects_copied_local_api_key_before_creating_zip(tmp_path, monkeypatch):
    from scripts import package_submission as package
    fake = 'fictional-' + 'private-planner-value'
    (tmp_path / 'planner.local.json').write_text(json.dumps({'apiKey': fake}))
    source = tmp_path / 'notes.txt'
    source.write_text('Accidental paste: ' + fake)
    monkeypatch.setattr(package, 'ROOT', tmp_path)
    monkeypatch.setattr(package, 'source_files', lambda root: [source])
    with pytest.raises(RuntimeError, match='Refusing sensitive package file') as error:
        package.run()
    assert fake not in str(error.value)
    assert not (tmp_path / 'submission/AgentFlow-Pay-unified.zip').exists()


def test_checksum_valid_archive_still_rejects_model_config(tmp_path):
    import hashlib
    import zipfile
    from scripts.verify_submission import run
    name = 'src/planner.local.json'
    content = b'{"apiKey":""}'
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('submission/' + name, content)
        output.writestr('submission/SHA256SUMS.txt', hashlib.sha256(content).hexdigest() + '  ' + name + '\n')
    with pytest.raises(ValueError, match='sensitive archive entry'):
        run(archive)
