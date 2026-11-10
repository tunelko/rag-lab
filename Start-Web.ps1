[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status')]
    [string]$Action = 'start',
    [ValidateRange(1024, 65535)]
    [int]$Port
)
$ErrorActionPreference = 'Stop'
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run Configurar.ps1 first to install the environment.' }
$env:PYTHONUTF8 = '1'
$controllerArguments = @((Join-Path $PSScriptRoot 'server_control.py'), $Action)
if ($PSBoundParameters.ContainsKey('Port')) { $controllerArguments += @('--port', "$Port") }
& $pythonExe @controllerArguments
exit $LASTEXITCODE
