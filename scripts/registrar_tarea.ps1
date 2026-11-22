# Registra (o reemplaza) la tarea programada de Windows que ejecuta el ETL cada hora.
#   Registrar:  powershell -ExecutionPolicy Bypass -File scripts\registrar_tarea.ps1
#   Quitar:     Unregister-ScheduledTask -TaskName 'ContentFlow-ETL' -Confirm:$false
param([int]$CadaHoras = 1)

$script = Join-Path $PSScriptRoot 'actualizar.ps1'
$accion = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$disparador = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours((Get-Date).Hour + 1) `
    -RepetitionInterval (New-TimeSpan -Hours $CadaHoras)
$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName 'ContentFlow-ETL' -Description 'ETL ContentFlow: Cosmos DB -> CSV para Power BI' `
    -Action $accion -Trigger $disparador -Settings $config -Force |
    Select-Object TaskName, State
