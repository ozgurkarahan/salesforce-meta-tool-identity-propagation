# Renders the video to out/customer360-story.mp4.
# On Windows ARM64, Remotion has no bundled FFmpeg, so we render a JPEG sequence
# and encode it with a system FFmpeg (set $env:FFMPEG or have ffmpeg on PATH).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if ($env:PROCESSOR_ARCHITECTURE -ne "ARM64") {
  npx remotion render Customer360Story out/customer360-story.mp4
  exit $LASTEXITCODE
}

$ffmpeg = $env:FFMPEG
if (-not $ffmpeg) { $ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source }
if (-not $ffmpeg) {
  $ffmpeg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
}
if (-not $ffmpeg) { throw "FFmpeg not found. Install it (winget install Gyan.FFmpeg) or set `$env:FFMPEG." }

if (Test-Path out/frames) { Remove-Item out/frames -Recurse -Force }
npx remotion render Customer360Story out/frames --sequence --image-format=jpeg --jpeg-quality=95
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$first = Get-ChildItem out/frames -Filter *.jpeg | Sort-Object Name | Select-Object -First 1
$m = [regex]::Match($first.Name, '^(.*?)(\d+)\.jpeg$')
$src = "out/frames/$($m.Groups[1].Value)%0$($m.Groups[2].Value.Length)d.jpeg"
& $ffmpeg -y -hide_banner -loglevel error -framerate 30 -start_number ([int]$m.Groups[2].Value) -i $src `
  -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -movflags +faststart out/customer360-story.mp4
Write-Host "Wrote out/customer360-story.mp4"
