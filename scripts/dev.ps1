<#
.SYNOPSIS
  One entry point for the day-to-day jobs, so each is done the same way every time.

.EXAMPLE
  .\scripts\dev.ps1 frontend        # check + build + restart the UI (after any frontend change)
  .\scripts\dev.ps1 backend         # restart the API (after any backend change)
  .\scripts\dev.ps1 check           # frontend checks + backend tests, deploying nothing
  .\scripts\dev.ps1 test tests/test_holdings_planning.py   # some backend tests
  .\scripts\dev.ps1 backup          # save a dated copy of the holdings ledger
  .\scripts\dev.ps1 db-role         # apply the passwords in .env (after a change, or to rotate)

.DESCRIPTION
  frontend       docker compose up --build. The image cannot build unless the type-check,
                 lint and unit tests pass (see frontend/Dockerfile), so this is also the
                 test run. Waits until the new container answers, then prunes this
                 project's superseded images.
  backend        Restarts the API container (the code is bind-mounted; nothing to build).
                 Refuses while a sync job is running -- a restart kills it mid-write --
                 unless -Force. Waits for /api/health.
  backend-image  Rebuilds the API image. Only needed when backend/requirements.txt changes.
                 Same sync guard as `backend`.
  check          Frontend checks (the Dockerfile's `check` stage, cached) and the backend
                 pytest suite. Nothing is deployed.
  test           The backend pytest suite, or the paths/arguments given after it.
  backup         Saves the holdings ledger (every portfolio, transaction, SIP, goal and alert
                 rule) to Documents\MyFinonce-backups\holdings-<date>.json -- outside the repo,
                 and outside the database volume a `docker compose down -v` would delete. The
                 Holdings page's Download backup button saves the same file.
  db-role        Applies .env to the database: creates or updates the backend's login role
                 (APP_DB_USER, not a superuser) and its password, the superuser's password,
                 and hands the app role its tables (scripts/postgres-init/10-app-role.sh).
                 Recreates the postgres and backend containers so they read the new values;
                 the data volume is kept. Same sync guard as `backend`.
#>
param(
  [Parameter(Position = 0, Mandatory = $true)]
  [ValidateSet("frontend", "backend", "backend-image", "check", "test", "backup", "db-role")]
  [string]$Command,
  [switch]$Force,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Rest
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n> $text" -ForegroundColor Cyan }
function Done($text, $sw) { Write-Host ("  {0} in {1:N1}s" -f $text, $sw.Elapsed.TotalSeconds) -ForegroundColor Green }
function Invoke-Native([scriptblock]$block, [string]$what) {
  & $block
  if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

function Assert-NoSyncRunning {
  if ($Force) { return }
  try {
    $job = (Invoke-RestMethod -TimeoutSec 5 http://127.0.0.1:8000/api/admin/status).sync_job
  } catch {
    # A 4xx is the backend answering, but refusing the question (e.g. a misconfigured
    # request guard, backend/app/core/request_guard.py): it is up and may be mid-sync.
    $resp = $_.Exception.Response
    if ($resp -and [int]$resp.StatusCode -ge 400 -and [int]$resp.StatusCode -lt 500) {
      throw "The backend answered HTTP $([int]$resp.StatusCode) to /api/admin/status, so dev.ps1 cannot confirm no sync is running. Fix that first, or pass -Force if you are sure none is."
    }
    return   # no answer, a timeout or a 5xx: nothing to interrupt
  }
  if ($job.is_running) {
    throw "A '$($job.kind)' sync is running (step: $($job.step)). Restarting now would kill it mid-write. Wait for it, or pass -Force."
  }
}

function Wait-Until([scriptblock]$ready, [string]$what, [int]$timeoutSec = 120) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while (-not (& $ready)) {
    if ($sw.Elapsed.TotalSeconds -gt $timeoutSec) { throw "$what did not come up within ${timeoutSec}s -- see: docker compose logs $what" }
    Start-Sleep -Milliseconds 500
  }
}

function Test-BackendHealthy {
  try { (Invoke-RestMethod -TimeoutSec 3 http://127.0.0.1:8000/api/health).status -eq "ok" } catch { $false }
}

function Remove-StaleImages {
  # Each build leaves the image it replaced dangling; only this project's are removed.
  docker image prune -f --filter "label=org.myfinonce.app" | Out-Null
}

function Invoke-BackendTests([string[]]$targets) {
  $pytestArgs = if ($targets) { $targets -join " " } else { "tests/" }
  Invoke-Native { docker exec mf_backend sh -c "cd /app && python -m pytest $pytestArgs -q -p no:warnings" } "Backend tests"
}

$total = [Diagnostics.Stopwatch]::StartNew()
switch ($Command) {
  "frontend" {
    Step "Checking and building the frontend (type-check, lint and tests run inside the build)"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Invoke-Native { docker compose up -d --build --no-deps frontend } "Frontend build"
    Done "Built" $sw
    Step "Waiting for the new container"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Wait-Until { (docker inspect -f "{{.State.Health.Status}}" mf_frontend) -eq "healthy" } "frontend"
    Done "Serving on http://localhost:3000" $sw
    Remove-StaleImages
  }
  "backend" {
    Assert-NoSyncRunning
    Step "Restarting the backend"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Invoke-Native { docker restart mf_backend | Out-Null } "Backend restart"
    Wait-Until { Test-BackendHealthy } "backend"
    Done "API healthy on http://localhost:8000" $sw
  }
  "backend-image" {
    Assert-NoSyncRunning
    Step "Rebuilding the backend image"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Invoke-Native { docker compose up -d --build --no-deps backend } "Backend build"
    Wait-Until { Test-BackendHealthy } "backend"
    Done "API healthy on http://localhost:8000" $sw
    Remove-StaleImages
  }
  "backup" {
    Step "Saving the holdings ledger"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $dir = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "MyFinonce-backups"
    New-Item -ItemType Directory -Force $dir | Out-Null
    $file = Join-Path $dir ("holdings-{0}.json" -f (Get-Date -Format "yyyy-MM-dd_HHmmss"))
    Invoke-WebRequest -TimeoutSec 60 -UseBasicParsing http://127.0.0.1:8000/api/holdings/backup.json -OutFile $file
    Done "Saved $file" $sw
  }
  "db-role" {
    if (-not (Test-Path (Join-Path $root ".env"))) { throw "No .env beside docker-compose.yml. Copy .env.example to .env and fill it in first." }
    Assert-NoSyncRunning
    $sw = [Diagnostics.Stopwatch]::StartNew()
    # Stopped first, so nothing writes while Postgres restarts and the passwords change.
    Step "Stopping the backend"
    Invoke-Native { docker compose stop backend } "Backend stop"
    Step "Recreating Postgres with the values in .env (the data volume is kept)"
    Invoke-Native { docker compose up -d --wait postgres } "Postgres start"
    Step "Applying the database role"
    Invoke-Native { docker exec mf_postgres sh /docker-entrypoint-initdb.d/10-app-role.sh } "Role script"
    Step "Recreating the backend so it logs in with the new values"
    Invoke-Native { docker compose up -d --no-deps backend } "Backend start"
    Wait-Until { Test-BackendHealthy } "backend"
    Done "API healthy on http://localhost:8000" $sw
  }
  "check" {
    Step "Frontend: type-check, lint, unit tests"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Invoke-Native { docker build --target check frontend } "Frontend checks"
    Done "Frontend checks passed" $sw
    Step "Backend: pytest"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Invoke-BackendTests $Rest
    Done "Backend tests passed" $sw
  }
  "test" {
    Step "Backend: pytest $($Rest -join ' ')"
    Invoke-BackendTests $Rest
  }
}
Write-Host ("`nDone in {0:N1}s" -f $total.Elapsed.TotalSeconds) -ForegroundColor Green
