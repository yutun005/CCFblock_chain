#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# No file deletion. Guard against accidentally resetting a remote / real chain.
.cache/venv/bin/python - <<'PY'
from web3 import Web3
w = Web3(Web3.HTTPProvider('http://127.0.0.1:8545'))
if w.eth.chain_id != 31337 or 'anvil' not in w.client_version.lower():
    raise SystemExit('Reset restricted to local Anvil chainId=31337')
result = w.provider.make_request('anvil_reset', [])
if 'error' in result:
    raise SystemExit(str(result['error']))
print('Local Anvil reset; redeploy contracts. Raw experiment files preserved.')
PY
