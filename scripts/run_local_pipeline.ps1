param(
  [string]$Query = "",
  [string]$Topic = "",
  [string]$RequestJson = "",
  [string]$RawXhs = "",
  [string]$OutDir = "",
  [string]$Db = "data\xhs-trend.sqlite",
  [string]$EnvFile = ".env",
  [int]$MaxNotes = 60,
  [int]$PerQuery = 10,
  [int]$MaxComments = 30,
  [string]$FocusTopics = "",
  [int]$QueryLimit = 12,
  [int]$ScrollRounds = 5,
  [int]$DetailTimeout = 18,
  [int]$MaxDetailNotes = 0,
  [ValidateSet("full", "top", "none")]
  [string]$DetailMode = "full",
  [int]$MaxDetailFailures = 4,
  [ValidateSet("general", "latest", "popular", "comments", "collects")]
  [string]$Sort = "general",
  [switch]$NoDetail,
  [switch]$Diagnose,
  [int]$DiagnosticLimit = 8,
  [switch]$RefreshCookie,
  [switch]$NoCookieEnv,
  [switch]$UseExistingBrowser,
  [switch]$AIAnalyze,
  [string]$CdpUrl = "http://127.0.0.1:9222",
  [int]$CdpPort = 9222,
  [switch]$NoXhs,
  [switch]$NoOpen,
  [switch]$InstallDeps,
  [switch]$SmokeTest,
  [int]$Days = 7,
  [int]$LimitKeywords = 10,
  [switch]$LocalOnlyInsights,
  [switch]$LocalOnlyDiscovery,
  [switch]$EnableMediaOCR,
  [switch]$KeepMedia
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

function Write-Step([string]$Message) {
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Resolve-ProjectPath([string]$PathValue) {
  if (-not $PathValue) { return "" }
  if ([System.IO.Path]::IsPathRooted($PathValue)) { return $PathValue }
  return (Join-Path $ProjectRoot $PathValue)
}

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
  throw "Python not found. Install Python 3.10+ or create .venv first."
}

function Invoke-PythonStrict([string[]]$ArgsList, [string]$Label) {
  Write-Step $Label
  & $Python @ArgsList
  if ($LASTEXITCODE -ne 0) {
    throw "Python step failed ($Label), exit code $LASTEXITCODE."
  }
}

function Invoke-PythonOptional([string[]]$ArgsList, [string]$Label) {
  Write-Step $Label
  try {
    & $Python @ArgsList
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
      Write-Host "Warning: optional database layer failed ($Label), exit code $exitCode. Original HTML/JSON outputs are kept." -ForegroundColor Yellow
      $global:LASTEXITCODE = 0
      return $false
    }
    return $true
  } catch {
    Write-Host "Warning: optional database layer failed ($Label): $($_.Exception.Message). Original HTML/JSON outputs are kept." -ForegroundColor Yellow
    $global:LASTEXITCODE = 0
    return $false
  }
}

function Invoke-OptionalNative([string]$Label, [scriptblock]$Command) {
  Write-Step $Label
  try {
    & $Command
    $exitCode = $LASTEXITCODE
    if ($null -ne $exitCode -and $exitCode -ne 0) {
      Write-Host "Warning: optional step failed ($Label), exit code $exitCode. Original HTML/JSON outputs are kept." -ForegroundColor Yellow
      $global:LASTEXITCODE = 0
      return $false
    }
    return $true
  } catch {
    Write-Host "Warning: optional step failed ($Label): $($_.Exception.Message). Original HTML/JSON outputs are kept." -ForegroundColor Yellow
    $global:LASTEXITCODE = 0
    return $false
  }
}

function Add-ArgIfValue([ref]$ArgsList, [string]$Name, [string]$Value) {
  if ($Value) {
    $ArgsList.Value += @($Name, $Value)
  }
}

$Python = Find-Python
$DbAbs = Resolve-ProjectPath $Db
$EnvFileAbs = Resolve-ProjectPath $EnvFile

if (-not $Topic) {
  if ($Query) { $Topic = $Query } elseif ($SmokeTest) { $Topic = "trend observer smoke test" } else { $Topic = "trend-observer" }
}

if (-not $OutDir) {
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $OutDirAbs = Join-Path $ProjectRoot "outputs\local-$stamp"
} else {
  $OutDirAbs = Resolve-ProjectPath $OutDir
}
New-Item -ItemType Directory -Force $OutDirAbs | Out-Null

Write-Step "Local pipeline context"
Write-Host "Project root: $ProjectRoot"
Write-Host "Python: $Python"
Write-Host "Database: $DbAbs"
Write-Host "Output directory: $OutDirAbs"
if (Test-Path $EnvFileAbs) {
  Write-Host ".env: found"
} else {
  Write-Host "Warning: .env not found at $EnvFileAbs. Steps that need API keys or cookies may warn/fail, but HTML generation can still run when raw data is available." -ForegroundColor Yellow
}

$initOk = Invoke-PythonOptional @("scripts\init_database.py", "--db", $DbAbs) "Initialize local SQLite database"

$oldDbPath = $env:XHS_DB_PATH
$env:XHS_DB_PATH = $DbAbs
try {
  $runner = Join-Path $ProjectRoot "trend_xhs_observer.ps1"
  if (-not (Test-Path $runner)) {
    throw "Original runner not found: $runner"
  }

  $runnerArgs = @(
    "-Topic", $Topic,
    "-OutDir", $OutDirAbs,
    "-MaxNotes", "$MaxNotes",
    "-PerQuery", "$PerQuery",
    "-MaxComments", "$MaxComments",
    "-QueryLimit", "$QueryLimit",
    "-ScrollRounds", "$ScrollRounds",
    "-DetailTimeout", "$DetailTimeout",
    "-MaxDetailNotes", "$(if ($MaxDetailNotes -gt 0) { $MaxDetailNotes } else { $MaxNotes })",
    "-DetailMode", $DetailMode,
    "-MaxDetailFailures", "$MaxDetailFailures",
    "-Sort", $Sort,
    "-EnvFile", $EnvFileAbs
  )
  Add-ArgIfValue ([ref]$runnerArgs) "-Query" $Query
  Add-ArgIfValue ([ref]$runnerArgs) "-RequestJson" (Resolve-ProjectPath $RequestJson)
  Add-ArgIfValue ([ref]$runnerArgs) "-RawXhs" (Resolve-ProjectPath $RawXhs)
  Add-ArgIfValue ([ref]$runnerArgs) "-FocusTopics" $FocusTopics
  Add-ArgIfValue ([ref]$runnerArgs) "-CdpUrl" $CdpUrl
  $runnerArgs += @("-CdpPort", "$CdpPort")
  if ($NoDetail) { $runnerArgs += "-NoDetail" }
  if ($Diagnose) { $runnerArgs += @("-Diagnose", "-DiagnosticLimit", "$DiagnosticLimit") }
  if ($RefreshCookie) { $runnerArgs += "-RefreshCookie" }
  if ($NoCookieEnv) { $runnerArgs += "-NoCookieEnv" }
  if ($UseExistingBrowser) { $runnerArgs += "-UseExistingBrowser" }
  if ($AIAnalyze -and -not $EnableMediaOCR) { $runnerArgs += "-AIAnalyze" }
  if ($NoXhs) { $runnerArgs += "-NoXhs" }
  if ($NoOpen) { $runnerArgs += "-NoOpen" }
  if ($InstallDeps) { $runnerArgs += "-InstallDeps" }
  if ($SmokeTest) { $runnerArgs += "-SmokeTest" }

  Write-Step "Run original HTML/JSON/Gemini pipeline"
  & powershell -NoProfile -ExecutionPolicy Bypass -File $runner @runnerArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Original pipeline failed, exit code $LASTEXITCODE."
  }
} finally {
  if ($null -eq $oldDbPath) {
    Remove-Item Env:\XHS_DB_PATH -ErrorAction SilentlyContinue
  } else {
    $env:XHS_DB_PATH = $oldDbPath
  }
}

$RequestOut = Join-Path $OutDirAbs "request.json"
$RawOut = Join-Path $OutDirAbs "raw-xhs.json"
$DetailAuditJsonOut = Join-Path $OutDirAbs "detail-open-audit.json"
$DetailAuditMarkdownOut = Join-Path $OutDirAbs "detail-open-audit.md"
$NormalizedOut = Join-Path $OutDirAbs "normalized-research.json"
$ReportOut = Join-Path $OutDirAbs "trend-report.json"
$HtmlOut = Join-Path $OutDirAbs "report.html"
$IndexHtmlOut = Join-Path $OutDirAbs "index.html"
$DiagnosticsDir = Join-Path $OutDirAbs "diagnostics"
$QualityAuditOut = Join-Path $OutDirAbs "quality-audit.json"
$AiInputOut = Join-Path $OutDirAbs "ai-input.json"
$AiInputCompatOut = Join-Path $OutDirAbs "ai_input.json"
$AiInputMarkdownOut = Join-Path $OutDirAbs "ai_input.md"
$AiPromptOut = Join-Path $OutDirAbs "ai-analysis-prompt.md"
$AiInputEffectiveJsonOut = Join-Path $OutDirAbs "ai_input_effective.json"
$AiInputEffectiveMdOut = Join-Path $OutDirAbs "ai_input_effective.md"
$AiInputQualitySummaryOut = Join-Path $OutDirAbs "ai_input_quality_summary.json"
$AiInputUpgradeReportOut = Join-Path $OutDirAbs "ai-input-upgrade-report.md"
$AiOutputJsonOut = Join-Path $OutDirAbs "ai_output.json"
$AiOutputMarkdownOut = Join-Path $OutDirAbs "ai_output.md"
$AiInputCompactMarkdownOut = Join-Path $OutDirAbs "ai_input_compact.md"
$AiOutputSummaryJsonOut = Join-Path $OutDirAbs "ai_output_summary.json"
$AiOutputSummaryMarkdownOut = Join-Path $OutDirAbs "ai_output_summary.md"
$AiErrorsOut = Join-Path $OutDirAbs "ai_errors.json"
$DbSyncReportOut = Join-Path $OutDirAbs "db-sync-report.json"
$ObservationReportOut = Join-Path $OutDirAbs "observation-report.json"
$TrendSummaryOut = Join-Path $OutDirAbs "trend-db-summary.json"
$TrendDbReportOut = Join-Path $OutDirAbs "trend-db-report.md"
$InsightReportOut = Join-Path $OutDirAbs "insight-report.json"
$DiscoverMdOut = Join-Path $OutDirAbs "discovered-keywords.md"
$DiscoverJsonOut = Join-Path $OutDirAbs "discovered-keywords.json"
$PlanMdOut = Join-Path $OutDirAbs "keyword-task-plan.md"
$PlanJsonOut = Join-Path $OutDirAbs "keyword-task-plan.json"
$RequestDraftOut = Join-Path $OutDirAbs "request-draft.json"
$HealthMdOut = Join-Path $OutDirAbs "trend-loop-health.md"
$HealthJsonOut = Join-Path $OutDirAbs "trend-loop-health.json"
$MediaManifestOut = Join-Path $OutDirAbs "media-manifest.json"
$MediaReportOut = Join-Path $OutDirAbs "media-report.md"
$MediaTextJsonOut = Join-Path $OutDirAbs "media-text.json"
$MediaTextMdOut = Join-Path $OutDirAbs "media-text.md"
$MediaCleanupJsonOut = Join-Path $OutDirAbs "media-cleanup-report.json"
$MediaCleanupMdOut = Join-Path $OutDirAbs "media-cleanup-report.md"
$EffectiveNotesJsonOut = Join-Path $OutDirAbs "effective-notes.json"
$EffectiveNotesMdOut = Join-Path $OutDirAbs "effective-notes.md"
$ExtractionQualityJsonOut = Join-Path $OutDirAbs "extraction-quality-report.json"
$ExtractionQualityMdOut = Join-Path $OutDirAbs "extraction-quality-report.md"

$CollectionIncomplete = $false
try {
  if (Test-Path $RawOut) {
    $RawForCollectionGate = Get-Content -Raw -Encoding UTF8 $RawOut | ConvertFrom-Json
    $CollectionIncomplete = [bool]$RawForCollectionGate.meta.collection_incomplete
  }
} catch {
  $CollectionIncomplete = $false
}

if ($CollectionIncomplete) {
  Write-Host "Warning: collection_incomplete=true. Skipping SQLite sync, observations, insights, keyword discovery, task planning, OCR, and AI side layers for this run. Check detail-open-audit first." -ForegroundColor Yellow
}

if ($initOk -and -not $CollectionIncomplete) {
  $syncArgs = @(
    "scripts\sync_run_to_database.py",
    "--db", $DbAbs,
    "--request", $RequestOut,
    "--raw", $RawOut,
    "--report", $ReportOut,
    "--quality-audit", $QualityAuditOut,
    "--out-dir", $OutDirAbs,
    "--json-report", $DbSyncReportOut
  )
  if (Test-Path $AiInputOut) { $syncArgs += @("--ai-input", $AiInputOut) }
  if (Test-Path $AiOutputJsonOut) { $syncArgs += @("--ai-output", $AiOutputJsonOut) }
  $null = Invoke-PythonOptional $syncArgs "Sync this run into long-term SQLite"

  $null = Invoke-PythonOptional @(
    "scripts\generate_observations.py",
    "--db", $DbAbs,
    "--json-report", $ObservationReportOut
  ) "Generate observations"

  $null = Invoke-PythonOptional @(
    "scripts\generate_trend_report.py",
    "--db", $DbAbs,
    "--output-dir", $OutDirAbs,
    "--days", "$Days",
    "--top-n", "20"
  ) "Generate database trend report"

  $insightArgs = @(
    "scripts\generate_insights.py",
    "--db", $DbAbs,
    "--days", "$Days",
    "--env-file", $EnvFileAbs,
    "--json-report", $InsightReportOut
  )
  if ($LocalOnlyInsights) { $insightArgs += "--local-only" }
  $null = Invoke-PythonOptional $insightArgs "Generate insights"

  $discoverArgs = @(
    "scripts\discover_keywords.py",
    "--db", $DbAbs,
    "--days", "$Days",
    "--output-dir", $OutDirAbs,
    "--env-file", $EnvFileAbs
  )
  if (Test-Path $TrendSummaryOut) { $discoverArgs += @("--trend-summary", $TrendSummaryOut) }
  if ($LocalOnlyDiscovery) { $discoverArgs += "--local-only" }
  $null = Invoke-PythonOptional $discoverArgs "Discover candidate keywords"

  $null = Invoke-PythonOptional @(
    "scripts\plan_keyword_tasks.py",
    "--db", $DbAbs,
    "--days", "$Days",
    "--output-dir", $OutDirAbs,
    "--limit-keywords", "$LimitKeywords",
    "--request-draft"
  ) "Plan next keyword tasks"

  $null = Invoke-PythonOptional @(
    "scripts\verify_trend_loop.py",
    "--db", $DbAbs,
    "--output-dir", $OutDirAbs
  ) "Verify trend loop health"

  $rerenderArgs = @(
    "scripts\render_trend_page.py",
    "--normalized", $NormalizedOut,
    "--raw", $RawOut,
    "--diagnostics-dir", $DiagnosticsDir,
    "--report-out", $ReportOut,
    "--html-out", $HtmlOut
  )
  if (Test-Path $AiOutputJsonOut) { $rerenderArgs += @("--ai-output-json", $AiOutputJsonOut) }
  $AiOutputMarkdownOut = Join-Path $OutDirAbs "ai_output.md"
  if (Test-Path $AiOutputMarkdownOut) { $rerenderArgs += @("--ai-output-md", $AiOutputMarkdownOut) }
  $rerenderOk = Invoke-PythonOptional $rerenderArgs "Re-render final intelligence workbench HTML"
  if ($rerenderOk -and (Test-Path $HtmlOut)) {
    Copy-Item -LiteralPath $HtmlOut -Destination $IndexHtmlOut -Force
  }
} elseif (-not $initOk) {
  Write-Host "Warning: database initialization failed, so database-side layers were skipped. Original outputs are still available." -ForegroundColor Yellow
}

if ($EnableMediaOCR -and $CollectionIncomplete) {
  Write-Host "Warning: media OCR / effective AI input skipped because collection_incomplete=true. Check detail-open-audit first." -ForegroundColor Yellow
} elseif ($EnableMediaOCR) {
  $null = Invoke-PythonOptional @(
    "scripts\extract_note_media.py",
    "--run-dir", $OutDirAbs
  ) "Extract note media images"

  $ocrArgs = @(
    "scripts\ocr_note_media.py",
    "--manifest", $MediaManifestOut
  )
  if ($KeepMedia) {
    $ocrArgs += "--keep-media"
  } else {
    $ocrArgs += "--cleanup-media"
  }
  $null = Invoke-PythonOptional $ocrArgs "OCR note media images"

  $null = Invoke-PythonOptional @(
    "scripts\build_effective_text.py",
    "--run-dir", $OutDirAbs
  ) "Build effective note text"

  $null = Invoke-PythonOptional @(
    "scripts\audit_extraction_quality.py",
    "--run-dir", $OutDirAbs
  ) "Audit extraction quality with effective text"

  if (Test-Path $EffectiveNotesJsonOut) {
    $null = Invoke-PythonOptional @(
      "scripts\export_ai_input.py",
      "--report", $ReportOut,
      "--quality-audit", $QualityAuditOut,
      "--effective-notes", $EffectiveNotesJsonOut,
      "--extraction-quality", $ExtractionQualityJsonOut,
      "--out", $AiInputOut,
      "--markdown-out", $AiInputMarkdownOut,
      "--prompt-out", $AiPromptOut,
      "--effective-out", $AiInputEffectiveJsonOut,
      "--effective-markdown-out", $AiInputEffectiveMdOut,
      "--quality-summary-out", $AiInputQualitySummaryOut,
      "--upgrade-report-out", $AiInputUpgradeReportOut
    ) "Export effective AI input package"
  } else {
    Write-Host "Warning: effective-notes.json was not found, so AI input remains on the legacy structured-notes path." -ForegroundColor Yellow
  }

  if ($AIAnalyze -and $CollectionIncomplete) {
    Write-Host "Warning: collection_incomplete=true, skipping Gemini analysis for this run. Check detail-open-audit first." -ForegroundColor Yellow
  } elseif ($AIAnalyze) {
    $null = Invoke-PythonOptional @(
      "scripts\gemini_analyze.py",
      "--input-md", $AiInputMarkdownOut,
      "--input-json", $AiInputOut,
      "--out-json", $AiOutputJsonOut,
      "--out-md", $AiOutputMarkdownOut,
      "--env-file", $EnvFileAbs,
      "--timeout", "120",
      "--retries", "1",
      "--retry-wait-seconds", "5",
      "--chunk-size", "8"
    ) "Analyze effective AI input with Gemini"

    $rerenderArgs = @(
      "scripts\render_trend_page.py",
      "--normalized", $NormalizedOut,
      "--raw", $RawOut,
      "--ai-output-json", $AiOutputJsonOut,
      "--ai-output-md", $AiOutputMarkdownOut,
      "--diagnostics-dir", $DiagnosticsDir,
      "--report-out", $ReportOut,
      "--html-out", $HtmlOut
    )
    $rerenderOk = Invoke-PythonOptional $rerenderArgs "Re-render final report with effective Gemini analysis"
    if ($rerenderOk -and (Test-Path $HtmlOut)) {
      Copy-Item -LiteralPath $HtmlOut -Destination $IndexHtmlOut -Force
    }
  }
} else {
  Write-Host "Media OCR layer skipped. Use -EnableMediaOCR to run extract_note_media.py, ocr_note_media.py, build_effective_text.py, and audit_extraction_quality.py." -ForegroundColor Yellow
}

Write-Step "Local pipeline outputs"
$paths = @(
  $HtmlOut,
  $IndexHtmlOut,
  $ReportOut,
  $DetailAuditJsonOut,
  $DetailAuditMarkdownOut,
  $DbSyncReportOut,
  $ObservationReportOut,
  $TrendDbReportOut,
  $TrendSummaryOut,
  $InsightReportOut,
  $AiInputOut,
  $AiInputCompatOut,
  $AiInputMarkdownOut,
  $AiPromptOut,
  $AiInputEffectiveJsonOut,
  $AiInputEffectiveMdOut,
  $AiInputQualitySummaryOut,
  $AiInputUpgradeReportOut,
  $AiInputCompactMarkdownOut,
  $AiOutputJsonOut,
  $AiOutputMarkdownOut,
  $AiOutputSummaryJsonOut,
  $AiOutputSummaryMarkdownOut,
  $AiErrorsOut,
  $DiscoverMdOut,
  $DiscoverJsonOut,
  $PlanMdOut,
  $PlanJsonOut,
  $RequestDraftOut,
  $HealthMdOut,
  $HealthJsonOut,
  $MediaManifestOut,
  $MediaReportOut,
  $MediaTextJsonOut,
  $MediaTextMdOut,
  $MediaCleanupJsonOut,
  $MediaCleanupMdOut,
  $EffectiveNotesJsonOut,
  $EffectiveNotesMdOut,
  $ExtractionQualityJsonOut,
  $ExtractionQualityMdOut
)
foreach ($path in $paths) {
  if (Test-Path $path) {
    Write-Host $path
  }
}

Write-Host ""
Write-Host "Done. This script did not overwrite the formal request.json and did not start any keyword task automatically." -ForegroundColor Green
