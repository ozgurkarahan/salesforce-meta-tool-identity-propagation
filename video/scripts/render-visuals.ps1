# Renders the README / project-page visuals (Remotion <Still>s) into ../site/assets
# and checks each PNG's pixel size and weight. Usage: npm run visuals
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$assets = Join-Path (Resolve-Path "..") "site/assets"

$jobs = @(
  @{ id = "Hero";   file = "visuals/hero.png";   scale = 2; w = 2400; h = 1200 },
  @{ id = "Trust";  file = "visuals/trust.png";  scale = 2; w = 2400; h = 1120 },
  @{ id = "Extend"; file = "visuals/extend.png"; scale = 2; w = 2400; h = 1200 },
  @{ id = "OgCard"; file = "og-image.png";       scale = 1; w = 1200; h = 630 }
)
$maxKb = 600

foreach ($j in $jobs) {
  $dest = Join-Path $assets $j.file
  New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
  npx remotion still $j.id $dest --scale=$($j.scale) --log=error
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  # PNG IHDR: width and height are big-endian uint32 at bytes 16-23.
  $b = [IO.File]::ReadAllBytes($dest)
  $w = ([int]$b[16] -shl 24) -bor ([int]$b[17] -shl 16) -bor ([int]$b[18] -shl 8) -bor [int]$b[19]
  $h = ([int]$b[20] -shl 24) -bor ([int]$b[21] -shl 16) -bor ([int]$b[22] -shl 8) -bor [int]$b[23]
  $kb = [math]::Round($b.Length / 1KB)
  if ($w -ne $j.w -or $h -ne $j.h) { throw "$($j.file): expected $($j.w)x$($j.h), got ${w}x${h}" }
  if ($kb -gt $maxKb) { throw "$($j.file): $kb KB exceeds the $maxKb KB budget" }
  Write-Host ("{0,-20} {1}x{2}  {3} KB" -f $j.file, $w, $h, $kb)
}
