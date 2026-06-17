# E-Assistant Product Search Platform

Version: `0.2.3`

E-Assistant is a full-stack product search and marketplace platform. It scrapes e-commerce sources, normalizes offers into MongoDB, ranks products, exposes a conversational AI assistant, and adds a buyer/seller marketplace with local reviews, orders, reports, and analytics.

This README is the primary technical handoff document for setup, architecture, runtime flows, operations, and delivery.

## 1. What the System Does

The platform has four main layers:

1. Product ingestion:
   - Scrapes products from supported stores.
   - Normalizes them into MongoDB collections used by search and the store UI.

2. Search and assistant:
   - Supports direct search APIs.
   - Supports a local-first assistant that shows local DB results first and enriches with online results afterward.
   - Supports product-followup conversations, page inspection, live web search, and saved conversation history.

3. Marketplace:
   - Buyers and sellers can create accounts.
   - Sellers can publish products.
   - Buyers can browse the store, leave in-app reviews, and place orders.

4. Reporting and analytics:
   - Seller reports.
   - Saved report history and PDF export.
   - Marketplace analytics and deep-learning prediction outputs for demand, rating, and seasonality.

## 2. Key Features

- Cross-source product scraping
- Gallery image support: `image` plus `images[]`
- Source-site rating and review-count capture
- Daraz source-site ratings now flow through scrape, ingest, and normalize with real values
- In-app ratings and reviews
- Marketplace seller listings mixed with scraped catalog products
- AI assistant with:
  - local-first search
  - background online enrichment
  - follow-up reasoning on selected products
  - comparison/refinement behavior
  - report generation and report history
- Real marketplace orders and seller sales reporting
- PDF report export
- Deep learning analytics for:
  - predicted app rating
  - predicted demand score
  - seasonal month relevance

## 3. Tech Stack

Backend:
- Python 3.12
- FastAPI
- MongoDB
- PyTorch
- sentence-transformers
- Playwright

Frontend:
- React
- TypeScript
- Vite
- Tailwind-style utility CSS

Operational:
- Docker / Docker Compose
- PowerShell helper scripts

## 4. Supported Sources

Current scrapers:

- Daraz: [F:\product search\src\scrapers\daraz\playwright_async.py](F:\product search\src\scrapers\daraz\playwright_async.py)
- Shophive: [F:\product search\src\scrapers\shophive\playwright_async.py](F:\product search\src\scrapers\shophive\playwright_async.py)
- iShopping Playwright: [F:\product search\src\scrapers\ishopping\playwright_sync.py](F:\product search\src\scrapers\ishopping\playwright_sync.py)
- iShopping requests/BS4: [F:\product search\src\scrapers\ishopping\requests_bs4.py](F:\product search\src\scrapers\ishopping\requests_bs4.py)

Current rating-data status:

- Daraz listing-page ratings are captured from rendered star classes and now persist into normalized offers.
- Shophive pages currently do not expose stable rating data in the active scrape path, so Shophive still falls back to predicted display ratings where needed.

## 5. Repository Layout

Top-level areas:

- `src/api/` FastAPI application
- `src/agent/` assistant orchestration and memory
- `src/core/` settings, normalization, DB helpers, auth, marketplace, reporting
- `src/mcp/` in-process tool layer used by the assistant
- `src/ml/` matcher, clustering, CF, and marketplace deep learning
- `src/scrapers/` source-specific scrapers
- `jobs/` operational scripts for scraping, processing, training, packaging, and smoke tests
- `react UI/` React frontend
- `config/` source configuration
- `artifacts/` trained models and generated runtime artifacts
- `tests/` regression coverage

## 6. Important Mongo Collections

Core product/search collections:

- `offers_raw`
- `offers_normalized`
- `canonical_products`
- `price_history`

Assistant collections:

- `chat_sessions`
- `chat_turns`
- `assistant_tool_logs`

Marketplace collections:

- marketplace users
- seller products
- reviews
- orders
- predictions

Reporting collections:

- saved reports

Exact collection names come from [F:\product search\src\core\settings.py](F:\product search\src\core\settings.py).

## 7. Environment Variables

Create `.env` from `.env.example`:

```powershell
Copy-Item .env.example .env
```

Minimum values to set:

```env
GROQ_API_KEY=your_groq_key
SERVICE_API_KEY=your_service_key
ADMIN_API_KEY=your_admin_key
```

Common production-oriented flags:

```env
REQUIRE_AUTH_FOR_WRITE=true
CONVERSATION_REQUIRE_USER_ID=true
RATE_LIMIT_ENABLED=true
INSPECT_PAGE_BLOCK_PRIVATE_NETWORKS=true
```

## 8. Prerequisites

For local development:

- Python 3.12
- Node.js 20+
- MongoDB

For Docker run:

- Docker Desktop

## 9. Local Setup From Scratch

### 9.1 Go to the project root

```powershell
cd "F:\product search"
```

### 9.2 Create virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 9.3 Install Python dependencies

```powershell
pip install -r requirements.txt
```

### 9.4 Install frontend dependencies

```powershell
Set-Location "react UI"
npm install
Set-Location ..
```

### 9.5 Create `.env`

```powershell
Copy-Item .env.example .env
```

Then edit `.env`.

### 9.6 Ensure MongoDB is running

MongoDB must be available before scraping, data processing, or backend startup.

### 9.7 Set `PYTHONPATH`

```powershell
$env:PYTHONPATH = (Get-Location).Path
```

## 10. Product Data Pipeline

This is the normal path to populate the database from scratch.

### 10.1 Initialize indexes

```powershell
python jobs\init_db.py
```

### 10.2 Run one scrape cycle

```powershell
python jobs\run_scrapper.py --once
```

Optional requests-based iShopping path:

```powershell
python jobs\run_scrapper.py --once --include-requests
```

### 10.3 Process scraped data

```powershell
python jobs\run_data_processing.py --with-init-db
```

This processing stage performs:

- source ingest
- offer normalization
- stale cleanup
- reclustering

### 10.4 Optional quality check

```powershell
python jobs\data_quality_report.py --fresh-hours 48
```

## 11. Training and Analytics Jobs

### 11.1 Standard training pipeline

```powershell
python jobs\run_training.py
```

Useful variants:

```powershell
python jobs\run_training.py --prepare-labels --bootstrap-interactions
python jobs\run_training.py --skip-cf
python jobs\run_training.py --skip-matcher
```

### 11.2 Marketplace deep learning training

Train the marketplace DL model:

```powershell
python jobs\train_marketplace_dl.py
```

Refresh marketplace predictions:

```powershell
python jobs\run_marketplace_predictions.py
```

Recommended full flow after scraping:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python jobs\run_scrapper.py --once
python jobs\run_data_processing.py --with-init-db
python jobs\train_marketplace_dl.py
python jobs\run_marketplace_predictions.py
```

## 12. Running the App Locally

### 12.1 Full app

```powershell
$env:PYTHONPATH = (Get-Location).Path
python run_all.py
```

Default URLs:

- UI: [http://127.0.0.1:5173](http://127.0.0.1:5173)
- API: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- API docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

If those ports are occupied, use explicit ports:

```powershell
python run_all.py --api-port 8001 --web-port 5174
```

### 12.2 API only

```powershell
$env:PYTHONPATH = (Get-Location).Path
uvicorn src.api.app:app --host 0.0.0.0 --port 8000
```

## 13. Running With Docker

### 13.1 Create `.env`

```powershell
Copy-Item .env.example .env
```

### 13.2 Start the stack

```powershell
docker compose up --build -d
```

Or:

```powershell
.\run_containers.ps1 -Build
```

URLs:

- Web UI: [http://localhost:3000](http://localhost:3000)
- API: [http://localhost:8000](http://localhost:8000)
- API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

### 13.3 Logs

```powershell
docker compose logs -f
docker compose logs -f api
```

### 13.4 Stop

```powershell
docker compose down
```

## 14. Core Runtime Flows

### 14.1 Search and assistant flow

1. User submits a query.
2. Assistant returns local DB results first.
3. Background online enrichment continues if needed.
4. Frontend polls phased search status.
5. Online results merge into the same session.
6. Follow-up questions stay on the selected product when appropriate.

### 14.2 Store product to assistant flow

From a store product page, `Ask AI About This Product` passes the concrete store product reference into the assistant. That prevents the assistant from incorrectly starting a fresh search when the product already exists inside the store.

### 14.3 Marketplace flow

1. Buyer or seller registers.
2. Seller publishes products.
3. Products appear in the store and assistant results.
4. Buyers review and order products.
5. Seller dashboard reflects orders, ratings, and reports.

## 15. API Surface

Important endpoints:

- `GET /health`
- `GET /health/details`
- `GET /search`
- `GET /recommend`
- `POST /assistant`
- `GET /assistant/conversations/{conversation_id}`
- `GET /assistant/conversations/{conversation_id}/search-status`
- `DELETE /assistant/conversations/{conversation_id}`
- `POST /interactions`

Marketplace/auth:

- `POST /store/auth/register`
- `POST /store/auth/login`
- `GET /store/auth/me`

Store/catalog:

- `GET /store/catalog`
- `GET /store/products/{product_id}`
- `GET /store/products/{product_id}/reviews`
- `POST /store/products/{product_id}/reviews`
- `DELETE /store/products/{product_id}/reviews/me`

Seller:

- `GET /store/seller/products`
- `POST /store/seller/products`
- `PUT /store/seller/products/{product_id}`
- `DELETE /store/seller/products/{product_id}`
- `GET /store/seller/orders`
- `PUT /store/seller/orders/{order_id}`
- `GET /store/seller/reports/summary`

Orders/reports:

- `POST /store/orders`
- `GET /store/orders/me`
- `GET /reports`
- `GET /reports/{report_id}`
- `GET /reports/{report_id}/pdf`
- `DELETE /reports/{report_id}`

Admin analytics:

- `GET /store/reports/source-ratings`
- `POST /store/admin/analytics/train`
- `POST /store/admin/analytics/predict`

## 16. Ratings Model in the UI

Products can expose two rating systems:

1. Source rating:
   - scraped from the original website
   - `source_rating`
   - `source_review_count`

2. In-app rating:
   - created by marketplace users
   - `app_rating`
   - `app_review_count`

If source ratings show `N/A`, that usually means source data has not been scraped or refreshed yet. Re-run scraping and processing to backfill those fields.

## 17. Marketplace Analytics and Deep Learning

The marketplace analytics layer adds:

- order counts
- units sold
- revenue
- interaction funnel data
- predicted app rating
- predicted demand score
- seasonal relevance
- best month labels

Relevant code:

- [F:\product search\src\ml\marketplace_dl\model.py](F:\product search\src\ml\marketplace_dl\model.py)
- [F:\product search\src\ml\marketplace_dl\features.py](F:\product search\src\ml\marketplace_dl\features.py)
- [F:\product search\src\ml\marketplace_dl\train.py](F:\product search\src\ml\marketplace_dl\train.py)
- [F:\product search\src\ml\marketplace_dl\infer.py](F:\product search\src\ml\marketplace_dl\infer.py)

## 18. Saved Reports and PDF Export

The platform auto-saves report-style outputs into report history.

Supported flows include:

- seller summary reports
- assistant-generated reports
- saved report detail pages
- PDF export through backend-generated files

PDFs are stored as report artifacts and can be downloaded again from report history.

## 19. Security Model

Default API-key mode:

```env
AUTH_MODE=api_key
SERVICE_API_KEY=your_service_key
ADMIN_API_KEY=your_admin_key
REQUIRE_AUTH_FOR_WRITE=true
```

JWT mode:

```env
AUTH_MODE=jwt
JWT_SECRET=your_jwt_secret
JWT_ALGORITHM=HS256
```

Marketplace buyer/seller auth is handled separately through marketplace tokens.

## 20. Operational Smoke Checks

### 20.1 Backend/API smoke test

```powershell
$env:PYTHONPATH = (Get-Location).Path
python jobs\smoke_assistant_api.py --base-url http://127.0.0.1:8000 --service-api-key YOUR_SERVICE_API_KEY --admin-api-key YOUR_ADMIN_API_KEY
```

### 20.2 Run tests

```powershell
$env:PYTHONPATH = (Get-Location).Path
pytest -q
```

### 20.3 Frontend build

```powershell
Set-Location "react UI"
npm run build
Set-Location ..
```

## 21. Delivery and Client Packaging

Generate the client package:

```powershell
.\jobs\create_client_delivery.ps1
```

For a smaller package without artifacts:

```powershell
.\jobs\create_client_delivery.ps1 -SkipArtifacts
```

Related handoff files:

- [F:\product search\CLIENT_DELIVERY.md](F:\product search\CLIENT_DELIVERY.md)
- [F:\product search\HOW_TO_RUN.txt](F:\product search\HOW_TO_RUN.txt)
- [F:\product search\CLIENT_RELEASE_VERSION.txt](F:\product search\CLIENT_RELEASE_VERSION.txt)

## 22. Troubleshooting

### Assistant says no products found

Check:

- Mongo is running
- scraping has been executed
- `run_data_processing.py` has been executed
- `offers_normalized` is populated

### Source ratings show `N/A`

Re-run:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python jobs\run_scrapper.py --once
python jobs\run_data_processing.py --with-init-db
```

### Store product is not understood by the assistant

The store-to-chat handoff now uses a product reference ID. If this breaks again, verify:

- store product page link includes `product=<product_id>`
- `POST /assistant` receives `reference_product_id`
- assistant state is seeded with the referenced store product

### Frontend gets `401 Unauthorized`

Check:

- `SERVICE_API_KEY` is set in `.env`
- `run_all.py` is restarted after env changes
- frontend dev server was started after the key was present

### Port already in use

Run on different ports:

```powershell
python run_all.py --api-port 8001 --web-port 5174
```

## 23. File-Level References

High-value entry points:

- API app: [F:\product search\src\api\app.py](F:\product search\src\api\app.py)
- Assistant: [F:\product search\src\agent\assistant.py](F:\product search\src\agent\assistant.py)
- Assistant memory: [F:\product search\src\agent\memory.py](F:\product search\src\agent\memory.py)
- Marketplace helpers: [F:\product search\src\core\marketplace.py](F:\product search\src\core\marketplace.py)
- Reports: [F:\product search\src\core\report_store.py](F:\product search\src\core\report_store.py)
- PDF reports: [F:\product search\src\core\pdf_report.py](F:\product search\src\core\pdf_report.py)
- Search pipeline: [F:\product search\src\core\search_pipeline.py](F:\product search\src\core\search_pipeline.py)
- MCP tools: [F:\product search\src\mcp\server.py](F:\product search\src\mcp\server.py)
- Marketplace analytics: [F:\product search\src\core\marketplace_analytics.py](F:\product search\src\core\marketplace_analytics.py)
- Frontend app shell: [F:\product search\react UI\App.tsx](F:\product search\react UI\App.tsx)
- Search page: [F:\product search\react UI\src\pages\Home.tsx](F:\product search\react UI\src\pages\Home.tsx)
- Store product page: [F:\product search\react UI\src\pages\StoreProduct.tsx](F:\product search\react UI\src\pages\StoreProduct.tsx)
- Seller dashboard: [F:\product search\react UI\src\pages\SellerDashboard.tsx](F:\product search\react UI\src\pages\SellerDashboard.tsx)

## 24. Recommended Daily Workflow

For active development:

```powershell
cd "F:\product search"
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = (Get-Location).Path
python jobs\run_scrapper.py --once
python jobs\run_data_processing.py --with-init-db
python run_all.py
```

For model refresh:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python jobs\run_training.py
python jobs\train_marketplace_dl.py
python jobs\run_marketplace_predictions.py
```

## 25. Notes

- The delivery ZIP does not contain your live `.env`.
- Mongo data is not embedded into the code package.
- `artifacts` are included in the default client package unless explicitly skipped.
- Local scraped/debug folders are excluded from client delivery.
