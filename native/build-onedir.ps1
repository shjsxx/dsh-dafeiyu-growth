param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$nativeRoot = $PSScriptRoot
$projectRoot = Split-Path -Parent $nativeRoot
$output = Join-Path $nativeRoot 'dist-onedir'
$work = Join-Path $nativeRoot '.build-onedir'
New-Item -ItemType Directory -Force -Path $output, $work | Out-Null
$buildPaths = & $Python -c "import json,os,sys; from pathlib import Path; import PySide6,shiboken6; print(json.dumps([str(Path(sys.executable).parent),sys.base_prefix,os.path.join(os.environ['SystemRoot'],'System32'),os.environ['SystemRoot'],str(Path(PySide6.__file__).parent),str(Path(shiboken6.__file__).parent)]))"
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies unavailable' }
$oldPath = $env:PATH
try {
  $env:PATH = ($buildPaths | ConvertFrom-Json) -join ';'
  # Onedir avoids the per-start onefile extraction that Windows antivirus can
  # block on PySide6\MSVCP140.dll. The self-contained release uses this layout.
  & $Python -m PyInstaller --noconfirm --clean --onedir --console --noupx --hidden-import win32com.server.util --hidden-import win32com.server.policy --hidden-import win32com.server.exception --hidden-import winerror --hidden-import onnxruntime --hidden-import onnxruntime.capi._pybind_state --collect-submodules websockets --name dsh-dafeiyu-helper --distpath $output --workpath $work --specpath $work --add-data "$(Join-Path $nativeRoot 'third_party');third_party" --add-data "$(Join-Path $nativeRoot 'embedding_backend.py');." --add-data "$(Join-Path $nativeRoot 'semantic_memory.py');." --paths $nativeRoot --paths (Join-Path $nativeRoot 'runtime') (Join-Path $nativeRoot 'runtime\helper.py')
  if ($LASTEXITCODE -ne 0) { throw 'Native helper onedir build failed' }
} finally { $env:PATH = $oldPath }
$exe = Join-Path $output 'dsh-dafeiyu-helper\dsh-dafeiyu-helper.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "Missing onedir helper: $exe" }
$size = (Get-ChildItem (Split-Path -Parent $exe) -Recurse -File | Measure-Object Length -Sum).Sum
Get-Item $exe | Select-Object FullName,Length
"Onedir bundle bytes=$size"
