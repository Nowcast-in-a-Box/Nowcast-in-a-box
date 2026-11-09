@echo off
rem Double-click entry point for Windows: ensure the Handler is running, then
rem open the local console page in the default browser.
rem
rem Note: when this script starts the Handler, it runs in its own window;
rem closing that window stops it again (same as Start NiB.cmd).
set "NIBROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$l = Join-Path $env:NIBROOT 'bin\windows\run.ps1';" ^
  "$u = $null;" ^
  "try { $o = & $l status; if ($LASTEXITCODE -eq 0) { foreach ($x in $o) { if ($x -match '^console (http\S+)') { $u = $Matches[1] } } } } catch {};" ^
  "if (-not $u) {" ^
  "  Write-Host 'Starting the Handler in a new window...';" ^
  "  Start-Process powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$l,'start');" ^
  "  for ($i = 0; $i -lt 30 -and -not $u; $i++) { Start-Sleep -Seconds 1; try { $o = & $l status; if ($LASTEXITCODE -eq 0) { foreach ($x in $o) { if ($x -match '^console (http\S+)') { $u = $Matches[1] } } } } catch {} }" ^
  "};" ^
  "if (-not $u) { Write-Host 'Common causes: Docker Desktop is not running, a data'; Write-Host 'directory from config.yaml is missing, or an older Handler is still running.'; Write-Host 'Status output:'; try { $o = & $l status; $o | ForEach-Object { Write-Host ('  ' + $_) } } catch {}; exit 1 };" ^
  "Start-Process $u"
