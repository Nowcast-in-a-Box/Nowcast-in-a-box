# Remove the locally installed Handler on Windows.
# Results, weights and the user's data folder are kept unless -Purge is given.
$ErrorActionPreference = "Stop"

$DryRun = $false
$Yes = $false
$Purge = $false
foreach ($arg in $args) {
    switch ($arg.ToLowerInvariant()) {
        "--dry-run" { $DryRun = $true }
        "--yes" { $Yes = $true }
        "-y" { $Yes = $true }
        "--purge" { $Purge = $true }
        "-h" { Write-Host "Usage: Uninstall NiB.cmd [--dry-run] [--yes] [--purge]"; exit 0 }
        "--help" { Write-Host "Usage: Uninstall NiB.cmd [--dry-run] [--yes] [--purge]"; exit 0 }
        default { Write-Error "unknown option: $arg"; exit 2 }
    }
}

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Config = Join-Path $Root "config.yaml"

function Remove-Path([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    if ($DryRun) {
        Write-Host "would remove $Path"
        return
    }
    Write-Host "remove $Path"
    Remove-Item -LiteralPath $Path -Recurse -Force
}

function Confirm-Action([string]$Prompt) {
    if ($Yes) { return $true }
    $answer = Read-Host "$Prompt [y/N]"
    return $answer -match "^[Yy]$"
}

function Clear-GeneratedDirectory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Get-ChildItem -LiteralPath $Path -Force | Where-Object { $_.Name -ne ".gitkeep" } | ForEach-Object {
        Remove-Path $_.FullName
    }
}

$Bin = $env:NIB_HANDLER_BIN
if (-not $Bin) {
    $candidate = Get-ChildItem -Path (Join-Path $Root "bin\windows") -Filter "nib-handler-windows-*.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($candidate) { $Bin = $candidate.FullName }
}
if (-not $Bin) {
    $fallback = Join-Path $Root "bin\nib-handler.exe"
    if (Test-Path -LiteralPath $fallback) { $Bin = $fallback }
}

if (-not $DryRun) {
    if (-not (Confirm-Action "Remove the local NiB Handler installation? Runs and artifacts will be kept.")) {
        Write-Host "Cancelled."
        exit 1
    }
    if ($Purge -and -not (Confirm-Action "PURGE also removes generated runs, artifacts and downloaded binaries. Continue?")) {
        Write-Host "Cancelled."
        exit 1
    }
}

if ($Bin) {
    if ($DryRun) {
        Write-Host "would stop the Handler using $Bin if it is running"
    } else {
        & $Bin uninstall -config $Config
        if ($LASTEXITCODE -ne 0) {
            Write-Error "the Handler is still serving its port and could not be stopped. Nothing was removed."
            exit 1
        }
    }
} else {
    throw "No Handler binary found; cannot verify shutdown. Nothing was removed."
}

$Registry = "HKCU:\Software\Classes\nib"
if ($DryRun -and (Test-Path -LiteralPath $Registry)) {
    Write-Host "would remove registry key $Registry"
}

$CacheRoot = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { [Environment]::GetFolderPath("LocalApplicationData") }
if ($DryRun) { Remove-Path (Join-Path $CacheRoot "NiB\handler") }
Remove-Path (Join-Path $Root "nib-settings.json")
Remove-Path (Join-Path $Root "logs\handler.log")

if ($Purge) {
    Clear-GeneratedDirectory (Join-Path $Root "runs")
    Clear-GeneratedDirectory (Join-Path $Root "artifacts")
    Remove-Path (Join-Path $Root "resources")
    Remove-Path (Join-Path $Root "logs")
    Remove-Path (Join-Path $Root "tmp")
    Get-ChildItem -Path (Join-Path $Root "bin") -Filter "nib-handler*" -File -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-Path $_.FullName
    }
}

if ($DryRun) {
    Write-Host "Dry run complete; no changes were made."
    exit 0
}

Write-Host "Local NiB Handler installation removed."
if ($Purge) {
    Write-Host "Generated runs, artifacts, resources and local binaries were also removed."
} else {
    Write-Host "Runs, artifacts and your data folder were kept."
}
