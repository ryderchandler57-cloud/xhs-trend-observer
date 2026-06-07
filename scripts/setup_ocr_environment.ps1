param(
  [switch]$CheckOnly,
  [switch]$Install
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Write-Step([string]$Message) {
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-ProjectPython {
  $preferred = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
  if (Test-Path $preferred) {
    return (Resolve-Path $preferred).Path
  }
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  throw "Python not found. Install Python or set up the Codex primary runtime first."
}

function Quote-ProcessArg([string]$Arg) {
  if ($Arg -match '[\s"]') {
    return '"' + $Arg.Replace('"', '\"') + '"'
  }
  return $Arg
}

function Invoke-ProcessText([string]$ExePath, [string[]]$ArgsList) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $ExePath
  $psi.Arguments = (($ArgsList | ForEach-Object { Quote-ProcessArg $_ }) -join " ")
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $psi
  [void]$process.Start()
  $stdout = $process.StandardOutput.ReadToEnd()
  $stderr = $process.StandardError.ReadToEnd()
  $process.WaitForExit()
  return [pscustomobject]@{
    ExitCode = $process.ExitCode
    Text = (($stdout, $stderr) -join "`n").Trim()
  }
}

function Invoke-PythonText([string[]]$ArgsList) {
  $result = Invoke-ProcessText $Python $ArgsList
  return [pscustomobject]@{
    ExitCode = $result.ExitCode
    Text = $result.Text
  }
}

function Test-PythonImport([string]$ModuleName, [string]$Snippet) {
  $result = Invoke-PythonText @("-c", $Snippet)
  return [pscustomobject]@{
    Module = $ModuleName
    Available = ($result.ExitCode -eq 0)
    Output = $result.Text
  }
}

function Classify-InstallFailure([string]$Text) {
  $lower = $Text.ToLowerInvariant()
  if ($lower -match "no matching distribution|could not find a version|requires-python|unsupported python") {
    return "Python version or platform may be incompatible with the Paddle/PaddleOCR wheels. Check that this is a supported 64-bit Python."
  }
  if ($lower -match "timeout|temporarily unavailable|connection|proxy|ssl|certificate|network") {
    return "Network download failed or the pip index is unreachable. Check network, proxy and certificates, then retry."
  }
  if ($lower -match "403|404|not found|simple index|trusted-host") {
    return "The pip index failed. The Paddle CPU index may be unreachable from this network."
  }
  if ($lower -match "permission|access is denied") {
    return "Permission issue. Use a user-writable virtual environment, or run PowerShell as administrator."
  }
  if ($lower -match "module not found|modulenotfounderror") {
    return "The module still cannot be imported. It may have been installed into the wrong Python. Check the Python path printed above."
  }
  return "Install failed for an unknown reason. Read the pip output above and confirm that this is the project Python."
}

function Run-InstallCommand([string]$Label, [string[]]$ArgsList) {
  Write-Step $Label
  Write-Host "$Python $($ArgsList -join ' ')" -ForegroundColor DarkGray
  $result = Invoke-ProcessText $Python $ArgsList
  $exitCode = $result.ExitCode
  $text = $result.Text
  if ($text) {
    Write-Host $text
  }
  if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "Install failed: $Label" -ForegroundColor Red
    Write-Host (Classify-InstallFailure $text) -ForegroundColor Yellow
    exit $exitCode
  }
}

if ($CheckOnly -and $Install) {
  throw "Please choose only one mode: -CheckOnly or -Install."
}
if (-not $CheckOnly -and -not $Install) {
  $CheckOnly = $true
}

$Python = Find-ProjectPython
$InstallCommands = @(
  "$Python -m pip install --upgrade pip",
  "$Python -m pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/",
  "$Python -m pip install paddleocr"
)

Write-Step "Python environment"
Write-Host "Python path: $Python"
$version = Invoke-PythonText @("--version")
Write-Host "Python version: $($version.Text)"

$versionInfo = Invoke-PythonText @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
if ($versionInfo.ExitCode -eq 0) {
  $parts = $versionInfo.Text.Split(".")
  if ($parts.Count -ge 2) {
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    if ($major -ne 3 -or $minor -lt 9) {
      Write-Host "Warning: PaddleOCR usually needs a modern 64-bit Python 3.x environment. Current version may be incompatible." -ForegroundColor Yellow
    }
  }
}

Write-Step "Current OCR package status"
$paddle = Test-PythonImport "paddle" "import paddle; print(paddle.__version__)"
$paddleocr = Test-PythonImport "paddleocr" "import paddleocr; print('paddleocr ok')"

Write-Host "paddle importable: $($paddle.Available)"
if ($paddle.Output) { Write-Host "paddle output: $($paddle.Output)" }
Write-Host "paddleocr importable: $($paddleocr.Available)"
if ($paddleocr.Output) { Write-Host "paddleocr output: $($paddleocr.Output)" }

Write-Step "Install commands"
foreach ($cmd in $InstallCommands) {
  Write-Host $cmd
}

if ($CheckOnly) {
  if (-not $paddle.Available -or -not $paddleocr.Available) {
    Write-Host ""
    Write-Host "OCR packages are not ready. Re-run with -Install to install them into the Python shown above." -ForegroundColor Yellow
  } else {
    Write-Host ""
    Write-Host "OCR packages are ready in the project Python environment." -ForegroundColor Green
  }
  exit 0
}

Write-Step "Install OCR packages"
Run-InstallCommand "Upgrade pip" @("-m", "pip", "install", "--upgrade", "pip")
Run-InstallCommand "Install PaddlePaddle CPU 3.2.0" @("-m", "pip", "install", "paddlepaddle==3.2.0", "-i", "https://www.paddlepaddle.org.cn/packages/stable/cpu/")
Run-InstallCommand "Install PaddleOCR" @("-m", "pip", "install", "paddleocr")

Write-Step "Verify after install"
$paddleAfter = Test-PythonImport "paddle" "import paddle; print(paddle.__version__)"
$paddleocrAfter = Test-PythonImport "paddleocr" "import paddleocr; print('paddleocr ok')"

Write-Host "paddle importable: $($paddleAfter.Available)"
if ($paddleAfter.Output) { Write-Host "paddle output: $($paddleAfter.Output)" }
Write-Host "paddleocr importable: $($paddleocrAfter.Available)"
if ($paddleocrAfter.Output) { Write-Host "paddleocr output: $($paddleocrAfter.Output)" }

if (-not $paddleAfter.Available -or -not $paddleocrAfter.Available) {
  Write-Host ""
  Write-Host "Install finished, but import verification failed." -ForegroundColor Red
  Write-Host (Classify-InstallFailure (($paddleAfter.Output, $paddleocrAfter.Output) -join "`n")) -ForegroundColor Yellow
  exit 1
}

Write-Host ""
Write-Host "PaddleOCR is ready. You can re-run ocr_note_media.py on an existing media-manifest.json." -ForegroundColor Green
