$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3.11 -m venv .venv }
    else { python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 virtual environment creation failed' }
}
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $projectPython -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
$foundryDirectory = Join-Path $projectRoot '.tools\foundry'
if (-not (Test-Path -LiteralPath (Join-Path $foundryDirectory 'forge.exe'))) {
    New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot '.tools') | Out-Null
    $archivePath = Join-Path $projectRoot '.tools\foundry.zip'
    Invoke-WebRequest -Uri 'https://github.com/foundry-rs/foundry/releases/download/v1.5.1/foundry_v1.5.1_win32_amd64.zip' -OutFile $archivePath
    $expectedHash = '293fedd9ad45eb9d774008815dbda820bddd3c85cab3baf55a0be3775e86d5ae'
    if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
        throw 'Foundry archive checksum mismatch'
    }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $foundryDirectory -Force
}
if (-not (Test-Path -LiteralPath 'contracts\lib\openzeppelin-contracts\contracts\utils\cryptography\EIP712.sol')) {
    git clone --depth 1 --branch v5.2.0 https://github.com/OpenZeppelin/openzeppelin-contracts.git contracts/lib/openzeppelin-contracts
    if ($LASTEXITCODE -ne 0) { throw 'OpenZeppelin download failed' }
}
if (-not (Test-Path -LiteralPath 'contracts\lib\forge-std\src\Test.sol')) {
    git clone --depth 1 --branch v1.9.7 https://github.com/foundry-rs/forge-std.git contracts/lib/forge-std
    if ($LASTEXITCODE -ne 0) { throw 'forge-std download failed' }
}
& $projectPython scripts/module_c.py build
if ($LASTEXITCODE -ne 0) { throw 'Contract build failed' }
Write-Output 'Module C dependencies ready. Follow README.md for chain deployment.'
