# Run the prebuilt nib-handler on Windows. No local Go install.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Repo = "Nowcast-in-a-Box/Nowcast-in-a-box"
switch ($env:PROCESSOR_ARCHITECTURE) {
    "AMD64" { $GoArch = "amd64" }
    "ARM64" { $GoArch = "arm64" }
    default { Write-Error "unsupported architecture: $($env:PROCESSOR_ARCHITECTURE)" }
}
$Name = "nib-handler-windows-${GoArch}.exe"
$Dist = Join-Path $Root "handler\dist"
$Bin = Join-Path $Dist $Name
New-Item -ItemType Directory -Force -Path $Dist | Out-Null
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
Download it from a GitHub Release, or from the CI artifact named nib-handler, and put it there.
"@
    }
}
& $Bin -config (Join-Path $Root "config.yaml") @args
