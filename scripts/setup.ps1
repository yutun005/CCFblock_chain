$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
python manage.py setup
if ($LASTEXITCODE -ne 0) { throw 'Setup failed' }
