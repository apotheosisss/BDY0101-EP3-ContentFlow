# Actualización automática del pipeline ContentFlow
#   1. Ejecuta el ETL (Cosmos DB -> CSV en data/processed)
#   2. Deja un registro en logs/etl_AAAA-MM-DD.log
# Power BI toma los CSV nuevos en su próxima actualización (manual en Desktop,
# o programada en Power BI Service a través de una puerta de enlace).
$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $raiz 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("etl_{0:yyyy-MM-dd}.log" -f (Get-Date))

"[{0:yyyy-MM-dd HH:mm:ss}] Inicio de actualización" -f (Get-Date) | Add-Content $log -Encoding utf8
try {
    & (Join-Path $raiz '.venv\Scripts\python.exe') -W ignore (Join-Path $raiz 'scripts\etl_contentflow.py') *>&1 |
        Add-Content $log -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "El ETL terminó con código $LASTEXITCODE" }
    "[{0:yyyy-MM-dd HH:mm:ss}] OK" -f (Get-Date) | Add-Content $log -Encoding utf8
}
catch {
    "[{0:yyyy-MM-dd HH:mm:ss}] ERROR: $_" -f (Get-Date) | Add-Content $log -Encoding utf8
    exit 1
}
