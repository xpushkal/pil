# PIL — privacy-preserving LLM proxy

PIL is a drop-in HTTPS proxy in front of OpenAI / Anthropic / Gemini that:

1. **Scrubs** PII and dev secrets (API keys, GitHub tokens, AWS creds, PEMs, JWTs, …)
   out of every prompt before it leaves your perimeter.
2. **Semantically caches** responses so identical-meaning prompts don't pay
   the upstream provider twice.
3. **Audits** every call with metadata only — zero-retention by default;
   raw payloads only land in storage if the org explicitly opts in, and
   are AES-256-GCM encrypted at rest.

You point your client at `localhost:8000/openai/v1/chat/completions` (or the
Anthropic/Gemini path) and PIL forwards verbatim to the real provider. The
upstream provider key on the inbound request is passed through and **never
stored**.

## Current sprint

This branch ships **Sprint 1 Phase 1**: scaffolding and infrastructure only.
The proxy/PII/cache pipeline lands in Phase 2 of the same sprint. See
[Docs/PIL_MVP_Plan_v1.pdf](Docs/PIL_MVP_Plan_v1.pdf) for the full spec.

What works today:
- FastAPI app boots, exposes `/health/live`, `/health/ready`, `/metrics`.
- Postgres + pgvector schema (incl. declarative-partitioned `semantic_cache`).
- Alembic migrations, forward + backward round-trip tested in CI.
- OTEL traces → Jaeger; Prometheus metrics; structured JSON logs to stdout.
- Docker image baked with `all-MiniLM-L6-v2` + `en_core_web_lg` so cold start
  doesn't fetch from the internet.
- GitHub Actions: lint, unit, integration (real Postgres + Redis), image
  build + smoke test.

Not yet wired (lands in Phase 2):
- `/openai/*`, `/anthropic/*`, `/gemini/*`, `/v1/messages` proxy endpoints
- `X-PIL-Key` auth + per-key rate limit
- Presidio scrubber + 8 custom recognizers
- Semantic cache lookup
- Audit log writes

Out of scope this whole sprint: compression, intent classifier, RAG,
budgets, SDK, `/stats`, bias flag, self-bypass breaker.

## Local-first quickstart (M4 Pro / 24GB)

PIL runs entirely on your laptop. No cloud is required for Sprint 1 or 2.

```bash
git clone git@github.com:xpushkal/pil.git
cd pil
cp .env.example .env
# (optional — only needed to make integration tests hit a real provider)
# echo "OPENAI_API_KEY=sk-..." >> .env

docker compose up --build
```

The first build bakes the ML models into the image (`sentence-transformers/all-MiniLM-L6-v2`
+ spaCy's `en_core_web_lg`) — expect ~5 minutes on a cold cache. Subsequent
boots cold-start in **< 30 seconds**.

Confirm it's alive:

```bash
curl -s http://localhost:8000/health/live   # {"status":"ok"}
curl -s http://localhost:8000/health/ready  # checks Postgres + Redis
curl -s http://localhost:8000/metrics | head
```

Run the migrations (one-time, against the running Postgres container):

```bash
docker compose exec app uv run alembic upgrade head
```

Jaeger UI: <http://localhost:16686>  ·  Prometheus exporter: <http://localhost:8889/metrics>

## Dev workflow

PIL uses [`uv`](https://github.com/astral-sh/uv) for dependency management.

```bash
# one-time
uv python install 3.12
uv sync --extra dev
uv run pre-commit install

# common
uv run pytest tests/unit              # fast, no services needed
uv run pytest tests/integration -m integration   # needs PG + Redis on :5432 / :6379
uv run ruff check .
uv run ruff format .
uv run mypy app/core
uv run alembic revision -m "describe change"
```

## Repository layout

```
app/
  main.py                      # FastAPI entry, lifespan, middleware
  settings.py                  # pydantic-settings, PIL_* env vars
  api/                         # health.py now; proxy.py + keys.py in Phase 2
  core/
    pii/recognizers/           # plugin loader picks any .py here at startup
    providers/                 # openai/anthropic/gemini adapters — Phase 2
  db/
    models.py
    session.py
    migrations/                # Alembic; 0001_initial.py creates the full schema
  observability/
    tracing.py                 # OTEL provider + FastAPI/HTTPX/SQLA/Redis instrumentors
    metrics.py                 # Prometheus collectors
    logging.py                 # structlog JSON to stdout, trace + request id auto-bound
  utils/                       # request id contextvar, redis client
config/
  cache.yaml                   # cache defaults, pre-warmed (provider, model) partitions
  otel-collector.yaml          # collector pipeline + payload-stripping processor
tests/
  unit/                        # no services needed
  integration/                 # needs real PG + Redis
  fixtures/
.github/workflows/ci.yml       # lint, type, unit, integration, build image
Dockerfile                     # python:3.12-slim, non-root user, models baked in
docker-compose.yml             # postgres+pgvector, redis, jaeger, otel-collector, app
```

## Defence-in-depth: no PII in logs

The OTEL collector applies an `attributes/strip_payloads` processor that
deletes any `gen_ai.prompt` / `gen_ai.completion` / `http.{request,response}.body`
attributes before exporting traces. App-side scrubbing should already
prevent these from being set; the collector strips them anyway as a
belt-and-braces measure.

The audit table (`requests`) stores **metadata only** — token counts,
PII categories, latency, trace id. Raw prompts/responses are only persisted
in `request_payloads` when the org has `raw_logging_opt_in = true`, and even
then they are AES-256-GCM encrypted with a per-org key.

## Government-ID handling

Per founder direction, the following Presidio entities are **disabled**
and never loaded: `US_SSN`, `US_DRIVER_LICENSE`, `US_PASSPORT`, `UK_NHS`,
`IN_AADHAAR`, `MEDICAL_LICENSE`. See
[app/core/pii/recognizers/README.md](app/core/pii/recognizers/README.md).

## License

Proprietary. All rights reserved.
