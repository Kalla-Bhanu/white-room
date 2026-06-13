param(
    [int]$Port = 8765,
    [switch]$ResetDemo
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

if ($ResetDemo) {
    python scripts/bootstrap_demo.py --reset
} else {
    python scripts/bootstrap_demo.py
}

python -m cli.main serve --port $Port
