"""Check publishable files without displaying credentials or changing the Git index."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {'.cache', '.venv', '.tools', 'node_modules', 'deployments', '.git'}
PRIVATE_NAMES = {'planner.local.json', 'runtime.json', 'local-environment.json', 'local-access.txt', 'local-keys.json'}
PATTERNS = {
    'private-key-block': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'provider-api-key': re.compile(rb'\bsk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{20,}'),
    'github-token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'aws-access-key': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'slack-token': re.compile(rb'\bxox[baprs]-[A-Za-z0-9-]{20,}'),
    'literal-wallet-key': re.compile(rb'''(?i)(?:private[_-]?key|AGENTFLOW_[A-Z_]*_KEY)["']?\s*[:=]\s*["'](?:0x)?[0-9a-f]{64}["']'''),
}


def private_path(name: str) -> bool:
    p = PurePosixPath(name.lower())
    return (bool(set(p.parts) & PRIVATE_DIRS)
            or p.name in PRIVATE_NAMES
            or (p.name.startswith('.env') and p.name != '.env.example')
            or any(p.name.endswith(s) for s in ('.key', '.pem', '.p12', '.pfx', '.keystore', '.sqlite', '.sqlite3', '.sqlite-wal', '.sqlite-shm'))
            or p.parts[:3] in {('infra', 'besu', 'generated'), ('infra', 'besu', 'data')})


def local_secrets(root: Path) -> set[bytes]:
    """Compare actual local credentials as well as recognizable provider formats."""
    found: set[bytes] = set()

    def add(value):
        if isinstance(value, str) and len(value.strip()) >= 16:
            found.add(value.strip().encode())
            if value.startswith('0x') and len(value) == 66:
                found.add(value[2:].encode())

    def collect(obj, all_values=False):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    collect(value, all_values)
                elif all_values or (key.lower() in {'apikey', 'privatekey', 'private_key', 'token', 'apitoken', 'secret'}
                                    or key.lower().endswith(('_key', '_token'))):
                    add(value)
        elif isinstance(obj, list):
            for value in obj:
                collect(value, all_values)

    for name in ('planner.local.json', '.cache/local-environment.json', 'deployments/local-keys.json'):
        path = root / name
        if path.is_file():
            # Fail closed on unreadable configuration without echoing its contents.
            collect(json.loads(path.read_text(encoding='utf-8-sig')), name.endswith('local-keys.json'))
    path = root / '.cache/local-access.txt'
    if path.is_file():
        add(path.read_text(encoding='utf-8').strip())
    for key, value in os.environ.items():
        if key.startswith('AGENTFLOW_') and ('KEY' in key or 'TOKEN' in key):
            add(value)
    return found


def inspect(name: str, content: bytes, secrets: set[bytes]) -> list[str]:
    problems = ['private-file'] if private_path(name) else []
    if any(secret in content for secret in secrets):
        problems.append('local-credential')
    problems.extend(rule for rule, pattern in PATTERNS.items() if pattern.search(content))
    return problems


def git(root: Path, *args) -> bytes:
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)


def check(root: Path, staged=False) -> tuple[int, list[tuple[str, list[str]]]]:
    options = ['--cached'] if staged else ['--cached', '--others', '--exclude-standard']
    names = sorted({p.decode('utf-8') for p in git(root, 'ls-files', '-z', *options).split(b'\0') if p})
    secrets = local_secrets(root)
    findings = []
    checked = 0
    proc = subprocess.Popen(['git', '-C', str(root), 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE) if staged else None
    try:
        for name in names:
            path = root / name
            if '\n' in name or '\r' in name:
                raise ValueError('Unsupported file name')
            if staged:
                proc.stdin.write((':' + name + '\n').encode())
                proc.stdin.flush()
                header = proc.stdout.readline().split()
                if len(header) != 3 or header[1] != b'blob':
                    raise ValueError('Cannot read indexed file')
                content = proc.stdout.read(int(header[2]))
                proc.stdout.read(1)
            else:
                if path.is_symlink():
                    findings.append((name, ['symlink']))
                    continue
                if not path.is_file():
                    continue
                content = path.read_bytes()
            checked += 1
            problems = inspect(name, content, secrets)
            if problems:
                findings.append((name, problems))
    finally:
        if proc:
            proc.stdin.close()
            proc.stdout.close()
            proc.wait()
    return checked, findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', action='store_true', help='Read every indexed blob, including force-added private files')
    args = parser.parse_args()
    try:
        count, findings = check(ROOT, args.staged)
    except (OSError, ValueError, subprocess.CalledProcessError):
        print('Source safety check could not complete. Check Git and local JSON configuration; no credentials were printed.')
        return 2
    for name, rules in findings:
        print(json.dumps({'file': name, 'rules': rules}, ensure_ascii=True))
    print(f'Source safety: {count} files checked; {len(findings)} blocked files. No credential values printed.')
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())
