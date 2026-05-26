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

## What ships in v0.1.0-sprint1

The Sprint 1 done-when condition is met: a real OpenAI request can be sent
through PIL, gets PII-and-secret scrubbed, semantic-cache-checked, audit-
logged, and forwarded with `X-PIL-*` response headers. See
[Docs/PIL_MVP_Plan_v1.pdf](Docs/PIL_MVP_Plan_v1.pdf) for the full spec.

What works:
- `/openai/{path}`, `/anthropic/{path}`, `/gemini/{path}`, `/v1/messages`
  drop-in proxy endpoints (streaming supported; tool/function-calling
  passed through).
- `X-PIL-Key` auth (argon2id-hashed), per-key Redis sliding-window rate
  limit, key rotation with 24h grace.
- Presidio scrubber with 9 enabled built-ins + 8 custom dev-secret
  recognizers (OpenAI / Anthropic / AWS / GitHub / private keys / DB
  connection strings / JWT). Gov-ID entities hard-disabled per founder
  direction (see [app/core/pii/recognizers/README.md](app/core/pii/recognizers/README.md)).
- Reversible + one-way PII modes; reversible mapping in Redis with TTL.
- Embedding service (`all-MiniLM-L6-v2`, MPS auto) loaded once at startup
  — the same instance feeds Sprint 2 RAG.
- Semantic cache (pgvector cosine, 0.92 threshold, per-`(org, provider,
  model)` isolation, TTL overrides per org).
- Audit log: metadata-only by default; opt-in encrypted payloads (AES-GCM
  per-org DEK).
- Observability: OTEL traces → Jaeger, Prometheus metrics, structlog JSON
  to stdout. Collector strips any `gen_ai.prompt` / `gen_ai.completion`
  attributes as defence-in-depth.
- Response headers on every call: `X-PIL-Request-Id`, `X-PIL-PII-Entities`
  (cat:count list), `X-PIL-Cache-Hit`, `X-PIL-Latency-Ms`.

Out of scope this sprint (lands in Sprint 2): compression engine, intent
classifier, RAG / document upload, Python SDK, `X-PIL-Tokens-*` headers.
Out of scope Sprint 3: `/stats`, bias flag, self-bypass breaker, citation
grounding, rolling summarizer.

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

Bootstrap an org + first key (use the `PIL_MASTER_ENCRYPTION_KEY` from your
`.env` as the admin token):

```bash
ORG_ID=$(curl -s -X POST http://localhost:8000/api/v1/orgs \
  -H "X-PIL-Admin-Token: $(grep PIL_MASTER_ENCRYPTION_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"name":"My Co"}' | jq -r .id)

PIL_KEY=$(curl -s -X POST http://localhost:8000/api/v1/orgs/$ORG_ID/keys \
  -H "X-PIL-Admin-Token: $(grep PIL_MASTER_ENCRYPTION_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"name":"laptop"}' | jq -r .plaintext)

echo "$PIL_KEY"   # store it — returned exactly once
```

Send a real OpenAI request through PIL:

```bash
curl -sD - http://localhost:8000/openai/v1/chat/completions \
  -H "X-PIL-Key: $PIL_KEY" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[
    {"role":"user","content":"Email me at alice@example.com about ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
  ]}'
```

The response includes `X-PIL-PII-Entities`, `X-PIL-Cache-Hit`,
`X-PIL-Latency-Ms`, `X-PIL-Request-Id`. The body that actually reached
OpenAI had the email and GitHub token replaced with `<EMAIL_ADDRESS_1>` /
`<GITHUB_TOKEN_1>` placeholders; PIL restored them in the response.

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
