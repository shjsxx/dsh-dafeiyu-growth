param(
  [string[]]$Names = @('待机呼吸休闲', '原地左转奔跑', '点击回应-开心跃动', '点击回应-害羞惊讶',
                       '工作状态-思考冒泡', '工作状态-雀跃庆祝', '睡觉-循环'),
  [string]$RuntimeRoot = (Join-Path $env:USERPROFILE '.dsh\yunlan-dsh-pet-runtime\prepared-3c99b1b'),
  [string]$Output = (Join-Path $env:USERPROFILE '.dsh\yunlan-renderer\interpolated')
)
$ErrorActionPreference = 'Stop'
$source = Join-Path $RuntimeRoot 'assets\webm'
New-Item -ItemType Directory -Force -Path $Output | Out-Null
foreach ($name in $Names) {
  $input = Join-Path $source "$name.webm"
  if (-not (Test-Path -LiteralPath $input)) { throw "Missing source animation: $input" }
  $target = Join-Path $Output "$name.webm"
  # libvpx is required to decode the WebM alpha side data. minterpolate works on
  # the color planes; yuva420p output preserves the original alpha channel.
  & ffmpeg -v error -vcodec libvpx-vp9 -i $input `
    -vf 'minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:vsbmc=1' `
    -c:v libvpx-vp9 -pix_fmt yuva420p -auto-alt-ref 0 -b:v 0 -crf 30 -row-mt 1 -cpu-used 2 -y $target
  if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed for $name" }
  $fps = & ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate -of default=nw=1:nk=1 $target
  if ($fps -ne '60/1') { throw "Unexpected frame rate for $name : $fps" }
  Write-Output ("{0}: {1} bytes, {2}" -f $name, (Get-Item -LiteralPath $target).Length, $fps)
}
Write-Output "Interpolated clips live in $Output; delete a file there to fall back to the pinned source."
