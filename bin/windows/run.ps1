# Windows entry point. Run from PowerShell.
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Root = Split-Path -Parent (Split-Path -Parent $Here)
$Repo = "Nowcast-in-a-Box/Nowcast-in-a-box"
switch ($env:PROCESSOR_ARCHITECTURE) {
    "AMD64" { $GoArch = "amd64" }
    "ARM64" { $GoArch = "arm64" }
    default { Write-Error "unsupported architecture: $($env:PROCESSOR_ARCHITECTURE)" }
}
$Name = "nib-handler-windows-${GoArch}.exe"
$Bin = Join-Path $Here $Name
if (-not (Test-Path $Bin)) {
    $Url = "https://github.com/$Repo/releases/latest/download/$Name"
    Write-Host "fetching $Url"
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Bin -UseBasicParsing
    } catch {
        if (Test-Path $Bin) {
            Remove-Item $Bin
        }
        Write-Error @"
no handler binary at $Bin
Download it from a GitHub Release, or build with scripts/cross-build.sh, and put it there.
"@
    }
}
if ($args.Count -gt 0 -and $args[0] -eq "diagnostics") {
    $DiagnosticsArgs = @($args | Select-Object -Skip 1)
    & $Bin diagnostics -config (Join-Path $Root "config.yaml") @DiagnosticsArgs
} elseif ($args.Count -gt 0 -and $args[0] -in @("start", "serve", "status", "stop", "restart", "connect", "uninstall")) {
    $Action = $args[0]
    $Remaining = @($args | Select-Object -Skip 1)
    & $Bin $Action -config (Join-Path $Root "config.yaml") @Remaining
} else {
    & $Bin -config (Join-Path $Root "config.yaml") @args
}
exit $LASTEXITCODE
