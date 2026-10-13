#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python scripts/module_c.py test-security
.venv/bin/python scripts/module_c.py collect-results
