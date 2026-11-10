[CmdletBinding()]
param(
    [string]$Python = 'python',
    [switch]$UseLock
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$env:PIP_CACHE_DIR = Join-Path $projectRoot '.cache\pip'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Executable $($Arguments -join ' ')"
    }
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Invoke-Checked -Executable $Python -Arguments @('-m', 'venv', (Join-Path $projectRoot '.venv'))
}

Invoke-Checked -Executable $venvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip')
Invoke-Checked -Executable $venvPython -Arguments @(
    '-m', 'pip', 'install', '--only-binary=:all:',
    '-r', (Join-Path $projectRoot 'requirements-cuda.txt'),
    '--report', (Join-Path $projectRoot 'install-cuda.json')
)
$requirements = Join-Path $projectRoot 'requirements.txt'
if ($UseLock) {
    $requirements = Join-Path $projectRoot 'requirements-lock.txt'
    if (-not (Test-Path -LiteralPath $requirements)) { throw 'requirements-lock.txt does not exist yet.' }
}
Invoke-Checked -Executable $venvPython -Arguments @(
    '-m', 'pip', 'install', '--only-binary=:all:', '-r', $requirements,
    '--report', (Join-Path $projectRoot 'install-rag.json')
)
Invoke-Checked -Executable $venvPython -Arguments @('-m', 'pip', 'check')
Write-Host "Environment ready: $venvPython" -ForegroundColor Green
Write-Host 'No model has been downloaded or run. Enter with: . .\Enter-Lab.ps1'
