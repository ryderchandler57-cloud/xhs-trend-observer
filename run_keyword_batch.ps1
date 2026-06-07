param(
  [string]$Keywords = "",
  [string]$KeywordFile = "",
  [string]$OutDir = "",
  [int]$MaxNotes = 5,
  [int]$PauseMinMs = 1600,
  [int]$PauseMaxMs = 4200,
  [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Find-Python {
  $candidates = @()
  if ($env:CODEX_PYTHON) { $candidates += $env:CODEX_PYTHON }
  $candidates += (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
  $candidates += (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) { return (Resolve-Path $candidate).Path }
  }
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher) { return $pyLauncher.Source }
  throw "Python not found."
}

function Get-SafeName([string]$Text) {
  if (-not $Text) { return "keyword" }
  $clean = $Text -replace '[\\/:*?"<>|]', ' '
  $clean = ($clean -replace '\s+', '-').Trim('-')
  if ($clean.Length -gt 30) { $clean = $clean.Substring(0, 30).Trim('-') }
  if (-not $clean) { return "keyword" }
  return $clean
}

function Split-Keywords([string]$Text) {
  $items = @()
  if ($Text) {
    $items += ($Text -split '[;\r\n]+' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  }
  if ($KeywordFile) {
    $path = $KeywordFile
    if (-not [System.IO.Path]::IsPathRooted($path)) { $path = Join-Path $ProjectRoot $path }
    if (-not (Test-Path $path)) { throw "Keyword file not found: $path" }
    $items += (Get-Content -LiteralPath $path -Encoding UTF8 | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith("#") })
  }
  $seen = @{}
  $out = @()
  foreach ($item in $items) {
    $key = $item.ToLowerInvariant()
    if (-not $seen.ContainsKey($key)) {
      $seen[$key] = $true
      $out += $item
    }
  }
  return $out
}

$keywordList = Split-Keywords $Keywords
if ($keywordList.Count -eq 0) {
  throw "No keywords supplied. Use -Keywords `"关键词1;关键词2`" or -KeywordFile keywords.txt."
}

if (-not $OutDir) {
  $stamp = Get-Date -Format "yyyyMMdd-HHmm"
  $OutDir = Join-Path $ProjectRoot "outputs\keyword-batch-$stamp"
} elseif (-not [System.IO.Path]::IsPathRooted($OutDir)) {
  $OutDir = Join-Path $ProjectRoot $OutDir
}

$runsDir = Join-Path $OutDir "keywords"
New-Item -ItemType Directory -Force $runsDir | Out-Null
$runner = Join-Path $ProjectRoot "trend_xhs_observer.cmd"
$python = Find-Python

Write-Host "Keyword batch output: $OutDir" -ForegroundColor Cyan
Write-Host "Keywords: $($keywordList -join ' / ')"

$index = 0
foreach ($keyword in $keywordList) {
  $index += 1
  $safe = Get-SafeName $keyword
  $runDir = Join-Path $runsDir ("{0:D3}-{1}" -f $index, $safe)
  Write-Host ""
  Write-Host "==> [$index/$($keywordList.Count)] $keyword" -ForegroundColor Cyan
  & $runner -Query $keyword -MaxNotes $MaxNotes -AIAnalyze -OutDir $runDir -NoOpen
  if ($LASTEXITCODE -ne 0) {
    throw "Keyword run failed: $keyword"
  }
  if ($index -lt $keywordList.Count) {
    $delay = Get-Random -Minimum $PauseMinMs -Maximum ($PauseMaxMs + 1)
    Write-Host "Waiting $delay ms before next keyword..."
    Start-Sleep -Milliseconds $delay
  }
}

& $python "scripts\render_keyword_batch_report.py" `
  "--batch-root" $OutDir `
  "--out-json" (Join-Path $OutDir "keyword-batch-report.json") `
  "--out-html" (Join-Path $OutDir "report.html")
if ($LASTEXITCODE -ne 0) {
  throw "Keyword batch report rendering failed."
}

Write-Host ""
Write-Host "Keyword batch done." -ForegroundColor Green
Write-Host "Report: $(Join-Path $OutDir "report.html")"
Write-Host "JSON: $(Join-Path $OutDir "keyword-batch-report.json")"

if (-not $NoOpen) {
  Start-Process -FilePath (Join-Path $OutDir "report.html") | Out-Null
}
