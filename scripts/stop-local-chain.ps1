$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.cache\venv\Scripts\python.exe'
& $python (Join-Path $PSScriptRoot 'local_demo.py') stop-chain
if ($LASTEXITCODE -ne 0) { throw 'Anvil checkpoint/stop failed; inspect the error above.' }
