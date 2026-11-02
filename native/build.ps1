param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$nativeRoot = $PSScriptRoot
$projectRoot = Split-Path -Parent $nativeRoot
$output = Join-Path $nativeRoot 'dist'
$work = Join-Path $nativeRoot '.build'
New-Item -ItemType Directory -Force -Path $output,$work | Out-Null
$buildPaths = & $Python -c "import json,os,sys; from pathlib import Path; import PySide6,shiboken6; print(json.dumps([str(Path(sys.executable).parent),sys.base_prefix,os.path.join(os.environ['SystemRoot'],'System32'),os.environ['SystemRoot'],str(Path(PySide6.__file__).parent),str(Path(shiboken6.__file__).parent)]))"
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies unavailable' }
$oldPath = $env:PATH
try {
  $env:PATH = ($buildPaths | ConvertFrom-Json) -join ';'
  & $Python -m PyInstaller --noconfirm --clean --onefile --console --noupx --hidden-import win32com.server.util --hidden-import win32com.server.policy --hidden-import win32com.server.exception --hidden-import winerror --hidden-import onnxruntime --hidden-import onnxruntime.capi._pybind_state --collect-submodules websockets --name dsh-dafeiyu-helper --distpath $output --workpath $work --specpath $work --add-data "$(Join-Path $nativeRoot 'third_party');third_party" --paths $nativeRoot --paths (Join-Path $nativeRoot 'runtime') (Join-Path $nativeRoot 'runtime\helper.py')
  if ($LASTEXITCODE -ne 0) { throw 'Native helper build failed' }
} finally { $env:PATH = $oldPath }
Get-Item (Join-Path $output 'dsh-dafeiyu-helper.exe') | Select-Object FullName,Length
