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
  & $Python -m PyInstaller --noconfirm --clean --onedir --console --noupx --hidden-import win32com.server.util --hidden-import win32com.server.policy --hidden-import win32com.server.exception --hidden-import winerror --hidden-import onnxruntime --hidden-import onnxruntime.capi._pybind_state --hidden-import evolution_service --hidden-import evolution_kernel --hidden-import relationship --hidden-import naturalization --hidden-import web_scout --hidden-import research_planner --hidden-import research_scheduler --hidden-import research_runtime --hidden-import research_queue --hidden-import memory_curator --hidden-import memory_health --hidden-import persona_reflection --hidden-import persona_history --hidden-import persona_health --hidden-import research_ui --hidden-import web_learning --hidden-import owner_web_learning --hidden-import autonomous_learning --hidden-import web_learning_ui --hidden-import memory_curation_ui --hidden-import memory_archive_ui --hidden-import persona_ui --collect-submodules websockets --name dsh-dafeiyu-helper --distpath $output --workpath $work --specpath $work --add-data "$(Join-Path $nativeRoot 'third_party');third_party" --add-data "$(Join-Path $nativeRoot 'embedding_backend.py');." --add-data "$(Join-Path $nativeRoot 'semantic_memory.py');." --paths $nativeRoot --paths (Join-Path $nativeRoot 'runtime') (Join-Path $nativeRoot 'runtime\helper.py')
  if ($LASTEXITCODE -ne 0) { throw 'Native helper onedir build failed' }
} finally { $env:PATH = $oldPath }
$exe = Join-Path $output 'dsh-dafeiyu-helper\dsh-dafeiyu-helper.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "Missing onedir helper: $exe" }
$size = (Get-ChildItem (Split-Path -Parent $exe) -Recurse -File | Measure-Object Length -Sum).Sum
Get-Item $exe | Select-Object FullName,Length
"Onedir bundle bytes=$size"
