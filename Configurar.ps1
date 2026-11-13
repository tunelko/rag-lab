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
    # Same order as the wizard: an explicit variable, the remembered choice, then the session's language.
    $languageFile = Join-Path $PSScriptRoot '.cache/setup-language'
    if ($env:RAG_SETUP_LANGUAGE) {
        $language = $env:RAG_SETUP_LANGUAGE
    } elseif (Test-Path -LiteralPath $languageFile) {
        $language = (Get-Content -LiteralPath $languageFile -TotalCount 1).Trim()
    } else {
        $language = (Get-UICulture).TwoLetterISOLanguageName
    }
    if ($language -like 'es*') {
        throw 'El asistente no ha terminado correctamente. Revisa el mensaje anterior. Se necesita Python 3.13 x64.'
    }
    throw 'The wizard did not finish correctly. Check the message above. Python 3.13 x64 is required.'
}
