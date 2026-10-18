"""Generate local checksums for exactly the GitHub source inventory."""
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.repository_files import source_files


def main():
    files = source_files(ROOT)
    lines = [hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.relative_to(ROOT).as_posix() for p in files]
    (ROOT / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'Checksums written for {len(files)} GitHub source files')


if __name__ == '__main__':
    main()
