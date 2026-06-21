# AI-Powered Transaction Processing Pipeline

A production-grade async backend that accepts dirty financial CSVs, processes them through a job queue, uses an LLM to classify and summarise transactions, and exposes a clean polling API.

## Architecture

```
┌──────────┐   POST /jobs/upload   ┌────────────┐   enqueue   ┌────────────────┐
│  Client  │ ─────────────────────▶│  FastAPI   │ ──────────▶│ Celery Worker  │
└──────────┘                       │  (async)   │             │                │
      ▲                            └────────────┘             │ 1. Clean CSV   │
      │                                  │                    │ 2. Detect ─────┤
      │  GET /jobs/{id}/status           │ read/write         │    anomalies   │
      │  GET /jobs/{id}/results          ▼                    │ 3. LLM batch ──┤──▶ Gemini Flash
      │                            ┌────────────┐             │    classify    │     (free tier)
      └────────────────────────────│ PostgreSQL │◀────────────│ 4. LLM        │
                                   └────────────┘   persist   │    narrative   │
                                                              └────────────────┘
                                   ┌────────────┐
                                   │   Redis    │◀── Celery broker + result backend
                                   └────────────┘
```

**Request lifecycle:** `POST /jobs/upload` → validates CSV → creates `Job(status=pending)` in Postgres → enqueues Celery task → returns `job_id` immediately. Worker picks up the task, runs the full pipeline, and updates `Job(status=completed)` + stores `Transaction` rows + `JobSummary`. Client polls `GET /jobs/{job_id}/status` until `completed`, then fetches `GET /jobs/{job_id}/results`.

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI + async SQLAlchemy |
| Database | PostgreSQL 16 |
| Queue | Celery 5 + Redis 7 |
| LLM (primary) | Gemini 1.5 Flash (free tier) |
| LLM (fallback) | OpenAI GPT-4o-mini |
| Containers | Docker + Docker Compose |
| Monitoring | Flower (Celery dashboard) |

## Setup

### Prerequisites
- Docker and Docker Compose installed
- A free Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

### 1. Clone the repo
```bash
git clone https://github.com/Akleshsoni/alemeno-transaction-pipeline.git
cd alemeno-transaction-pipeline
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY
```

### 3. Start everything
```bash
docker compose up --build
```

All services start automatically:
- **API** → `http://localhost:8000`
- **API Docs** → `http://localhost:8000/docs`
- **Flower** (queue monitor) → `http://localhost:5555`

## API Endpoints

### Upload a CSV
```bash
curl -X POST http://localhost:8000/jobs/upload \
  -F "file=@transactions.csv"
```
Response:
```json
{
  "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "pending",
  "message": "Job enqueued. Poll GET /jobs/{job_id}/status for updates."
}
```

### Check job status
```bash
curl http://localhost:8000/jobs/3fa85f64-5717-4562-b3fc-2c963f66afa6/status
```
Response (while processing):
```json
{
  "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "processing",
  "filename": "transactions.csv",
  "row_count_raw": 95,
  "row_count_clean": 0,
  "created_at": "2024-07-15T10:00:00Z",
  "completed_at": null,
  "error_message": null,
  "summary": null
}
```

Response (completed):
```json
{
  "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "completed",
  "row_count_raw": 95,
  "row_count_clean": 72,
  "summary": {
    "total_spend_inr": 842391.50,
    "total_spend_usd": 54281.20,
    "top_merchants": ["IRCTC", "Jio Recharge", "Flipkart"],
    "anomaly_count": 7,
    "narrative": "The account shows high utility and travel spend...",
    "risk_level": "medium",
    "category_breakdown": {"Travel": 320000, "Utilities": 180000}
  }
}
```

### Get full results
```bash
curl http://localhost:8000/jobs/3fa85f64-5717-4562-b3fc-2c963f66afa6/results
```
Returns cleaned transactions list, flagged anomalies, category breakdown, and LLM narrative.

### List all jobs
```bash
curl http://localhost:8000/jobs
# Filter by status:
curl "http://localhost:8000/jobs?status=completed"
```

### Health check
```bash
curl http://localhost:8000/health
```

## Processing Pipeline

When a job is dequeued, the worker executes these steps in order:

**a) Data Cleaning**
- Normalise `DD-MM-YYYY` and `YYYY/MM/DD` → ISO 8601
- Strip `$` prefix from amounts
- Uppercase currency and status fields
- Fill missing categories with `None` (sent to LLM)
- Remove exact duplicate rows (SHA-256 fingerprint)

**b) Anomaly Detection**
- Flag `amount > 3× per-account median` as statistical outlier
- Flag USD transactions at domestic-only merchants (Swiggy, Ola, IRCTC, Zomato, Jio)

**c) LLM Classification (batched)**
- Rows with no category are batched (15 rows/call) and sent to Gemini Flash
- Assigns: Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, Other
- Retry: 3 attempts with exponential backoff; failed batches marked `llm_failed=true`

**d) LLM Narrative Summary**
- Single LLM call produces: total spend by currency, top 3 merchants, anomaly count, spending narrative, risk level

## Running Tests

```bash
# Inside the container:
docker compose exec api pytest tests/ -v

# Or locally (with Python 3.12+):
pip install -r requirements.txt
pytest tests/ -v
```

## Data Model

```
Job
  id (UUID PK)  filename  status  row_count_raw  row_count_clean
  created_at    completed_at    error_message

Transaction
  id (UUID PK)  job_id (FK)  txn_id  date  merchant  amount  currency
  status  category  account_id  notes
  is_anomaly  anomaly_reason  llm_category  llm_raw_response  llm_failed

JobSummary
  id (UUID PK)  job_id (FK)  total_spend_inr  total_spend_usd
  top_merchants (JSONB)  anomaly_count  narrative  risk_level
  category_breakdown (JSONB)
```

## Scalability Notes

**Current bottleneck at 100× traffic:** The Celery worker pool (4 concurrent workers) and single PostgreSQL connection pool (10 connections) would saturate. LLM API rate limits (~60 RPM on Gemini free tier) would also become the ceiling.

**For enterprise scale:**
- Horizontal Celery workers behind a load balancer
- PostgreSQL read replicas + PgBouncer connection pooler
- LLM caching layer (Redis) for repeated merchant/category combos
- Switch to Gemini Pro or Anthropic API with higher rate limits
- Consider streaming results instead of polling (WebSockets or SSE)

## License
MIT
