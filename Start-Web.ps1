[CmdletBinding()]
param([ValidateRange(1024, 65535)][int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run Setup-Lab.ps1 first.' }
$env:PYTHONUTF8 = '1'
$env:OMP_NUM_THREADS = '4'
Write-Host "Consulta:       http://127.0.0.1:$Port/" -ForegroundColor Green
Write-Host "Biblioteca:     http://127.0.0.1:$Port/admin" -ForegroundColor Green
Write-Host "Configuracion:  http://127.0.0.1:$Port/configuracion" -ForegroundColor Green
Write-Host "Estadisticas:   http://127.0.0.1:$Port/estadisticas" -ForegroundColor Green
Write-Host "Consola:        http://127.0.0.1:$Port/?vista=consola" -ForegroundColor Green
Write-Host 'Ctrl+C para cerrar. No se carga ningun modelo al arrancar.'
& $pythonExe -m uvicorn web_app:app --app-dir $PSScriptRoot --host 127.0.0.1 --port $Port --no-access-log
if ($LASTEXITCODE -ne 0) { throw "El servidor termino con codigo $LASTEXITCODE. Comprueba que el puerto esta libre." }
