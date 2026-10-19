# Dispatches to the Windows entry under bin/.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root "bin\windows\run.ps1") @args
exit $LASTEXITCODE
