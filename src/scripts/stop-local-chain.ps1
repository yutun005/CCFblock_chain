$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$recordPath = Join-Path $projectRoot 'deployments\local-anvil-process.json'
if (-not (Test-Path -LiteralPath $recordPath)) { throw 'No project Anvil process record' }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$chainProcess = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
if ($chainProcess) {
    $expectedBinary = Join-Path $projectRoot '.tools\foundry\anvil.exe'
    if ($chainProcess.Path -ne $expectedBinary) { throw 'PID is not the expected project Anvil executable' }
    Stop-Process -Id $record.pid
    Write-Output 'Project Anvil stopped; experiment records preserved.'
}
