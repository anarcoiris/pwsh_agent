# Script de gestion del Daemon de repo-hygiene
# Uso:
#   .\scripts\manage_daemon.ps1 -Start
#   .\scripts\manage_daemon.ps1 -Stop
#   .\scripts\manage_daemon.ps1 -Status
#   .\scripts\manage_daemon.ps1 -Enqueue -RepoPath "C:\Ruta\Al\Repo" [-TaskType "ai_review"|"static"] [-TargetFile "ruta/relativa.py"] [-AddedBy "human"|"inference"]

param(
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [switch]$Enqueue,
    [string]$RepoPath,
    [string]$TaskType = "ai_review",
    [string]$TargetFile = $null,
    [string]$AddedBy = "human"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$ReportsDir = Join-Path $Root ".reports"
$PidFile = Join-Path $ReportsDir "daemon.pid"
$QueueFile = Join-Path $ReportsDir "review_queue.json"
$LogFile = Join-Path $ReportsDir "daemon.log"

function Get-DaemonProcess {
    if (Test-Path $PidFile) {
        $pidVal = Get-Content $PidFile -Raw
        if ($pidVal -match "^\d+$") {
            $proc = Get-Process -Id ([int]$pidVal) -ErrorAction SilentlyContinue
            if ($proc -and $proc.ProcessName -like "*python*") {
                return $proc
            }
        }
    }
    return $null
}

if ($Start) {
    $proc = Get-DaemonProcess
    if ($proc) {
        Write-Host "El Daemon ya esta ejecutandose con PID $($proc.Id)." -ForegroundColor Yellow
        return
    }
    
    Write-Host "Arrancando Daemon de repo-hygiene en segundo plano..." -ForegroundColor Cyan
    # Arrancar python en segundo plano de manera oculta
    $processInfo = Start-Process python -ArgumentList "scripts/hygiene_daemon.py" -WindowStyle Hidden -PassThru -WorkingDirectory $Root
    
    # Esperar un momento a que se escriba el PID
    Start-Sleep -Seconds 2
    
    $proc = Get-DaemonProcess
    if ($proc) {
        Write-Host "Daemon iniciado con exito (PID: $($proc.Id))." -ForegroundColor Green
    } else {
        Write-Host "Error al iniciar el Daemon. Revisa los logs en .reports/daemon.log." -ForegroundColor Red
    }
}
elseif ($Stop) {
    $proc = Get-DaemonProcess
    if (-not $proc) {
        Write-Host "El Daemon no esta ejecutandose." -ForegroundColor Yellow
        # Limpiar pid huerfano si existe
        if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
        return
    }
    
    Write-Host "Deteniendo Daemon (PID: $($proc.Id))..." -ForegroundColor Cyan
    Stop-Process -Id $proc.Id -Force
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
    Write-Host "Daemon detenido correctamente." -ForegroundColor Green
}
elseif ($Status) {
    $proc = Get-DaemonProcess
    if ($proc) {
        Write-Host "Estado: EJECUTANDOSE (PID: $($proc.Id))" -ForegroundColor Green
    } else {
        Write-Host "Estado: DETENIDO" -ForegroundColor Red
    }
    
    Write-Host "`n--- Informacion de Inactividad de Prueba ---" -ForegroundColor Cyan
    python scripts/hygiene_daemon.py --test-idle
    
    if (Test-Path $LogFile) {
        Write-Host "`n--- Ultimas 10 lineas de log ---" -ForegroundColor Cyan
        Get-Content $LogFile -Tail 10
    }
    
    if (Test-Path $QueueFile) {
        $q = Get-Content $QueueFile | ConvertFrom-Json
        $pendingCount = $q.pending.Count
        $completedCount = $q.completed.Count
        Write-Host "`nTareas en cola: $pendingCount pendientes | $completedCount completadas" -ForegroundColor Yellow
        if ($pendingCount -gt 0) {
            Write-Host "Proximas tareas pendientes:" -ForegroundColor Gray
            $q.pending | ForEach-Object { Write-Host "  - [$($_.task_type)] $($_.repo_path) (por: $($_.added_by))" -ForegroundColor Gray }
        }
    }
}
elseif ($Enqueue) {
    if (-not $RepoPath) {
        Write-Host "Error: Debes especificar -RepoPath al encolar una tarea." -ForegroundColor Red
        return
    }
    
    $absPath = Resolve-Path $RepoPath -ErrorAction SilentlyContinue
    if (-not $absPath) {
        # Si no se resuelve en disco, intentar ruta relativa al root
        $absPath = Join-Path $Root $RepoPath
    }
    
    if (-not (Test-Path $absPath)) {
        Write-Host "Error: No se encontro el directorio del repositorio en $absPath" -ForegroundColor Red
        return
    }
    
    $absPathStr = $absPath.Path
    
    # Inicializar cola si no existe
    if (-not (Test-Path $QueueFile)) {
        New-Item -ItemType File -Path $QueueFile -Force | Out-Null
        Set-Content $QueueFile '{"pending":[],"completed":[]}'
    }
    
    # Cargar y actualizar JSON de cola
    $q = Get-Content $QueueFile -Raw | ConvertFrom-Json
    
    # Evitar duplicados pendientes del mismo tipo y ruta
    $duplicate = $q.pending | Where-Object { $_.repo_path -eq $absPathStr -and $_.task_type -eq $TaskType }
    if ($duplicate) {
        Write-Host "La tarea [$TaskType] para $absPathStr ya esta pendiente en la cola." -ForegroundColor Yellow
        return
    }
    
    $newTask = @{
        repo_path   = $absPathStr
        task_type   = $TaskType
        target_file = $TargetFile
        added_by    = $AddedBy
        timestamp   = (Get-Date -Format "o")
        status      = "pending"
    }
    
    $q.pending += $newTask
    $qJson = $q | ConvertTo-Json -Depth 5
    Set-Content $QueueFile $qJson -Encoding utf8
    
    Write-Host "[OK] Tarea encolada con exito: [$TaskType] en $absPathStr (Agregada por: $AddedBy)" -ForegroundColor Green
}
else {
    Write-Host "Uso: .\scripts\manage_daemon.ps1 [-Start] [-Stop] [-Status] [-Enqueue -RepoPath <path>]" -ForegroundColor Yellow
}
