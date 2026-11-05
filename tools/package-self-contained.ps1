param(
  [string]$RuntimeSource = (Join-Path $env:USERPROFILE '.dsh\yunlan-dsh-pet-runtime'),
  [string]$HelperSource = (Join-Path $PSScriptRoot '..\native\dist-onedir\dsh-dafeiyu-helper'),
  [string]$Out = (Join-Path $PSScriptRoot '..\dist\self-contained'),
  [string]$ModelSource = (Join-Path $env:LOCALAPPDATA 'dsh-dafeiyu\models\bge-small-zh-v1.5'),
  [switch]$IncludeModels,
  [switch]$Zip,
  [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$outPath = [IO.Path]::GetFullPath($Out)
if ($outPath -eq $root -or $outPath -eq (Join-Path $root 'dist') -or -not $outPath.StartsWith($root + [IO.Path]::DirectorySeparatorChar)) {
  throw "Output must be a child directory of the plugin repository: $outPath"
}
$electron = Join-Path $RuntimeSource 'electron\DeepSeek Harness.exe'
$prepared = Join-Path $RuntimeSource 'prepared-3c99b1b'
$helperExe = if (Test-Path -LiteralPath (Join-Path $HelperSource 'dsh-dafeiyu-helper.exe')) {
  Join-Path $HelperSource 'dsh-dafeiyu-helper.exe'
} elseif ((Get-Item -LiteralPath $HelperSource -ErrorAction SilentlyContinue).PSIsContainer) {
  Join-Path $HelperSource 'dsh-dafeiyu-helper.exe'
} else { $HelperSource }
$helperDir = Split-Path -Parent $helperExe
$required = @{
  electron = $electron
  prepared = (Join-Path $prepared 'runtime\electron-helper\index.html')
  helper = $helperExe
  host = (Join-Path $root 'desktop\main.cjs')
  codeLicense = (Join-Path $root 'LICENSE')
  assetLicense = (Join-Path $root 'ASSET_LICENSE.md')
  notice = (Join-Path $root 'NOTICE.md')
}
foreach ($entry in $required.GetEnumerator()) {
  $ok = if ($entry.Key -in 'prepared') { Test-Path -LiteralPath (Split-Path -Parent $entry.Value) } else { Test-Path -LiteralPath $entry.Value }
  if (-not $ok) { throw "Missing $($entry.Key): $($entry.Value)" }
}
$helperSize = (Get-ChildItem $helperDir -Recurse -File | Measure-Object Length -Sum).Sum
$runtimeSize = (Get-ChildItem (Join-Path $RuntimeSource 'electron') -Recurse -File | Measure-Object Length -Sum).Sum
$preparedSize = (Get-ChildItem $prepared -Recurse -File | Measure-Object Length -Sum).Sum
Write-Output ("Self-contained plan: electron={0:N1}MB helper={1:N1}MB prepared={2:N1}MB output={3}" -f ($runtimeSize/1MB), ($helperSize/1MB), ($preparedSize/1MB), $outPath)
if ($DryRun) {
  Write-Output 'Dry run: no files copied.'
  return
}
if (Test-Path -LiteralPath $outPath) { Remove-Item -LiteralPath $outPath -Recurse -Force }
New-Item -ItemType Directory -Force -Path $outPath, (Join-Path $outPath 'runtime') | Out-Null
function CopyTree([string]$From, [string]$To) {
  New-Item -ItemType Directory -Force -Path $To | Out-Null
  & robocopy $From $To /E /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -gt 7) { throw "robocopy failed ($LASTEXITCODE): $From -> $To" }
}
foreach ($name in 'src','lib','web','desktop','docs') {
  CopyTree (Join-Path $root $name) (Join-Path $outPath $name)
}
foreach ($name in 'package.json','cordis.patch.yml','README.md','CHANGELOG.md','LICENSE','ASSET_LICENSE.md','NOTICE.md','SECURITY.md','CONTRIBUTING.md','requirements-native.txt','requirements-dev.txt') {
  $source = Join-Path $root $name
  if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $outPath $name) -Force }
}
# Source files stay alongside the binary so the MIT source and native patch
# remain inspectable; tests, live probes, render tools, build caches and local
# verification are excluded.
New-Item -ItemType Directory -Force -Path (Join-Path $outPath 'native') | Out-Null
Get-ChildItem (Join-Path $root 'native') -File -Filter '*.py' |
  Where-Object { $_.Name -notlike 'test_*' -and $_.Name -notlike 'live_*' -and $_.Name -notlike 'render_*' } |
  Copy-Item -Destination (Join-Path $outPath 'native') -Force
CopyTree (Join-Path $root 'native\runtime') (Join-Path $outPath 'native\runtime')
foreach ($name in 'upstream-LICENSE','third_party','requirements-native.txt','upstream_compat.json') {
  $source = Join-Path $root "native\$name"
  if (Test-Path -LiteralPath $source) {
    if ((Get-Item -LiteralPath $source).PSIsContainer) { CopyTree $source (Join-Path $outPath "native\$name") }
    else { Copy-Item -LiteralPath $source -Destination (Join-Path $outPath "native\$name") -Force }
  }
}
CopyTree (Join-Path $RuntimeSource 'electron') (Join-Path $outPath 'runtime\electron')
CopyTree $prepared (Join-Path $outPath 'runtime\prepared-3c99b1b')
CopyTree $helperDir (Join-Path $outPath 'runtime\helper')
if ($IncludeModels) {
  if (-not (Test-Path -LiteralPath $ModelSource)) { throw "Missing optional model directory: $ModelSource" }
  CopyTree $ModelSource (Join-Path $outPath 'runtime\models\bge-small-zh-v1.5')
}
$helperHash = (Get-FileHash -LiteralPath (Join-Path $outPath 'runtime\helper\dsh-dafeiyu-helper.exe') -Algorithm SHA256).Hash
$manifest = [ordered]@{
  format = 1
  pinnedCommit = '3c99b1bb4d1bcb831880e8871718c50b64af0d9b'
  helper = [ordered]@{ path = 'runtime/helper/dsh-dafeiyu-helper.exe'; sha256 = $helperHash; bytes = (Get-Item (Join-Path $outPath 'runtime\helper\dsh-dafeiyu-helper.exe')).Length }
  electron = 'runtime/electron/DeepSeek Harness.exe'
  prepared = 'runtime/prepared-3c99b1b'
  models = if ($IncludeModels) { 'runtime/models/bge-small-zh-v1.5' } else { $null }
  licenses = @('LICENSE','ASSET_LICENSE.md','NOTICE.md','native/upstream-LICENSE')
  builtAt = (Get-Date).ToUniversalTime().ToString('o')
}
[IO.File]::WriteAllText((Join-Path $outPath 'runtime\manifest.json'), ($manifest | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $outPath 'runtime\README.md'), @'
# Bundled runtime

This directory is generated by `tools/package-self-contained.ps1`. It contains
the pinned dsh-pet renderer, Electron, and the onedir native helper. The
character assets remain under CC BY-NC-SA 4.0; see `../ASSET_LICENSE.md`.

The plugin supervisor starts `helper/dsh-dafeiyu-helper.exe` with
`--external-renderer --renderer-config <active.json>` and stops it when the DSH
plugin is disposed. Do not edit `manifest.json` after packaging.
'@, [Text.UTF8Encoding]::new($false))
$packagePath = Join-Path $outPath 'package.json'
$package = Get-Content -LiteralPath $packagePath -Raw -Encoding UTF8 | ConvertFrom-Json
$package.private = $false
$package.license = 'SEE LICENSE IN ASSET_LICENSE.md'
$files = @($package.files)
foreach ($entry in 'runtime/','ASSET_LICENSE.md') {
  if ($files -notcontains $entry) { $files += $entry }
}
$package.files = $files
[IO.File]::WriteAllText($packagePath, ($package | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))
$patchPath = Join-Path $outPath 'cordis.patch.yml'
$patch = Get-Content -LiteralPath $patchPath -Raw -Encoding UTF8
if ($patch -match 'nativeBundle:') {
  $patch = $patch -replace 'nativeBundle:\s*[^\r\n]+', 'nativeBundle: true'
} else {
  $patch = $patch -replace 'showCard: true', "showCard: true`n        nativeBundle: true"
}
[IO.File]::WriteAllText($patchPath, $patch, [Text.UTF8Encoding]::new($false))
$total = (Get-ChildItem $outPath -Recurse -File | Measure-Object Length -Sum).Sum
Write-Output ("Staged self-contained plugin: {0} files, {1:N1}MB" -f (Get-ChildItem $outPath -Recurse -File).Count, ($total/1MB))
Write-Output "Native bundle: $outPath"
if ($Zip) {
  $zipPath = "$outPath.zip"
  if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
  Compress-Archive -Path (Join-Path $outPath '*') -DestinationPath $zipPath -CompressionLevel Optimal
  Write-Output ("Zip: {0} ({1:N1}MB)" -f $zipPath, ((Get-Item $zipPath).Length/1MB))
}
exit 0
