$ErrorActionPreference = 'Stop'
$labRoot = $PSScriptRoot
$activate = Join-Path $labRoot '.venv\Scripts\Activate.ps1'
if (-not (Test-Path -LiteralPath $activate)) {
    throw 'Run Setup-Lab.ps1 first.'
}
$env:HF_HOME = Join-Path $labRoot '.cache\huggingface'
$env:SENTENCE_TRANSFORMERS_HOME = Join-Path $labRoot '.cache\sentence-transformers'
$env:PIP_CACHE_DIR = Join-Path $labRoot '.cache\pip'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:ANONYMIZED_TELEMETRY = 'False'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $labRoot
. $activate
Write-Host 'RAG lab active. Embeddings default to CPU. GPU inference requires an explicit ask command.' -ForegroundColor Cyan
