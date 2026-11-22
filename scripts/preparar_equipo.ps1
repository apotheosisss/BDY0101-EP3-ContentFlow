# Prepara cualquier computador para ejecutar el proyecto ContentFlow.
#
#   Modo completo (Python + conexión a Cosmos DB):
#     powershell -ExecutionPolicy Bypass -File scripts\preparar_equipo.ps1
#
#   Modo sin conexión (usa los CSV que ya vienen en el repositorio; no necesita
#   Python, .env ni acceso a Azure; sirve si la red bloquea el puerto 10255):
#     powershell -ExecutionPolicy Bypass -File scripts\preparar_equipo.ps1 -SinConexion
#
# Pasos del modo completo:
#   1. Crea el entorno virtual e instala las dependencias.
#   2. Verifica la conexión con Cosmos DB.
#   3. Ejecuta el ETL (genera data/processed).
#   4. Apunta el parámetro RutaDatos de Power BI a la carpeta de este equipo.
param([switch]$SinConexion)

$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

function Paso($n, $texto) { Write-Host "`n[$n/4] $texto" -ForegroundColor Cyan }

if ($SinConexion) {
    Write-Host "Modo sin conexión: se usarán los CSV incluidos en data\processed (pasos 1 a 3 omitidos)." -ForegroundColor Yellow
}
else {
    if (-not (Test-Path '.env')) {
        Write-Host "Falta el archivo .env en $raiz" -ForegroundColor Red
        Write-Host "Cópialo en esa carpeta, o usa el modo sin conexión:  -SinConexion"
        exit 1
    }

    Paso 1 'Entorno de Python'
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        $py = (Get-Command python -ErrorAction SilentlyContinue).Source
        if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
        if (-not $py) { throw 'No se encontró Python. Instálalo desde python.org marcando "Add python.exe to PATH".' }
        & $py -m venv .venv
    }
    & .venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias (¿hay internet?)' }

    Paso 2 'Conexión con Azure Cosmos DB'
    & .venv\Scripts\python.exe -W ignore scripts\cargar_datos.py --verificar
    if ($LASTEXITCODE -ne 0) {
        Write-Host "No hay conexión con Cosmos DB. Revisa el .env, o prueba con otra red (la red puede bloquear el puerto 10255)." -ForegroundColor Red
        Write-Host "Para mostrar el tablero igual:  powershell -ExecutionPolicy Bypass -File scripts\preparar_equipo.ps1 -SinConexion"
        exit 1
    }

    Paso 3 'ETL (extracción y transformación)'
    & .venv\Scripts\python.exe -W ignore scripts\etl_contentflow.py
    if ($LASTEXITCODE -ne 0) { throw 'El ETL falló' }
}

Paso 4 'Ruta de datos en Power BI'
if (-not (Test-Path 'data\processed\fact_interacciones.csv')) { throw 'No existen los CSV en data\processed' }
$expr = 'powerbi\ContentFlow.SemanticModel\definition\expressions.tmdl'
$ruta = (Join-Path $raiz 'data\processed') + '\'
$txt = [IO.File]::ReadAllText($expr)
$txt = [regex]::Replace($txt, 'expression RutaDatos = "[^"]*"', ('expression RutaDatos = "' + $ruta.Replace('$', '$$') + '"'))
[IO.File]::WriteAllText($expr, $txt, (New-Object Text.UTF8Encoding $false))
Write-Host "RutaDatos = $ruta"

Write-Host "`nListo. Abre powerbi\ContentFlow.pbip y pulsa Actualizar." -ForegroundColor Green
