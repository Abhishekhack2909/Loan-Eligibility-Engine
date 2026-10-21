# Local runbook

This runbook is the supported operating mode for the current project slice.
It uses local services only and does not require an AWS account or billable
cloud services.

## Service wiring

The checked-in local equivalent of the target AWS path is:

```text
MinIO bucket (S3 API)
  -> MinIO webhook notification (/events/minio)
  -> local event-router (normalizes Records)
  -> LocalStack SQS: loan-eligibility-events
  -> local ingestion-worker
  -> PostgreSQL + signed n8n callback
```

`docker-compose.yml` configures MinIO's webhook target as
`http://event-router:8091/events/minio`. `local/minio_bootstrap.py` creates
`loan-uploads` and registers the explicit object-created notification through
the S3-compatible API. `local/event_router.py` accepts
MinIO's `Records` payload, validates bucket/key pairs, URL-decodes each key,
and sends this normalized body to LocalStack SQS:

```json
{"source":"minio","records":[{"bucket":"loan-uploads","key":"uploads/{upload_id}/{filename}"}]}
```

`local/localstack-init.sh` creates the `loan-eligibility-events` queue.
`local/ingestion_worker.py` consumes it, streams the object from MinIO through
the shared backend CSV parser/service in bounded batches, writes rows/errors to
PostgreSQL, and posts the signed completion event to n8n. Local and AWS use the
same callback fields, including top-level `status`, and the same UTF-8 canonical
JSON signing. The AWS target in `serverless.yml` uses the AWS-native
EventBridge S3 detail shape before its adapter reaches this shared service.
The current local worker is not an AWS deployment and is not replaced by this
documentation slice.

## Setup

PowerShell:

```powershell
Copy-Item .env.example .env
uv sync --project backend --group test
```

Bash:

```bash
cp .env.example .env
uv sync --project backend --group test
```

Start the checked-in local PostgreSQL, MinIO, event-router, LocalStack,
ingestion worker, Mailpit, n8n, and backend services:

```bash
docker compose up --build
```

The Compose bootstrap creates the `loan-uploads` bucket, the
`loan-eligibility-events` queue, and the PostgreSQL schema on a fresh database.
The n8n container runs `local/n8n-bootstrap.js` before starting n8n; the script
imports all three workflows and creates the PostgreSQL credential. Its marker
prevents duplicate imports on restart. If an existing volume predates this
bootstrap, remove only the local `n8n-data` volume and start the stack again,
or import the schema manually:

```bash
psql "$DATABASE_DSN" -f migrations/001_initial.sql
```

The main host endpoints are backend `http://localhost:8000`, web UI
`http://localhost:8080`, MinIO `http://localhost:9000` (console `:9001`),
event-router `http://localhost:8091`, ingestion-worker health `:8092`,
LocalStack `http://localhost:4566`, n8n `http://localhost:5678`, Mailpit
`http://localhost:8025`, and the LLM stub `http://localhost:8090`. PostgreSQL
is available to host tools at `localhost:15432` (containers use
`postgres:5432`). Inside a
container use service names such as `minio`, `event-router`, `localstack`,
`postgres`, `n8n`, `mailpit`, and `llm-stub`.

## Run the backend

```bash
uv run --project backend uvicorn backend.app.main:app --reload
```

The supported smoke endpoint is `GET http://localhost:8000/health`. Upload
creation and status require PostgreSQL and MinIO to be reachable.

## Local event test

1. `POST /uploads` with `{"filename":"sample.csv"}`.
2. Upload the returned presigned URL with `PUT`, using the exact CSV content
   type from the response request.
3. Confirm MinIO emits its webhook and the event-router sends the normalized
   `source=minio` message to the LocalStack `loan-eligibility-events` queue.
4. Watch the ingestion worker logs. It fetches the object, records row-level
   errors, writes rows, and completes the upload status before calling n8n.
5. Re-deliver the same SQS message and confirm duplicate rows increase while
   no second user row is created.
6. Let n8n receive the signed completion callback and continue through
   Workflows B and C.

## Demo script

The following is a host-shell demonstration outline. Replace the placeholder
`$UPLOAD_URL` with the URL returned by the first command and keep the CSV
schema exact:

```bash
curl -sS -X POST http://localhost:8000/uploads \
  -H 'content-type: application/json' \
  -d '{"filename":"sample.csv","content_type":"text/csv"}'

cat > sample.csv <<'CSV'
user_id,name,email,monthly_income,credit_score,employment_status,age
demo-1,Ada Lovelace,ada@example.test,5000,720,employed, thirty
CSV

curl -sS -X PUT "$UPLOAD_URL" \
  -H 'content-type: text/csv' \
  --upload-file sample.csv

curl -sS http://localhost:8000/uploads/$UPLOAD_ID
```

For a successful path, change the sample age to `30`; the intentionally bad
`thirty` value is useful for demonstrating a recoverable invalid-row record.
Capture the upload ID from the first response before running the final status
request. Then inspect the event-router, LocalStack SQS, PostgreSQL, n8n
execution, and Mailpit UI in that order.

## n8n credentials and workflow import

The Compose service mounts `n8n/` at `/data/workflows` and runs
`local/n8n-bootstrap.js` before starting n8n. The script imports
`01-public-loan-catalog.json`, `02-ingestion-match.json`, and
`03-email-delivery.json`, then creates the credential named
`Postgres local (configure in n8n)` with host `postgres`, port `5432`, database
`loan_engine`, user `loan_engine`, the `POSTGRES_PASSWORD` value, and SSL
disabled. It writes the marker
`/home/node/.n8n/.loan-engine-provisioned-v1` only after all imports succeed, so
normal restarts do not duplicate them. The SES branch requires an AWS
credential whose name matches `AWS_SES_CREDENTIAL_NAME`. It is disabled in
local mode; the controlled AWS demo host enables it separately.

Workflow B receives the backend callback at
`/webhook/loan-ingestion-completed`. It verifies the shared HMAC signature,
canonical UTF-8 JSON, timestamp window, and top-level `status=completed` before
doing work. Its idempotency insert branches to a duplicate response before any
user/product query. The shared secret belongs in Compose environment/configuration
and must not be committed.

Workflow A defaults to fixtures and connects each of the three fixture files
directly to extraction, so it does not wait for the unexecuted public branch.
The public branch retains three personal-loan HTTP Request nodes. Set
`LLM_MODE=stub` for the default local service; set `LLM_MODE=openai-compatible`
to call a local OpenAI-compatible `/chat/completions` endpoint using
`LLM_API_BASE_URL` and `LLM_MODEL`. Only public product text is sent to either
branch.

## Checks

## Safe two-user notification demo

The 10,000-row batch already exists locally. Do not upload it again and do not
run Workflow B for this demo. The checked fixture
`tests/fixtures/demo-notification-two-users.json` targets exactly two existing
matched users and maps them to the two allowlisted aliases. Their original CSV
emails remain unchanged in `users`.

Confirm the local environment has only these notification recipients and SES is
disabled:

```powershell
docker compose exec n8n node -e "console.log(process.env.DEMO_RECIPIENTS, process.env.AWS_SES_ENABLED)"
```

Trigger only Workflow C:

```powershell
curl.exe -X POST http://localhost:5678/webhook/loan-demo-notification `
  -H "Content-Type: application/json" `
  --data-binary "@tests/fixtures/demo-notification-two-users.json"
```

Expected response:

```json
{"status":"completed","deliveries_recorded":2,"no_matches":false}
```

Open `http://localhost:8025` and show the two new messages addressed to the two
aliases. Then query PostgreSQL:

```powershell
docker compose exec postgres psql -U loan_engine -d loan_engine -c "SELECT u.name, u.email AS original_email, ed.recipient, ed.status, ed.template_key FROM email_deliveries ed JOIN users u ON u.id=ed.user_id WHERE ed.template_key='eligible-loans-demo-v1' ORDER BY ed.recipient;"
```

The result must show two `sent` rows, while `original_email` still contains the
source CSV address. Do not modify the earlier `failed` delivery records.

## Automated checks

The single offline project test command is:

```bash
make test
```

The Python test portion alone is:

```bash
uv run --project backend pytest tests/backend
```

It does not require PostgreSQL, MinIO, LocalStack, n8n, Mailpit, AWS
credentials, or public internet pages. The live local run above is an
integration smoke test and requires the checked-in local services.

## Troubleshooting

- **`503 DATABASE_DSN is not configured`:** load `.env` into the backend process,
  check that PostgreSQL is listening, and apply `migrations/001_initial.sql`.
- **Presigned URL points at the wrong host:** use `S3_ENDPOINT_URL` visible to
  the caller; a container-only `minio` hostname cannot be uploaded from the
  host without a host mapping.
- **No SQS message:** check the MinIO webhook target, event-router route, bucket
  name, object key prefix, and LocalStack queue URL. The router must send the
  normalized `source` plus bucket/key records, not only a filename. The local
  queue has no AWS DLQ; the AWS target's SQS redrive policy is documented in
  `serverless.yml`.
- **AWS messages go to the DLQ:** inspect the failed Lambda record and confirm
  the key contains a valid UUID in `uploads/{uuid}/...`; then check PostgreSQL
  connectivity, the dependency layer, and the versions from `backend/uv.lock`.
- **Callback rejected by n8n:** compare the canonical JSON body, timestamp,
  shared secret, and `sha256=` signature. Do not substitute a generic webhook
  authentication header.
- **Mailpit is empty:** verify Workflow C uses SMTP host `mailpit` from inside
  n8n, not `localhost`, and inspect the delivery record.
- **LLM branch fails:** set `LLM_MODE=stub` for offline tests. A local model
  API is optional and must not be required for deterministic matching.
