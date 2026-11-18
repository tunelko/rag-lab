[CmdletBinding()]
param(
    [string]$Python = '',
    [switch]$Check,
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$entryPoint = Join-Path $projectRoot 'install\configure_lab.py'
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
$interpreter = Get-Command $pythonCommand -ErrorAction SilentlyContinue
if ($interpreter) {
    & $pythonCommand @pythonArguments $entryPoint @wizardArguments
    if ($LASTEXITCODE -eq 0) { return }
}
# Same order as the wizard: an explicit variable, the remembered choice, then the session's language.
$languageFile = Join-Path $projectRoot '.cache/setup-language'
if ($env:RAG_SETUP_LANGUAGE) {
    $language = $env:RAG_SETUP_LANGUAGE
} elseif (Test-Path -LiteralPath $languageFile) {
    $language = Get-Content -LiteralPath $languageFile -TotalCount 1
} else {
    $language = (Get-UICulture).TwoLetterISOLanguageName
}
if ($language -like 'es*') {
    if (-not $interpreter) {
        throw 'No se encuentra Python. Instala CPython 3.13.x x64 con pip y el lanzador py, o indica su ruta con -Python. Comprueba la version con: py -3.13 -V'
    }
    throw 'El asistente no ha terminado correctamente. Revisa el mensaje anterior. Comprueba CPython 3.13.x x64 con: py -3.13 -V'
}
if (-not $interpreter) {
    throw 'Python interpreter not found. Install 64-bit CPython 3.13.x with pip and the py launcher, or pass its path with -Python. Check the version with: py -3.13 -V'
}
throw 'The wizard did not finish correctly. Check the message above. Check 64-bit CPython 3.13.x with: py -3.13 -V'
