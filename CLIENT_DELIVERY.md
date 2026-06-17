# Client Delivery Notes

This repository is prepared for client handoff.

## What To Send

Use the generated versioned ZIP from:

```powershell
.\jobs\create_client_delivery.ps1
```

That package is the shareable delivery artifact. Do not send the live working folder directly.
The archive root folder and filename carry the client release version from [`CLIENT_RELEASE_VERSION.txt`](CLIENT_RELEASE_VERSION.txt).
The delivery package now excludes local scraped/debug data folders by default.

Current data-quality note:

- Daraz source ratings now flow end to end from scraper to normalized catalog data.
- Shophive can still fall back to predicted display ratings because current live pages do not expose stable rating data in the active scrape path.

## What The Delivery Package Includes

- backend code
- frontend code
- tests
- Docker files
- README and handoff docs
- trained artifacts by default
- no local scraped database dump

## What The Delivery Package Excludes

- local `.env`
- `react UI/node_modules`
- `react UI/dist`
- `.pytest_cache`
- `__pycache__`
- `.venv`
- `delivery`
- `data`
- local checkpoints
- local scraper debug output

## Secrets Handling

The delivery package includes `.env.example`, not your real `.env`.

Client must create `.env` from `.env.example` and fill:

- `GROQ_API_KEY`
- `SERVICE_API_KEY`
- `ADMIN_API_KEY`
- optional `JWT_SECRET`

Do not ship your active local secrets.

## Recommended Client Run Paths

For a full step-by-step setup from scratch, use [`HOW_TO_RUN.txt`](HOW_TO_RUN.txt).

### Option 1: Docker

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

Open:

- Web UI: `http://localhost:3000`
- API: `http://localhost:8000`
- API Docs: `http://localhost:8000/docs`

### Option 2: Local Development

Prerequisites:

- Python installed
- Node.js installed
- MongoDB running locally

```powershell
Copy-Item .env.example .env
$env:PYTHONPATH = (Get-Location).Path
python run_all.py
```

Open:

- UI: `http://127.0.0.1:5173`
- API: `http://127.0.0.1:8000`

## Post-Delivery Smoke Test

After filling `.env` and starting the API:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python jobs\smoke_assistant_api.py --base-url http://127.0.0.1:8000 --service-api-key <SERVICE_API_KEY> --admin-api-key <ADMIN_API_KEY>
```

## Notes On Models And Data

- The package includes `artifacts` by default because they are part of the working runtime.
- Scraped Mongo data is not embedded in code delivery. Client needs their own Mongo instance and data load path.
- Local `data` and scraper debug folders are excluded from the package.
- If client wants a smaller package without artifacts, generate it with:

```powershell
.\jobs\create_client_delivery.ps1 -SkipArtifacts
```
