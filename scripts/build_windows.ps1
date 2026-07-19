[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [string]$InnoCompiler = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$specFile = Join-Path $projectRoot "packaging\review_writer.spec"
$installerScript = Join-Path $projectRoot "packaging\review_writer.iss"
$appExecutable = Join-Path $projectRoot "dist\ReviewWriter\ReviewWriter.exe"
$appVersion = "0.7.0"
$previousDataDir = $env:REVIEW_WRITER_DATA_DIR
$previousProjectsDir = $env:REVIEW_WRITER_PROJECTS_DIR

if ($env:OS -ne "Windows_NT") {
    throw "The Windows build must run on Windows."
}
if (-not (Test-Path $python)) {
    throw "Project virtual environment not found. Run uv sync --dev first."
}

Push-Location $projectRoot
try {
    if (-not $SkipTests) {
        & $python -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) { throw "Tests failed; build stopped." }
    }

    & $python -c "import PyInstaller"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller is missing. Run uv sync --dev." }

    & $python -m PyInstaller --noconfirm --clean $specFile
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $appExecutable)) {
        throw "PyInstaller build failed."
    }

    $smokeData = Join-Path $projectRoot "dist\smoke-data"
    $smokeProjects = Join-Path $projectRoot "dist\smoke-projects"
    $env:REVIEW_WRITER_DATA_DIR = $smokeData
    $env:REVIEW_WRITER_PROJECTS_DIR = $smokeProjects
    & $appExecutable --smoke-test
    if ($LASTEXITCODE -ne 0) { throw "Frozen application smoke test failed." }

    if (-not $SkipInstaller) {
        $compilerCandidates = @(
            $InnoCompiler,
            (Join-Path $projectRoot ".build-tools\Inno Setup 6\ISCC.exe"),
            (Join-Path ${env:LOCALAPPDATA} "Programs\Inno Setup 6\ISCC.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
            (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
        ) | Where-Object { $_ -and (Test-Path $_) }
        $compiler = $compilerCandidates | Select-Object -First 1
        if (-not $compiler) {
            throw "Inno Setup 6 was not found. Use -SkipInstaller for an onedir-only build."
        }
        & $compiler "/DMyAppVersion=$appVersion" $installerScript
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }
    }

    $artifacts = Get-ChildItem (Join-Path $projectRoot "dist\installer") -Filter "*.exe" -ErrorAction SilentlyContinue
    if ($artifacts) {
        $checksumLines = foreach ($artifact in $artifacts) {
            $hash = (Get-FileHash -Algorithm SHA256 $artifact.FullName).Hash.ToLowerInvariant()
            "$hash  $($artifact.Name)"
        }
        $checksumLines | Set-Content -Encoding ascii (Join-Path $projectRoot "dist\installer\SHA256SUMS.txt")
    }
}
finally {
    if ($null -eq $previousDataDir) {
        Remove-Item Env:REVIEW_WRITER_DATA_DIR -ErrorAction SilentlyContinue
    }
    else {
        $env:REVIEW_WRITER_DATA_DIR = $previousDataDir
    }
    if ($null -eq $previousProjectsDir) {
        Remove-Item Env:REVIEW_WRITER_PROJECTS_DIR -ErrorAction SilentlyContinue
    }
    else {
        $env:REVIEW_WRITER_PROJECTS_DIR = $previousProjectsDir
    }
    Pop-Location
}
