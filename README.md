# myFinonce

A self-hosted dashboard for Indian mutual funds, built on official AMFI data. Screen and compare funds, backtest SIPs, analyse risk, and track your own portfolios. It also covers live NSE IPOs and everyday financial calculators.

> Analysis software, not investment advice. myFinonce is not a SEBI-registered adviser.

## What's inside

The left menu is grouped the same way as this list.

### Calculator
| Route | What it does |
|---|---|
| `/calculator` | **Calculator** — order of operations, brackets, powers and roots, phone-style percent, memory, history, and quick percentages and GST.<br>**EMI** — monthly instalment, total interest and the full schedule by calendar or financial year, with CSV export. Prepayments can be monthly, yearly, a yearly EMI step-up or one-time, and after each one you choose to reduce the tenure or the EMI. Charts show interest saved and the loan balance with and without prepayments. |

### Stock Market
| Route | What it does |
|---|---|
| `/nse-ipo` | **NSE IPO** — current and upcoming public issues, live from nseindia.com. Each issue has full issue information, bid details (NSE only and all exchanges), demand graph and demand data. A "take a chance?" read of bidding demand covers subscription by investor category, issue size, fresh issue vs offer for sale, minimum application and timing. **Nothing from NSE is stored** anywhere. |

### Mutual Funds
| Route | What it does |
|---|---|
| `/` | **Overview** — market pulse by asset class, fund houses scored against their peers, category matrix, alpha leaders and laggards, a risk-reward quadrant, category rotation, and an **All Funds** table of every scheme. |
| `/holdings` | **Holdings** — your own portfolios, entered transaction by transaction. Shows value, total gain and how long the money behind it has been invested, return since start, XIRR, recent windows against peers, allocation, risk, SIPs and goals. |
| `/screener` | **Scheme Screener** — filter and rank the whole scheme universe, with CSV export. |
| `/compare` | **Compare & Simulate** — funds side by side, plus SIP and lumpsum backtests (XIRR, CAGR, Sharpe, max drawdown). |
| `/quant` | **Quantitative MF Analysis** — factor attribution, stress tests, fee drag, tail risk, rolling returns, Monte Carlo. |
| `/admin` | **Data Management** — NAV and TER sync, historical backfill, live logs. |
| `/scheme/[code]` | Single-fund page, reached from search and tables. |

The date range and the plan (Direct/Regular) and option (Growth/IDCW) filters are shared across the mutual-fund pages and kept in the URL.

## Data sources

- **AMFI** — daily NAVs and full NAV history (~27M rows since 2008), Total Expense Ratios as AMFI publishes them (four decimals), and fund-performance data (AUM, official benchmark returns, riskometer).
- **NSE** — public-issue data, fetched live when you open the NSE IPO page and never saved.
- **You** — Holdings transactions, stored only in your local database.

## Privacy

Everything runs on your machine. The backend and frontend listen on `127.0.0.1` only, because the Holdings ledger is personal financial data and there is no login. The database lives in a local Docker volume. Calculator inputs and NSE data stay in memory in your browser tab.

## Stack

- **PostgreSQL 16**, in the Docker volume `indian-mutual-funds_pgdata`
- **FastAPI** backend at `http://localhost:8000`
- **Next.js 14** frontend at `http://localhost:3000`
- **Docker Compose**, with the project name pinned to `indian-mutual-funds` so the volume stays attached if the folder moves

## Run (Windows)

Docker Desktop must be running.

```powershell
.\start.ps1
# or
docker compose up -d
```

After frontend changes, rebuild the frontend image (its source is baked in):

```powershell
docker compose up -d --build frontend
```

After backend changes, restart the backend (its source is mounted, without auto-reload):

```powershell
docker restart mf_backend
```

**First run:** a fresh clone starts with an empty database. Open **Data Management** (`/admin`) and run the NAV and TER sync, then the historical backfill. Admin actions need the `X-Admin-Token` header; the local default is in `docker-compose.yml`.

> `docker compose down -v` deletes the database volume, including your Holdings ledger. Back it up first from the Holdings page.

## Tests

```powershell
docker exec mf_backend python -m pytest tests/ -q
docker exec mf_frontend npm test
```

## Layout

```
backend/app/         FastAPI app: routers/, services/, db/ (SQL), AMFI and NSE clients
backend/tests/       pytest suite (runs against a separate test database)
frontend/app/        Next.js pages, one folder per route
frontend/components/ page components (holdings/, calculator/, nse-ipo/, overview/, shared/ ...)
frontend/lib/        API clients and pure logic, with vitest tests beside them
docs/                design notes
```
