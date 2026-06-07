param(
  [string]$Query = "",
  [string]$Topic = "",
  [string]$RequestJson = "",
  [string]$RawXhs = "",
  [string]$OutDir = "",
  [int]$MaxNotes = 60,
  [int]$PerQuery = 10,
  [int]$MaxComments = 30,
  [string]$FocusTopics = "",
  [int]$QueryLimit = 12,
  [switch]$ListFocusTopics,
  [int]$ScrollRounds = 5,
  [int]$DetailTimeout = 18,
  [int]$MaxDetailNotes = 0,
  [ValidateSet("full", "top", "none")]
  [string]$DetailMode = "full",
  [int]$MaxDetailFailures = 4,
  [ValidateSet("general", "latest", "popular", "comments", "collects")]
  [string]$Sort = "general",
  [string]$CollectedIndex = "",
  [switch]$NoSkipCollected,
  [string]$ScheduleDaily = "",
  [switch]$NoDetail,
  [switch]$Diagnose,
  [int]$DiagnosticLimit = 8,
  [string]$EnvFile = "",
  [switch]$RefreshCookie,
  [switch]$NoCookieEnv,
  [switch]$CookieOnly,
  [switch]$UseExistingBrowser,
  [switch]$RunBatch,
  [switch]$AIAnalyze,
  [string]$CdpUrl = "http://127.0.0.1:9222",
  [int]$CdpPort = 9222,
  [switch]$NoXhs,
  [switch]$NoOpen,
  [switch]$InstallDeps,
  [switch]$Doctor,
  [switch]$SmokeTest
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Write-Step([string]$Message) {
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
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

function Invoke-Python([string[]]$ArgsList) {
  & $Python @ArgsList
  if ($LASTEXITCODE -ne 0) {
    throw "Python command failed: $Python $($ArgsList -join ' ')"
  }
}

function Invoke-PythonOptional([string[]]$ArgsList, [string]$Label) {
  try {
    & $Python @ArgsList | ForEach-Object { Write-Host $_ }
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
      Write-Host "Warning: optional step failed ($Label), exit code $exitCode. Existing JSON/HTML/Gemini outputs are kept." -ForegroundColor Yellow
      $global:LASTEXITCODE = 0
      return $false
    }
    return $true
  } catch {
    Write-Host "Warning: optional step failed ($Label): $($_.Exception.Message). Existing JSON/HTML/Gemini outputs are kept." -ForegroundColor Yellow
    $global:LASTEXITCODE = 0
    return $false
  }
}

function Test-PythonModule([string]$ModuleName) {
  & $Python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$ModuleName') else 1)" | Out-Null
  return ($LASTEXITCODE -eq 0)
}

function Find-Browser {
  $candidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
    (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
    (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
    (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
  )
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) { return $candidate }
  }
  throw "Chrome or Edge was not found. Install one browser, or launch your own CDP browser and pass -UseExistingBrowser."
}

function Test-Cdp([string]$Url) {
  try {
    $null = Invoke-WebRequest -UseBasicParsing -Uri "$Url/json/version" -TimeoutSec 2
    return $true
  } catch {
    return $false
  }
}

function Find-FreeCdpPort([int]$StartPort) {
  for ($port = $StartPort; $port -lt ($StartPort + 20); $port++) {
    if (-not (Test-Cdp "http://127.0.0.1:$port")) { return $port }
  }
  throw "No free CDP port found from $StartPort to $($StartPort + 19). Close old browser automation windows and retry."
}

function Get-SafeName([string]$Text) {
  if (-not $Text) { return "trend-observer" }
  $clean = $Text -replace '[\\/:*?"<>|]', ' '
  $clean = ($clean -replace '\s+', '-').Trim('-')
  if ($clean.Length -gt 36) { $clean = $clean.Substring(0, 36).Trim('-') }
  if (-not $clean) { return "trend-observer" }
  return $clean
}

$Python = Find-Python
if (-not $EnvFile) {
  $EnvFile = Join-Path $ProjectRoot ".env"
} elseif (-not [System.IO.Path]::IsPathRooted($EnvFile)) {
  $EnvFile = Join-Path $ProjectRoot $EnvFile
}
if (-not $CollectedIndex) {
  $CollectedIndex = Join-Path $ProjectRoot "work\collected-note-ids.json"
} elseif (-not [System.IO.Path]::IsPathRooted($CollectedIndex)) {
  $CollectedIndex = Join-Path $ProjectRoot $CollectedIndex
}
if ($AIAnalyze) {
  $Diagnose = $true
  if ($DiagnosticLimit -lt $MaxNotes) { $DiagnosticLimit = $MaxNotes }
}

if ($InstallDeps) {
  Write-Step "Installing Python dependencies"
  Invoke-Python @("-m", "pip", "install", "-r", (Join-Path $ProjectRoot "requirements.txt"))
}

if ($ListFocusTopics) {
  Write-Step "Available focus topics"
  Invoke-Python @("scripts\prepare_request.py", "--list-focus-topics")
  exit 0
}

if ($Doctor) {
  Write-Step "Doctor"
  Write-Host "ProjectRoot: $ProjectRoot"
  Write-Host "Python: $Python"
  Write-Host "Playwright package: $(if (Test-PythonModule 'playwright') { 'OK' } else { 'missing; run with -InstallDeps' })"
  Write-Host "Cookie env file: $EnvFile"
  Write-Host "Cookie env exists: $(if (Test-Path $EnvFile) { 'yes' } else { 'no' })"
  Write-Host "Collected index: $CollectedIndex"
  try {
    $browser = Find-Browser
    Write-Host "Browser: $browser"
  } catch {
    Write-Host "Browser: missing"
  }
  Write-Host "CDP reachable: $(if (Test-Cdp $CdpUrl) { 'yes' } else { 'no' })"
  Write-Host "Doctor OK"
  exit 0
}

if ($ScheduleDaily) {
  Write-Step "Scheduling daily run"
  if (-not $Query) { throw "-ScheduleDaily requires -Query so the scheduled task knows what to collect." }
  $taskName = "Codex XHS Trend Observer"
  $runner = Join-Path $ProjectRoot "trend_xhs_observer.cmd"
  $focusTaskArg = ""
  if ($FocusTopics) { $focusTaskArg = " -FocusTopics `"$FocusTopics`"" }
  $argsForTask = "/c cd /d `"$ProjectRoot`" && `"$runner`" -Query `"$Query`" -MaxNotes $MaxNotes -PerQuery $PerQuery -QueryLimit $QueryLimit -Sort $Sort$focusTaskArg -NoOpen"
  schtasks /Create /F /SC DAILY /TN $taskName /TR "cmd.exe $argsForTask" /ST $ScheduleDaily | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "Failed to create scheduled task." }
  Write-Host "Scheduled task created: $taskName at $ScheduleDaily"
  exit 0
}

if ($SmokeTest) {
  $NoXhs = $true
  if (-not $Topic) { $Topic = "trend observer smoke test" }
  if (-not $RawXhs) { $RawXhs = Join-Path $ProjectRoot "work\fixtures\smoke-raw-xhs.json" }
  if (-not $OutDir) { $OutDir = Join-Path $ProjectRoot "outputs\smoke-test" }
}

if (-not $Topic) {
  if ($Query) { $Topic = $Query } elseif ($RunBatch) { $Topic = "batch-trend-observer" } else { $Topic = "trend-observer" }
}
if (-not $OutDir) {
  if ($RunBatch) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmm"
    $OutDir = Join-Path $ProjectRoot "outputs\batch-$stamp"
  } else {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $safeName = Get-SafeName $Topic
    $OutDir = Join-Path $ProjectRoot "outputs\$safeName-$stamp"
  }
} elseif (-not [System.IO.Path]::IsPathRooted($OutDir)) {
  $OutDir = Join-Path $ProjectRoot $OutDir
}

New-Item -ItemType Directory -Force $OutDir | Out-Null

$RequestOut = Join-Path $OutDir "request.json"
$RawOut = Join-Path $OutDir "raw-xhs.json"
$DetailAuditJsonOut = Join-Path $OutDir "detail-open-audit.json"
$DetailAuditMarkdownOut = Join-Path $OutDir "detail-open-audit.md"
$NormalizedOut = Join-Path $OutDir "normalized-research.json"
$ReportOut = Join-Path $OutDir "trend-report.json"
$HtmlOut = Join-Path $OutDir "report.html"
$IndexHtmlOut = Join-Path $OutDir "index.html"
$StructuredNotesOut = Join-Path $OutDir "structured-notes.json"
$CsvOut = Join-Path $OutDir "structured-notes.csv"
$SqliteOut = Join-Path $OutDir "xhs-notes.sqlite"
$XlsxOut = Join-Path $OutDir "structured-notes.xlsx"
$MarkdownOut = Join-Path $OutDir "trend-report.md"
$TrendDbOut = if ($env:XHS_DB_PATH) {
  if ([System.IO.Path]::IsPathRooted($env:XHS_DB_PATH)) { $env:XHS_DB_PATH } else { Join-Path $ProjectRoot $env:XHS_DB_PATH }
} else {
  Join-Path $ProjectRoot "data\xhs-trend.sqlite"
}
$DbSyncReportOut = Join-Path $OutDir "db-sync-report.json"
$ObservationReportOut = Join-Path $OutDir "observation-report.json"
$QualityAuditOut = Join-Path $OutDir "quality-audit.json"
$QualityAuditMarkdownOut = Join-Path $OutDir "quality-audit.md"
$AiInputOut = Join-Path $OutDir "ai-input.json"
$AiInputCompatOut = Join-Path $OutDir "ai_input.json"
$AiInputMarkdownOut = Join-Path $OutDir "ai_input.md"
$AiPromptOut = Join-Path $OutDir "ai-analysis-prompt.md"
$AiOutputJsonOut = Join-Path $OutDir "ai_output.json"
$AiOutputMarkdownOut = Join-Path $OutDir "ai_output.md"
$AiInputCompactMarkdownOut = Join-Path $OutDir "ai_input_compact.md"
$AiOutputSummaryJsonOut = Join-Path $OutDir "ai_output_summary.json"
$AiOutputSummaryMarkdownOut = Join-Path $OutDir "ai_output_summary.md"
$AiErrorsOut = Join-Path $OutDir "ai_errors.json"
$DiagnosticsDir = Join-Path $OutDir "diagnostics"
$CookieHealthOut = Join-Path $OutDir "cookie-health.json"

Write-Step "Preparing request"
$prepareArgs = @(
  "scripts\prepare_request.py",
  "--out", $RequestOut,
  "--topic", $Topic,
  "--max-notes", "$MaxNotes",
  "--per-query", "$PerQuery",
  "--max-comments", "$MaxComments",
  "--query-limit", "$QueryLimit"
)
if ($Query) { $prepareArgs += @("--query", $Query) }
if ($RequestJson) { $prepareArgs += @("--request-json", $RequestJson) }
if ($FocusTopics) { $prepareArgs += @("--focus-topics", $FocusTopics) }
Invoke-Python $prepareArgs
$PreparedRequest = Get-Content -Raw -Encoding UTF8 $RequestOut | ConvertFrom-Json
Write-Host "Focus topics:"
$PreparedRequest.focus_topics | ForEach-Object { Write-Host " - $_" }
if ($PreparedRequest.meta.focus_topic_warnings) {
  $PreparedRequest.meta.focus_topic_warnings | ForEach-Object { Write-Host "Focus warning: $_" -ForegroundColor Yellow }
}
Write-Host "Search plan:"
$PreparedRequest.expanded_queries | Select-Object -First 20 | ForEach-Object { Write-Host " - $_" }

if ($NoXhs -or $RawXhs) {
  if (-not $RawXhs) { throw "-NoXhs requires -RawXhs, unless you use -SmokeTest." }
  if (-not (Test-Path $RawXhs)) { throw "Raw XHS file not found: $RawXhs" }
  Write-Step "Using existing raw XHS data"
  Copy-Item -LiteralPath $RawXhs -Destination $RawOut -Force
} else {
  if (-not (Test-PythonModule "playwright")) {
    throw "Python package 'playwright' is missing. Re-run this command with -InstallDeps."
  }

  if ($UseExistingBrowser) {
    if (-not (Test-Cdp $CdpUrl)) {
      throw "Existing CDP browser is not reachable at $CdpUrl. Remove -UseExistingBrowser or launch a CDP browser first."
    }
  } else {
    if (Test-Cdp $CdpUrl) {
      Write-Step "Using existing CDP browser"
      Write-Host "CDP: $CdpUrl"
    } else {
      $LaunchPort = Find-FreeCdpPort $CdpPort
      $CdpUrl = "http://127.0.0.1:$LaunchPort"
      Write-Step "Launching visible browser for Xiaohongshu login"
      $browser = Find-Browser
      $profile = Join-Path $ProjectRoot ".xhs-profile-$LaunchPort"
      New-Item -ItemType Directory -Force $profile | Out-Null
      $browserArgs = @(
        "--remote-debugging-port=$LaunchPort",
        "--user-data-dir=$profile",
        "--disable-extensions",
        "--disable-notifications",
        "--no-first-run",
        "--no-default-browser-check",
        "https://www.xiaohongshu.com/explore"
      )
      Start-Process -FilePath $browser -ArgumentList $browserArgs | Out-Null
      Write-Host "CDP: $CdpUrl"
      Write-Host "A browser window was opened for Xiaohongshu."
      if ($NoCookieEnv) {
        Write-Host "Cookie env is disabled. Finish XHS login or verification there."
        Read-Host "Press Enter here after the XHS page is ready"
      }
    }
  }

  if (-not $NoCookieEnv) {
    Write-Step "Checking Xiaohongshu cookie session"
    $cookieArgs = @(
      "scripts\xhs_cookie_session.py",
      "--cdp-url", $CdpUrl,
      "--env-file", $EnvFile,
      "--out", $CookieHealthOut,
      "--interactive"
    )
    if ($RefreshCookie) { $cookieArgs += "--refresh" }
    Invoke-Python $cookieArgs
    Write-Host "Cookie env: $EnvFile"
    Write-Host "Cookie health: $CookieHealthOut"
    if ($CookieOnly) {
      Write-Host "Cookie check/save completed."
      exit 0
    }
  }

  Write-Step "Collecting Xiaohongshu notes"
  $collectArgs = @(
    "scripts\collect_xhs_notes.py",
    "--request", $RequestOut,
    "--out", $RawOut,
    "--cdp-url", $CdpUrl,
    "--max-notes", "$MaxNotes",
    "--per-query", "$PerQuery",
    "--max-comments", "$MaxComments",
    "--scroll-rounds", "$ScrollRounds",
    "--detail-timeout", "$DetailTimeout",
    "--max-detail-notes", "$(if ($MaxDetailNotes -gt 0) { $MaxDetailNotes } else { $MaxNotes })",
    "--detail-mode", $DetailMode,
    "--sort", $Sort,
    "--collected-index", $CollectedIndex
  )
  if ($NoDetail) { $collectArgs += "--no-detail" }
  if ($NoSkipCollected) { $collectArgs += "--no-skip-collected" }
  if ($MaxDetailFailures) { $collectArgs += @("--max-detail-failures", "$MaxDetailFailures") }
  if ($Diagnose) { $collectArgs += @("--diagnostics-dir", $DiagnosticsDir, "--diagnostic-limit", "$DiagnosticLimit") }
  Invoke-Python $collectArgs
}

Write-Step "Normalizing trend observations"
Invoke-Python @(
  "scripts\normalize_trend_research.py",
  "--request", $RequestOut,
  "--raw", $RawOut,
  "--out", $NormalizedOut
)

Write-Step "Rendering single-file HTML"
Invoke-Python @(
  "scripts\render_trend_page.py",
  "--normalized", $NormalizedOut,
  "--raw", $RawOut,
  "--diagnostics-dir", $DiagnosticsDir,
  "--report-out", $ReportOut,
  "--html-out", $HtmlOut
)
Copy-Item -LiteralPath $HtmlOut -Destination $IndexHtmlOut -Force

Write-Step "Exporting structured artifacts"
Invoke-Python @(
  "scripts\export_xhs_artifacts.py",
  "--report", $ReportOut,
  "--notes-json", $StructuredNotesOut,
  "--csv", $CsvOut,
  "--xlsx", $XlsxOut,
  "--sqlite", $SqliteOut,
  "--markdown", $MarkdownOut
)

Write-Step "Auditing collection quality"
Invoke-Python @(
  "scripts\audit_xhs_quality.py",
  "--request", $RequestOut,
  "--raw", $RawOut,
  "--report", $ReportOut,
  "--structured-notes", $StructuredNotesOut,
  "--html", $HtmlOut,
  "--cookie-health", $CookieHealthOut,
  "--out", $QualityAuditOut,
  "--markdown", $QualityAuditMarkdownOut
)

$CollectionIncomplete = $false
$GeminiSkippedForCollection = $false
try {
  $RawForCollectionGate = Get-Content -Raw -Encoding UTF8 $RawOut | ConvertFrom-Json
  $CollectionIncomplete = [bool]$RawForCollectionGate.meta.collection_incomplete
} catch {
  $CollectionIncomplete = $false
}

if ($CollectionIncomplete) {
  $GeminiSkippedForCollection = $AIAnalyze
  Write-Host "Warning: collection_incomplete=true, skipping AI input export and Gemini analysis for this run. Check detail-open-audit first." -ForegroundColor Yellow
} else {
  Write-Step "Exporting AI analysis input package"
  Invoke-Python @(
    "scripts\export_ai_input.py",
    "--report", $ReportOut,
    "--quality-audit", $QualityAuditOut,
    "--out", $AiInputOut,
    "--markdown-out", $AiInputMarkdownOut,
    "--prompt-out", $AiPromptOut
  )
  Copy-Item -LiteralPath $AiInputOut -Destination $AiInputCompatOut -Force
}

if ($AIAnalyze -and -not $CollectionIncomplete) {
  Write-Step "Analyzing AI input with Gemini"
  Invoke-Python @(
    "scripts\gemini_analyze.py",
    "--input-md", $AiInputMarkdownOut,
    "--input-json", $AiInputOut,
    "--out-json", $AiOutputJsonOut,
    "--out-md", $AiOutputMarkdownOut,
    "--env-file", $EnvFile,
    "--timeout", "120",
    "--retries", "1",
    "--retry-wait-seconds", "5",
    "--chunk-size", "8"
  )

  Write-Step "Rendering final report with Gemini analysis"
  Invoke-Python @(
    "scripts\render_trend_page.py",
    "--normalized", $NormalizedOut,
    "--raw", $RawOut,
    "--ai-output-json", $AiOutputJsonOut,
    "--ai-output-md", $AiOutputMarkdownOut,
    "--diagnostics-dir", $DiagnosticsDir,
    "--report-out", $ReportOut,
    "--html-out", $HtmlOut
  )
  Copy-Item -LiteralPath $HtmlOut -Destination $IndexHtmlOut -Force
}

Write-Step "Validating outputs"
$validateArgs = @(
  "scripts\validate_outputs.py",
  "--request", $RequestOut,
  "--raw", $RawOut,
  "--normalized", $NormalizedOut,
  "--report", $ReportOut,
  "--html", $HtmlOut,
  "--structured-notes", $StructuredNotesOut,
  "--csv", $CsvOut,
  "--xlsx", $XlsxOut,
  "--sqlite", $SqliteOut,
  "--markdown", $MarkdownOut,
  "--quality-audit", $QualityAuditOut
)
if (Test-Path $AiInputOut) { $validateArgs += @("--ai-input", $AiInputOut) }
if (Test-Path $AiPromptOut) { $validateArgs += @("--ai-prompt", $AiPromptOut) }
if ($AIAnalyze -and -not $GeminiSkippedForCollection) { $validateArgs += @("--ai-output-json", $AiOutputJsonOut, "--ai-output-md", $AiOutputMarkdownOut) }
if (Test-PythonModule "playwright") { $validateArgs += "--browser-check" }
Invoke-Python $validateArgs

if ($CollectionIncomplete) {
  Write-Host "Warning: collection_incomplete=true, skipping long-term SQLite sync and database observation. Check detail-open-audit first." -ForegroundColor Yellow
  $dbSyncOk = $false
  $observationOk = $false
} else {
  Write-Step "Syncing long-term SQLite side store"
  $syncArgs = @(
    "scripts\sync_run_to_database.py",
    "--db", $TrendDbOut,
    "--request", $RequestOut,
    "--raw", $RawOut,
    "--report", $ReportOut,
    "--quality-audit", $QualityAuditOut,
    "--out-dir", $OutDir,
    "--json-report", $DbSyncReportOut
  )
  if (Test-Path $AiInputOut) { $syncArgs += @("--ai-input", $AiInputOut) }
  if (Test-Path $AiOutputJsonOut) { $syncArgs += @("--ai-output", $AiOutputJsonOut) }
  $dbSyncOk = Invoke-PythonOptional $syncArgs "long-term SQLite sync"

  if ($dbSyncOk) {
    Write-Step "Generating database observation"
    $observationArgs = @(
      "scripts\generate_observations.py",
      "--db", $TrendDbOut,
      "--json-report", $ObservationReportOut
    )
    $observationOk = Invoke-PythonOptional $observationArgs "database observation"
  } else {
    $observationOk = $false
  }
}

$report = Get-Content -Raw -Encoding UTF8 $ReportOut | ConvertFrom-Json
$raw = Get-Content -Raw -Encoding UTF8 $RawOut | ConvertFrom-Json
$audit = Get-Content -Raw -Encoding UTF8 $QualityAuditOut | ConvertFrom-Json

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "HTML: $HtmlOut"
Write-Host "Index HTML: $IndexHtmlOut"
Write-Host "Final JSON: $ReportOut"
Write-Host "Structured notes JSON: $StructuredNotesOut"
Write-Host "Structured notes CSV: $CsvOut"
Write-Host "Structured notes Excel: $XlsxOut"
Write-Host "SQLite: $SqliteOut"
Write-Host "Trend SQLite side store: $TrendDbOut"
if ($dbSyncOk -and (Test-Path $DbSyncReportOut)) {
  Write-Host "Trend DB sync report: $DbSyncReportOut"
}
if ($observationOk -and (Test-Path $ObservationReportOut)) {
  Write-Host "Observation report: $ObservationReportOut"
}
Write-Host "Markdown: $MarkdownOut"
Write-Host "Quality audit JSON: $QualityAuditOut"
Write-Host "Quality audit Markdown: $QualityAuditMarkdownOut"
if (Test-Path $AiInputOut) { Write-Host "AI input JSON: $AiInputOut" }
if (Test-Path $AiInputCompatOut) { Write-Host "AI input JSON (compat): $AiInputCompatOut" }
if (Test-Path $AiInputMarkdownOut) { Write-Host "AI input Markdown: $AiInputMarkdownOut" }
if (Test-Path $AiPromptOut) { Write-Host "AI analysis prompt: $AiPromptOut" }
if ($AIAnalyze -and -not $GeminiSkippedForCollection) {
  Write-Host "Gemini output JSON: $AiOutputJsonOut"
  Write-Host "Gemini output Markdown: $AiOutputMarkdownOut"
  Write-Host "Gemini compact input Markdown: $AiInputCompactMarkdownOut"
  Write-Host "Gemini summary JSON: $AiOutputSummaryJsonOut"
  Write-Host "Gemini summary Markdown: $AiOutputSummaryMarkdownOut"
  Write-Host "Gemini errors JSON: $AiErrorsOut"
}
Write-Host "Normalized JSON: $NormalizedOut"
Write-Host "Raw XHS JSON: $RawOut"
if (Test-Path $DetailAuditJsonOut) {
  Write-Host "Detail open audit JSON: $DetailAuditJsonOut"
}
if (Test-Path $DetailAuditMarkdownOut) {
  Write-Host "Detail open audit Markdown: $DetailAuditMarkdownOut"
}
if ($Diagnose -and (Test-Path $DiagnosticsDir)) {
  Write-Host "Diagnostics: $DiagnosticsDir"
}
Write-Host "Records: $($raw.records.Count)"
Write-Host "Observations: $($report.observations.Count)"
Write-Host "Sort: $($report.meta.sort)"
Write-Host "Skipped existing: $($report.meta.skipped_existing_count)"
Write-Host "Detail records: $($report.meta.detail_record_count)"
Write-Host "API detail fallback records: $($report.meta.api_detail_record_count)"
Write-Host "Detail/comment evidence records: $($report.meta.evidence_record_count)"
Write-Host "Records with comments: $($report.meta.comment_record_count)"
Write-Host "Comments captured: $($report.meta.total_comment_count)"
Write-Host "Quality status: $($audit.status)"
Write-Host "Warnings: $($report.meta.warning_count)"
if ($report.meta.quality_warning) {
  Write-Host "Quality warning: $($report.meta.quality_warning)" -ForegroundColor Yellow
}
if (-not $NoOpen) {
  Write-Host "Opening HTML report..."
  try {
    Start-Process -FilePath $HtmlOut | Out-Null
  } catch {
    Invoke-Item -LiteralPath $HtmlOut
  }
} else {
  Write-Host "Open the HTML file in a browser to read the report."
}
