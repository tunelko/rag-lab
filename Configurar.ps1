[CmdletBinding()]
param(
    [string]$Python = '',
    [switch]$Check,
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$entryPoint = Join-Path $PSScriptRoot 'configure_lab.py'
if ($Python) {
    $pythonCommand = $Python
    $pythonArguments = @()
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCommand = 'py'
    $pythonArguments = @('-3.13')
} else {
    $pythonCommand = 'python'
    $pythonArguments = @()
}
$wizardArguments = @('--bootstrap')
if ($Check) { $wizardArguments = @('--check') }
if ($Start) { $wizardArguments = @('--start') }
& $pythonCommand @pythonArguments $entryPoint @wizardArguments
if ($LASTEXITCODE -ne 0) {
    throw 'El asistente no ha terminado correctamente. Revisa el mensaje anterior. Se necesita Python 3.13 x64.'
}
