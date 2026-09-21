# Indian Mutual Funds Analytics (MyFinonce)

AMFI-sourced mutual fund research dashboard: screen schemes, compare and backtest, attribute factor risk, and build hypothetical portfolios.

This is analysis software, not investment advice.

## Stack

- **Postgres 16** — NAV history (~27M rows) in Docker volume `indian-mutual-funds_pgdata`
- **FastAPI** — `http://localhost:8000`
- **Next.js 14** — `http://localhost:3000`
- **Ingest** — official AMFI daily NAV, historical backfill, and TER sync

Compose project name is pinned to `indian-mutual-funds` so the existing named volume stays attached if the folder is moved.

## Run (Windows)

Docker Desktop must be running.

```powershell
.\start.ps1
```

Or:

```powershell
docker compose up -d
```

After frontend source changes:

```powershell
docker compose up -d --build frontend
```

A fresh clone starts with an empty database. Use **Data Management** at `/admin` to run NAV/TER sync and historical backfill. Admin writes need header `X-Admin-Token` (local default is in `docker-compose.yml`).

## Pages

| Route | What it does |
|---|---|
| `/` | Market overview, leaders/laggards, category rotation |
| `/screener` | Filter and rank the scheme universe |
| `/compare` | Side-by-side funds and SIP/lumpsum backtest |
| `/quant` | Factor attribution, stress, fee drag, tail risk, Monte Carlo |
| `/portfolio` | Questionnaire → rules / MVO / HRP allocation |
| `/scheme/[code]` | Single-scheme dossier |
| `/admin` | Sync, backfill, logs |

Date range, plan type (Direct/Regular), and option type are shared across pages and stored in the URL.

## Tests

```powershell
docker exec mf_backend pytest tests/ -v
docker exec mf_frontend npm test
```
