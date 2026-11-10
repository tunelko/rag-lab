[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Action = 'start',
    [ValidateRange(1024, 65535)]
    [int]$Port,
    [Alias('h')]
    [switch]$Help,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Restart,
    [switch]$Status,
    [Alias('when-idle')]
    [switch]$WhenIdle
)
$ErrorActionPreference = 'Stop'
$Action = $Action.TrimStart('-').ToLowerInvariant()
if ($Help -or $Action -eq 'help') {
    Write-Output @'
Uso: Start-Web.ps1 [start|stop|restart|status] [-Port 8765]
     Start-Web.ps1 [--start|--stop|--restart|--status]
     Start-Web.ps1 [-Start|-Stop|-Restart|-Status]
     Start-Web.ps1 -h   (tambien --help o -Help)

Sin accion: start. Arranca en segundo plano; no necesitas mantener la consola abierta.
Lee RAG_WEB_PORT de .env (8765 por defecto). -Port permite elegir otro puerto.
Registros: reports/rag-server-runtime.log y reports/rag-server-runtime-errors.log
Stop/restart requiere que no haya tareas activas. Solo gestiona el servidor RAG.
Con --when-idle (o -WhenIdle), stop/restart espera a que termine la cola.
'@
    exit 0
}
$selectedActions = @(@('start', 'stop', 'restart', 'status') | Where-Object { $PSBoundParameters.ContainsKey($_) })
if (@($selectedActions).Count -gt 1 -or (@($selectedActions).Count -eq 1 -and $PSBoundParameters.ContainsKey('Action'))) {
    throw 'Indica una sola accion. Consulta Start-Web.ps1 -h.'
}
if (@($selectedActions).Count -eq 1) { $Action = $selectedActions[0] }
if ($Action -notin @('start', 'stop', 'restart', 'status')) {
    throw "Accion desconocida: $Action. Consulta Start-Web.ps1 -h."
}
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run Configurar.ps1 first to install the environment.' }
$env:PYTHONUTF8 = '1'
$controllerArguments = @((Join-Path $PSScriptRoot 'server_control.py'), $Action)
if ($PSBoundParameters.ContainsKey('Port')) { $controllerArguments += @('--port', "$Port") }
if ($WhenIdle) { $controllerArguments += '--when-idle' }
& $pythonExe @controllerArguments
exit $LASTEXITCODE
