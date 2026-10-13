#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
mkdir -p .tools/foundry
if [[ ! -x .tools/foundry/forge ]]; then
  case "$(uname -m)" in
    x86_64) architecture=amd64 ;;
    aarch64|arm64) architecture=arm64 ;;
    *) echo 'Unsupported CPU architecture'; exit 1 ;;
  esac
  case "$(uname -s)" in
    Linux) platform=linux ;;
    Darwin) platform=darwin ;;
    *) echo 'Use setup.ps1 for Windows'; exit 1 ;;
  esac
  curl -fL "https://github.com/foundry-rs/foundry/releases/download/v1.5.1/foundry_v1.5.1_${platform}_${architecture}.tar.gz" -o .tools/foundry.tar.gz
  tar -xzf .tools/foundry.tar.gz -C .tools/foundry
fi
if [[ ! -f contracts/lib/openzeppelin-contracts/contracts/utils/cryptography/EIP712.sol ]]; then
  git clone --depth 1 --branch v5.2.0 https://github.com/OpenZeppelin/openzeppelin-contracts.git contracts/lib/openzeppelin-contracts
fi
if [[ ! -f contracts/lib/forge-std/src/Test.sol ]]; then
  git clone --depth 1 --branch v1.9.7 https://github.com/foundry-rs/forge-std.git contracts/lib/forge-std
fi
.venv/bin/python scripts/module_c.py build
