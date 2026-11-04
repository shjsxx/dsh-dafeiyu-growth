param([switch]$Rollback, [switch]$Yes)
$ErrorActionPreference = 'Stop'
$pluginRoot = Split-Path -Parent $PSScriptRoot
$upstream = Join-Path $env:USERPROFILE '.dsh\profiles\desktop\node_modules\dsh-dafeiyu'
$package = Get-Content -LiteralPath (Join-Path $upstream 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$contract = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'upstream_compat.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($package.version -ne $contract.testedVersion) { throw "This native patch is tested against dsh-dafeiyu $($contract.testedVersion) only. Rebase before upgrading." }
$target = Join-Path $upstream 'runtime\bin\win32-x64\dsh-dafeiyu-helper.exe'
$backupDir = Join-Path $PSScriptRoot 'backup'
$patchedBackup = Join-Path $backupDir 'dsh-dafeiyu-helper-0.1.23-growth-patched.exe'
$upstreamBackup = Join-Path $backupDir 'dsh-dafeiyu-helper-0.1.23-original.exe'
$source = if ($Rollback) {
  if (Test-Path -LiteralPath $patchedBackup) { $patchedBackup }
  elseif (Test-Path -LiteralPath $upstreamBackup) { $upstreamBackup }
  else { throw 'No rollback binary found in native/backup' }
} else { Join-Path $PSScriptRoot 'dist\dsh-dafeiyu-helper.exe' }
if (-not (Test-Path -LiteralPath $source)) { throw "Missing native binary: $source" }

# Local, non-repository installation assets. Kept outside the plugin package so a
# plugin reinstall cannot silently swap the pinned renderer or the Electron copy.
$assets = Join-Path $env:USERPROFILE '.dsh\yunlan-dsh-pet-runtime'
$markerDir = Join-Path $env:USERPROFILE '.dsh\yunlan-renderer'
$marker = Join-Path $markerDir 'active.json'
$hostDir = Join-Path $assets 'host'
$runtimeDir = Join-Path $assets 'prepared-3c99b1b'
$electronPath = Join-Path $assets 'electron\DeepSeek Harness.exe'
$userDataDir = Join-Path $markerDir 'user-data'

if (-not $Rollback) {
  foreach ($required in @(
      (Join-Path $runtimeDir 'runtime\electron-helper\index.html'),
      (Join-Path $runtimeDir 'runtime\electron-helper\shared-core.js'),
      (Join-Path $runtimeDir 'assets\config.jsonc'),
      $electronPath)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing pinned runtime asset: $required" }
  }
  New-Item -ItemType Directory -Force -Path $hostDir, $markerDir, $userDataDir | Out-Null
  foreach ($name in 'main.cjs', 'preload.cjs', 'extension.js', 'config.cjs', 'presentation-host.cjs', 'control-channel.cjs') {
    Copy-Item -LiteralPath (Join-Path $pluginRoot "desktop\$name") -Destination (Join-Path $hostDir $name) -Force
  }
  $config = [ordered]@{
    enabled = $true
    electronPath = $electronPath
    scriptPath = (Join-Path $hostDir 'main.cjs')
    runtimeRoot = $runtimeDir
    userDataDir = $userDataDir
  }
  [IO.File]::WriteAllText($marker, ($config | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
  Write-Output "Wrote explicit external-renderer activation marker: $marker"
} else {
  if (Test-Path -LiteralPath $marker) {
    $disabled = "$marker.disabled-$(Get-Date -Format yyyyMMddHHmmss)"
    Move-Item -LiteralPath $marker -Destination $disabled -Force
    Write-Output "Disabled external renderer marker (kept for re-enable): $disabled"
  }
}

if (-not $Yes) {
  $answer = Read-Host "Overwrite $target and restart only the pet (not DSH)? Type yes to continue"
  if ($answer -ne 'yes') { throw 'Installation cancelled; no file was changed.' }
}
# Keep local rollback copies of both the pre-migration helper (exact previous
# state, including our own Qt patch) and the untouched upstream helper. The
# backup directory is deliberately not committed and not shipped.
if (-not $Rollback -and (Test-Path -LiteralPath $target)) {
  $targetHash = (Get-FileHash -LiteralPath $target).Hash
  $sourceHash = (Get-FileHash -LiteralPath $source).Hash
  New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
  if (-not (Test-Path -LiteralPath $patchedBackup) -and $targetHash -ne $sourceHash) {
    Copy-Item -LiteralPath $target -Destination $patchedBackup -Force
    Write-Output "Backed up the pre-migration helper to native/backup (local only)."
  }
  if (-not (Test-Path -LiteralPath $upstreamBackup) -and $targetHash -ne $sourceHash) {
    Copy-Item -LiteralPath $target -Destination $upstreamBackup -Force
  }
}
$hostIds = @(Get-CimInstance Win32_Process -Filter "Name='DeepSeek Harness.exe'" | Where-Object { $_.CommandLine -like '*dsh-desktop-host\lib\index.js*' } | Select-Object -ExpandProperty ProcessId)
$cacheRoot = Join-Path $env:LOCALAPPDATA 'dsh-dafeiyu\0.1.23\'
$helpers = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'dsh-dafeiyu-helper-*.exe' -and $_.ExecutablePath -like "$cacheRoot*" })
$launchers = @($helpers | Where-Object { $hostIds -contains $_.ParentProcessId })
$launcherIds = @($launchers | Select-Object -ExpandProperty ProcessId)
$toRestart = @($helpers | Where-Object { $launcherIds -contains $_.ProcessId -or $launcherIds -contains $_.ParentProcessId })
# Stop the pet before overwriting its executable: Windows locks a running image.
foreach ($process in $toRestart) {
  $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ProcessId)" -ErrorAction SilentlyContinue
  if ($current -and $current.ExecutablePath -eq $process.ExecutablePath) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
  }
}
if ($toRestart.Count -gt 0) { Start-Sleep -Milliseconds 400 }
$copied = $false
for ($attempt = 0; $attempt -lt 20 -and -not $copied; $attempt++) {
  try {
    Copy-Item -LiteralPath $source -Destination $target -Force -ErrorAction Stop
    $copied = $true
  } catch {
    Start-Sleep -Milliseconds 300
  }
}
if (-not $copied) { throw 'Unable to replace the running helper binary' }
if ((Get-FileHash $source).Hash -ne (Get-FileHash $target).Hash) { throw 'Native binary verification failed' }
Write-Output "Installed verified native binary ($((Get-Item $target).Length) bytes); rollback=$Rollback"
# Restart only this pet's launcher + UI, not the DSH host or chat session. The
# upstream helper owner restarts an exited helper with the new binary. If it
# already respawned the previous image during the copy, stop it once more so the
# next restart takes the verified file.
for ($attempt = 0; $attempt -lt 10; $attempt++) {
  $running = @(Get-CimInstance Win32_Process | Where-Object {
      $_.Name -like 'dsh-dafeiyu-helper-*.exe' -and $_.ExecutablePath -like "$cacheRoot*" })
  if ($running.Count -eq 0) { break }
  foreach ($process in $running) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Milliseconds 400
}
if ($toRestart.Count -gt 0) {
  Write-Output "Requested pet-only restart: $($toRestart.ProcessId -join ',')"
} else {
  Write-Output 'No running pet under the desktop host; change applies on next pet start.'
}
