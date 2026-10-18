"""List the GitHub source inventory without staging files or changing the index."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def source_files(root: Path = ROOT) -> list[Path]:
    def paths(*options):
        result = subprocess.run(["git", "-C", str(root), "ls-files", *options, "-z"],
                                check=True, capture_output=True)
        return {p.decode("utf-8") for p in result.stdout.split(b"\0") if p}
    names = paths("--cached", "--others", "--exclude-standard")
    ignored = paths("--cached", "--ignored", "--exclude-standard")
    files = [root / name for name in sorted(names - ignored) if (root / name).is_file()]
    if any(path.is_symlink() for path in files):
        raise RuntimeError("Source packaging does not follow symlinks")
    return files


def main():
    files = source_files()
    print(json.dumps({"files": len(files), "bytes": sum(p.stat().st_size for p in files),
                      "paths": [p.relative_to(ROOT).as_posix() for p in files]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
