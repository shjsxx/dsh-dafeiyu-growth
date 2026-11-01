param([switch]$Rollback, [switch]$Yes)
$ErrorActionPreference = 'Stop'
$upstream = Join-Path $env:USERPROFILE '.dsh\profiles\desktop\node_modules\dsh-dafeiyu'
$package = Get-Content -LiteralPath (Join-Path $upstream 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($package.version -ne '0.1.23') { throw 'This native patch is tested against dsh-dafeiyu 0.1.23 only. Rebase before upgrading.' }
$target = Join-Path $upstream 'runtime\bin\win32-x64\dsh-dafeiyu-helper.exe'
$source = if ($Rollback) { Join-Path $PSScriptRoot 'backup\dsh-dafeiyu-helper-0.1.23-original.exe' } else { Join-Path $PSScriptRoot 'dist\dsh-dafeiyu-helper.exe' }
if (-not (Test-Path -LiteralPath $source)) { throw "Missing native binary: $source" }
if (-not $Yes) {
  $answer = Read-Host "Overwrite $target and restart only the pet (not DSH)? Type yes to continue"
  if ($answer -ne 'yes') { throw 'Installation cancelled; no file was changed.' }
}
# Keep a local rollback copy of the upstream helper. The backup directory is
# deliberately not committed: it contains the upstream binary, not our source.
$backupDir = Join-Path $PSScriptRoot 'backup'
$backup = Join-Path $backupDir 'dsh-dafeiyu-helper-0.1.23-original.exe'
if (-not $Rollback -and -not (Test-Path -LiteralPath $backup) -and (Test-Path -LiteralPath $target)) {
  if ((Get-FileHash -LiteralPath $target).Hash -ne (Get-FileHash -LiteralPath $source).Hash) {
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    Copy-Item -LiteralPath $target -Destination $backup -Force
    Write-Output "Backed up the current upstream helper to native/backup (local only, not committed)."
  } else {
    Write-Output 'No original helper backup found: the installed helper is already patched. Reinstall dsh-dafeiyu 0.1.23 first if you need -Rollback.'
  }
}
$hostIds = @(Get-CimInstance Win32_Process -Filter "Name='DeepSeek Harness.exe'" | Where-Object { $_.CommandLine -like '*dsh-desktop-host\lib\index.js*' } | Select-Object -ExpandProperty ProcessId)
$cacheRoot = Join-Path $env:LOCALAPPDATA 'dsh-dafeiyu\0.1.23\'
$helpers = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'dsh-dafeiyu-helper-*.exe' -and $_.ExecutablePath -like "$cacheRoot*" })
$launchers = @($helpers | Where-Object { $hostIds -contains $_.ParentProcessId })
$launcherIds = @($launchers | Select-Object -ExpandProperty ProcessId)
$toRestart = @($helpers | Where-Object { $launcherIds -contains $_.ProcessId -or $launcherIds -contains $_.ParentProcessId })
Copy-Item -LiteralPath $source -Destination $target -Force
if ((Get-FileHash $source).Hash -ne (Get-FileHash $target).Hash) { throw 'Native binary verification failed' }
Write-Output "Installed verified native binary ($((Get-Item $target).Length) bytes); rollback=$Rollback"
# Restart only this pet's launcher + UI, not the DSH host or chat session.
if ($toRestart.Count -gt 0) {
  foreach ($process in $toRestart) {
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ProcessId)" -ErrorAction SilentlyContinue
    if ($current -and $current.ExecutablePath -eq $process.ExecutablePath) {
      Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
  Write-Output "Requested pet-only restart: $($toRestart.ProcessId -join ',')"
} else {
  Write-Output 'No running pet under the desktop host; change applies on next pet start.'
}
