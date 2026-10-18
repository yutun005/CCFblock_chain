"""Package GitHub source once; optionally attach separately generated experiment evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.repository_files import source_files


def run(include_evidence=False, evidence_dir=None):
    files = [(p, 'src/' + p.relative_to(ROOT).as_posix()) for p in source_files(ROOT)]
    evidence_root = Path(evidence_dir).resolve() if evidence_dir else ROOT
    if evidence_dir and not include_evidence:
        raise ValueError('--evidence-dir requires --include-evidence')
    if include_evidence:
        evidence = []
        for folder in ('experiments/raw', 'experiments/results'):
            evidence.extend(p for p in (evidence_root / folder).rglob('*') if p.is_file())
        if not evidence:
            raise RuntimeError('No experiment evidence found; run tests or set --evidence-dir')
        for p in sorted(evidence):
            if p.is_symlink() or p.suffix in {'.key', '.pem', '.sqlite', '.sqlite3'} or p.name.startswith('local-keys'):
                raise RuntimeError(f'Refusing private/runtime evidence file: {p.name}')
            files.append((p, p.relative_to(evidence_root).as_posix()))
    # Scan for actual local test keys, in addition to Git exclusions.
    secrets = []
    keys = ROOT / 'deployments/local-keys.json'
    if keys.is_file():
        secrets.extend(v.removeprefix('0x').encode() for v in json.loads(keys.read_text(encoding='utf-8')).values()
                       if isinstance(v, str) and len(v.removeprefix('0x')) == 64)
    destination = ROOT / 'submission/AgentFlow-Pay-unified.zip'
    destination.parent.mkdir(exist_ok=True)
    manifest = []
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for p, relative in files:
            content = p.read_bytes()
            if any(key in content for key in secrets):
                raise RuntimeError(f'Refusing private key in {relative}')
            archive.writestr('submission/' + relative, content)
            manifest.append(hashlib.sha256(content).hexdigest() + '  ' + relative)
        notes = json.dumps({'scope': 'Unified A/B/C/D GitHub source; no duplicate files',
                            'includes_experiment_evidence': include_evidence,
                            'dependencies': 'Restore locked dependencies with python manage.py setup',
                            'excludes': ['virtualenv', 'node_modules', 'tool binaries', 'private runtime', 'Git metadata']},
                           indent=2).encode()
        archive.writestr('submission/PACKAGE-NOTES.json', notes)
        manifest.append(hashlib.sha256(notes).hexdigest() + '  PACKAGE-NOTES.json')
        archive.writestr('submission/SHA256SUMS.txt', '\n'.join(manifest) + '\n')
    destination.with_suffix('.sha256').write_text(hashlib.sha256(destination.read_bytes()).hexdigest() + '  ' + destination.name + '\n', encoding='utf-8')
    from scripts.verify_submission import run as verify
    record = verify(destination)
    print(json.dumps({'archive': str(destination), 'bytes': destination.stat().st_size, **record}))
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--include-evidence', action='store_true')
    parser.add_argument('--evidence-dir', type=Path)
    args = parser.parse_args()
    run(args.include_evidence, args.evidence_dir)
