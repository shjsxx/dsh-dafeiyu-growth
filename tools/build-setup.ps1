$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { throw "csc.exe not found: $csc" }
$out = Join-Path $root "dist\YunlanSetup.exe"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $out) | Out-Null
& $csc /nologo /target:winexe /out:$out /codepage:65001 `
  /r:System.dll /r:System.Core.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll `
  /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll /r:System.Net.Http.dll `
  (Join-Path $PSScriptRoot "YunlanSetup.cs")
if ($LASTEXITCODE -ne 0) { throw "YunlanSetup build failed: $LASTEXITCODE" }
$item = Get-Item $out
"SHA256=$((Get-FileHash $out -Algorithm SHA256).Hash)"
"BYTES=$($item.Length)"
"OUT=$out"
