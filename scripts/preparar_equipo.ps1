# Prepara cualquier computador para ejecutar el proyecto ContentFlow.
#   powershell -ExecutionPolicy Bypass -File scripts\preparar_equipo.ps1
#
# Requisitos previos: Python 3.10+ y Power BI Desktop instalados, y el archivo .env
# (cadena de conexión de Cosmos DB) copiado en la raíz del proyecto.
#
# Pasos:
#   1. Crea el entorno virtual e instala las dependencias.
#   2. Verifica la conexión con Cosmos DB.
#   3. Ejecuta el ETL (genera data/processed).
#   4. Apunta el parámetro RutaDatos de Power BI a la carpeta de este equipo.
$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

function Paso($n, $texto) { Write-Host "`n[$n/4] $texto" -ForegroundColor Cyan }

if (-not (Test-Path '.env')) {
    Write-Host "Falta el archivo .env en $raiz" -ForegroundColor Red
    Write-Host "Cópialo desde el equipo original o créalo a partir de .env.example."
    exit 1
}

Paso 1 'Entorno de Python'
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $py) { $py = (Get-Command py -ErrorAction Stop).Source }
    & $py -m venv .venv
}
& .venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias' }

Paso 2 'Conexión con Azure Cosmos DB'
& .venv\Scripts\python.exe -W ignore scripts\cargar_datos.py --verificar
if ($LASTEXITCODE -ne 0) { throw 'No hay conexión con Cosmos DB: revisa .env y la red' }

Paso 3 'ETL (extracción y transformación)'
& .venv\Scripts\python.exe -W ignore scripts\etl_contentflow.py
if ($LASTEXITCODE -ne 0) { throw 'El ETL falló' }

Paso 4 'Ruta de datos en Power BI'
$expr = 'powerbi\ContentFlow.SemanticModel\definition\expressions.tmdl'
$ruta = (Join-Path $raiz 'data\processed') + '\'
$txt = [IO.File]::ReadAllText($expr)
$txt = [regex]::Replace($txt, 'expression RutaDatos = "[^"]*"', ('expression RutaDatos = "' + $ruta.Replace('$', '$$') + '"'))
[IO.File]::WriteAllText($expr, $txt, (New-Object Text.UTF8Encoding $false))
Write-Host "RutaDatos = $ruta"

Write-Host "`nListo. Abre powerbi\ContentFlow.pbip y pulsa Actualizar." -ForegroundColor Green
