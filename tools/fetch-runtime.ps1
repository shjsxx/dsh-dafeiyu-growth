param(
  [Parameter(Mandatory = $true)][string]$Url,
  [Parameter(Mandatory = $true)][string]$Sha256,
  [string]$Out = (Join-Path $PSScriptRoot '..\runtime'),
  [switch]$Force
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$outPath = [IO.Path]::GetFullPath($Out)
if (-not $outPath.StartsWith($root + [IO.Path]::DirectorySeparatorChar)) {
  throw "Output must stay inside the plugin directory: $outPath"
}
if ((Test-Path -LiteralPath $outPath) -and -not $Force) {
  throw "Runtime already exists: $outPath (use -Force to replace)"
}
$temp = Join-Path ([IO.Path]::GetTempPath()) ('yunlan-runtime-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $temp | Out-Null
try {
  $archive = Join-Path $temp 'runtime.zip'
  if ($Url -match '^[A-Za-z]:\\') {
    Copy-Item -LiteralPath $Url -Destination $archive -Force
  } else {
    Invoke-WebRequest -Uri $Url -OutFile $archive -UseBasicParsing
  }
  $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToUpperInvariant()
  if ($actual -ne $Sha256.ToUpperInvariant()) {
    throw "Runtime checksum mismatch: expected $Sha256 got $actual"
  }
  $extract = Join-Path $temp 'extract'
  Expand-Archive -LiteralPath $archive -DestinationPath $extract -Force
  $candidate = $extract
  foreach ($child in 'runtime','yunlan-runtime') {
    if (Test-Path -LiteralPath (Join-Path $candidate $child)) { $candidate = Join-Path $candidate $child; break }
  }
  $needed = @{
    electron = Join-Path $candidate 'electron\DeepSeek Harness.exe'
    prepared = Join-Path $candidate 'prepared-3c99b1b\runtime\electron-helper\index.html'
    helper = Join-Path $candidate 'helper\dsh-dafeiyu-helper.exe'
  }
  foreach ($entry in $needed.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $entry.Value)) { throw "Runtime archive is missing $($entry.Key): $($entry.Value)" }
  }
  if (Test-Path -LiteralPath $outPath) { Remove-Item -LiteralPath $outPath -Recurse -Force }
  Move-Item -LiteralPath $candidate -Destination $outPath -Force
  Write-Output "Runtime installed: $outPath"
  Write-Output "Helper SHA256: $((Get-FileHash (Join-Path $outPath 'helper\dsh-dafeiyu-helper.exe') -Algorithm SHA256).Hash)"
} finally {
  if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue }
}
