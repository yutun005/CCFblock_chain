#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m ruff check services scripts experiments tests infra
.venv/bin/python scripts/module_c.py test-contract
.venv/bin/python -m pytest -q
