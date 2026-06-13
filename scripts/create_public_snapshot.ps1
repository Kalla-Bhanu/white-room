param(
    [string]$OutputPath = ".\dist\white-room-public"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$destination = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)

if (Test-Path -LiteralPath $destination) {
    Remove-Item -LiteralPath $destination -Recurse -Force
}

New-Item -ItemType Directory -Path $destination | Out-Null

$excludeDirs = @(
    ".git",
    ".deps",
    ".pytest_cache",
    ".venv",
    "data",
    "dist",
    "__pycache__"
)

$excludeFiles = @(
    ".env",
    "secrets.local.json",
    "secrets.enc"
)

$excludeDocs = @(
    "WHITE_ROOM_MASTER_PLAN.md"
)

$includeRoots = @(
    ".github",
    "adapters",
    "bench",
    "cli",
    "core",
    "docs",
    "scripts",
    "templates",
    "tests",
    "web"
)

foreach ($root in $includeRoots) {
    $source = Join-Path $repoRoot $root
    if (Test-Path -LiteralPath $source) {
        robocopy $source (Join-Path $destination $root) /E /XD $excludeDirs /XF $excludeFiles | Out-Null
        if ($LASTEXITCODE -ge 8) {
            throw "robocopy failed for $root with exit code $LASTEXITCODE"
        }
    }
}

foreach ($doc in $excludeDocs) {
    $docPath = Join-Path (Join-Path $destination "docs") $doc
    if (Test-Path -LiteralPath $docPath) {
        Remove-Item -LiteralPath $docPath -Force
    }
}

$rootFiles = @(
    ".env.example",
    ".gitignore",
    "CONTRIBUTING.md",
    "CITATION.cff",
    "LICENSE",
    "NOTICE.md",
    "README.md",
    "ROADMAP.md",
    "SECURITY.md",
    "pyproject.toml",
    "requirements.txt"
)

foreach ($file in $rootFiles) {
    $source = Join-Path $repoRoot $file
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $destination $file)
    }
}

Write-Host "Public snapshot created at $destination"
Write-Host "Review it before publishing:"
Write-Host "  cd `"$destination`""
Write-Host "  python scripts/verify_release.py"
