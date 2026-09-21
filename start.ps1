# ==============================================================================
# Indian Mutual Funds Dashboard - Windows 11 Launch Script
# ==============================================================================

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "   Indian Mutual Funds AMFI Analytics Dashboard" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Cyan

# Check if Docker is in PATH or locate Docker Desktop installation
$DockerCmd = Get-Command "docker" -ErrorAction SilentlyContinue
if (-not $DockerCmd) {
    $DefaultDockerPath = "C:\Users\$env:USERNAME\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"
    $ProgramFilesDockerPath = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path $DefaultDockerPath) {
        $env:Path += ";$([System.IO.Path]::GetDirectoryName($DefaultDockerPath))"
    } elseif (Test-Path $ProgramFilesDockerPath) {
        $env:Path += ";$([System.IO.Path]::GetDirectoryName($ProgramFilesDockerPath))"
    }
}

# Verify Docker engine is running
Write-Host "`n[1/3] Checking Docker Desktop status..." -ForegroundColor Yellow
try {
    $dockerInfo = docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Docker engine is not running." -ForegroundColor Red
        Write-Host "Please start Docker Desktop from your Windows Start menu, then run this script again." -ForegroundColor Yellow
        exit 1
    }
} catch {
    Write-Host "ERROR: Could not execute 'docker'. Please verify Docker Desktop is installed and running." -ForegroundColor Red
    exit 1
}

Write-Host "Docker engine is running." -ForegroundColor Green

# Build and start the services
Write-Host "`n[2/3] Starting FastAPI backend + Next.js frontend..." -ForegroundColor Yellow
docker compose up -d

if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to start Docker Compose services." -ForegroundColor Red
    exit 1
}

Write-Host "`n[3/3] Waiting for services to initialize..." -ForegroundColor Yellow
Start-Sleep -Seconds 3

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "   DASHBOARD IS UP AND RUNNING!" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "App URL       : http://localhost:3000" -ForegroundColor White
Write-Host "API           : http://localhost:8000 (FastAPI, /api/health)" -ForegroundColor Gray
Write-Host "Database      : PostgreSQL 16 (compose project indian-mutual-funds)" -ForegroundColor Gray
Write-Host "Frontend rebuild after UI changes: docker compose up -d --build frontend" -ForegroundColor Gray
Write-Host "Backend reload: `$env:DEV_RELOAD='true'; docker compose up -d backend" -ForegroundColor Gray
Write-Host "AMFI Schemes  : growing daily via official AMFI sync" -ForegroundColor Gray
Write-Host ""
Write-Host "Helpful Commands:" -ForegroundColor Cyan
Write-Host "  View backend logs    : docker compose logs -f backend" -ForegroundColor Gray
Write-Host "  View frontend logs   : docker compose logs -f frontend" -ForegroundColor Gray
Write-Host "  Stop everything      : docker compose down" -ForegroundColor Gray
Write-Host "========================================================`n" -ForegroundColor Cyan

# Open in browser automatically
Start-Process "http://localhost:3000"
