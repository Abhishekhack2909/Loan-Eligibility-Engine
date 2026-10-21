# Loan Eligibility Engine

The Loan Eligibility Engine accepts a customer CSV, streams validated rows into
PostgreSQL, evaluates deterministic loan-product criteria, stores the complete
match set, and lets n8n orchestrate product discovery, ranking, and recipient
notifications.

The reproducible default path is **local-only for build and test**. An approved
assignment sandbox was also deployed in `ap-south-1`; its live evidence and
screen-recording steps are in [docs/aws-demo-runbook.md](docs/aws-demo-runbook.md).
Do not repeat or expand the AWS deployment without reviewing cost, data, and
recipient scope.

## Ownership and source of truth

This local integration slice owns:

- `serverless.yml` — plausible AWS S3/EventBridge/SQS/Lambda/IAM/VPC design;
- `.env.example`, `.gitignore`, `.nvmrc`, and this README;
- `docs/` — local runbook, workflow contracts, and the interactive architecture
  diagram;
- `scripts/infra/` — thin Lambda adapters that reuse backend service ports;
- the checked-in local worker, Compose wiring, n8n exports, migrations, fixtures,
  Dockerfile, and Makefile used by the reproducible local path.

## Architecture

Open [the interactive architecture diagram](docs/architecture.html). Its
topology shows the AWS target path and the local equivalents side by side:

```text
upload client -> optional API -> MinIO/S3 -> object-created event
             -> event-router/EventBridge -> LocalStack SQS/SQS
             -> ingestion worker/Lambda -> PostgreSQL/RDS
             -> n8n Workflows A/B/C -> ranked notification
```

For the completed AWS sandbox walkthrough, use
[docs/aws-demo-runbook.md](docs/aws-demo-runbook.md).

The AWS portion is parameterized for an existing private PostgreSQL/RDS
deployment. S3 is private and encrypted, EventBridge sends object-created
events to SQS, SQS has a DLQ, Lambda processing is VPC-attached, Secrets Manager
values are referenced by ARN, and CloudWatch log retention is 14 days. API
Gateway routes remain optional and can be disabled through the
`EnableApiRoutes` CloudFormation parameter; the approved demonstration
sandbox enables them with an API key.

When the approved AWS environment supplies the n8n callback URL and its
Secrets Manager-backed webhook secret, the ingestion Lambda sends the same
signed completion event as the local worker. A callback failure is retryable;
database idempotency makes the retried CSV safe. The demonstration sandbox
uses a private VPC callback to the EC2-hosted n8n instance.

## Runtime and dependency pins

- Python: `3.12` via `backend/.python-version`.
- Node tooling: `22.14.0` via `.nvmrc`.
- Python dependencies: exact pins in `backend/pyproject.toml`, with the
  repository lockfile at `backend/uv.lock`.
- The local workflow uses placeholder values from `.env.example`; copy it to
  `.env` and never commit `.env`.

## Sample CSV findings

The reviewed sample contains **10,000 data rows**, **7 columns**, repeated email
values, and exactly these fields:

| Field | Meaning and validation |
| --- | --- |
| `user_id` | Required non-empty text identifier. |
| `name` | Required non-empty display name. |
| `email` | Required email; normalized to lowercase for validation and recipient handling. Repeated values are retained. |
| `monthly_income` | Non-negative decimal amount. |
| `credit_score` | Integer from 0 through 850. |
| `employment_status` | Required non-empty text used by supported criteria. |
| `age` | Integer from 0 through 150. |

There are no extra columns in this finding. The parser does permit extra
columns and preserves them as `raw_data`, but product matching uses only the
known fields above. A repeated email does not mean duplicate users: the stable
ingestion identity is `(upload_batch_id, source_row_number)` and the database
keeps those rows separately.

## Local-only setup

### Prerequisites

Install or make available locally, without global project changes:

- Python 3.12 and `uv`;
- Node 22.14.0 if Serverless syntax inspection is needed (do not deploy);
- PostgreSQL 15+ (the Compose database is exposed to host tools on port `15432`
  to avoid colliding with an existing local PostgreSQL on `5432`);
- Docker Desktop for the checked-in PostgreSQL, MinIO, event-router, LocalStack,
  ingestion worker, Mailpit, n8n, LLM stub, backend, and web services.

The local stack files are outside this ownership slice and must be treated as
collaborator-owned when changing them.

PowerShell:

```powershell
Copy-Item .env.example .env
uv sync --project backend --group test
```

Start the checked-in local stack without AWS:

```bash
docker compose up --build
```

Bash:

```bash
cp .env.example .env
uv sync --project backend --group test
```

Apply the schema to the local database:

```bash
psql "$DATABASE_DSN" -f migrations/001_initial.sql
```

See [docs/local-runbook.md](docs/local-runbook.md) for the full local wiring,
demo sequence, and troubleshooting.

## Run commands

Start the FastAPI app from the repository root:

```bash
uv run --project backend uvicorn backend.app.main:app --reload
```

Check health without external services:

```bash
curl -sS http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

The upload endpoints require local PostgreSQL and MinIO. The local event path
is MinIO webhook -> event-router -> LocalStack SQS
`loan-eligibility-events` -> ingestion worker. The local router sends a
normalized `{"source":"minio","records":[{"bucket":"loan-uploads","key":"uploads/{upload_id}/{filename}"}]}`
body. The AWS EventBridge detail shape is a separate contract used by the AWS
Lambda adapter in `scripts/infra/`.

## Test command

Use this one offline backend test command:

```bash
uv run --project backend pytest tests/backend
```

The suite uses injected boundaries and does not call AWS, PostgreSQL, MinIO,
n8n, Mailpit, SES, public web pages, or an LLM service. Do not replace the
offline LLM stub with a network model in this check.

## n8n setup and the three workflows

Compose provisions the three exports and one PostgreSQL credential before the
first n8n start through `local/n8n-bootstrap.js`. The persistent marker
`/home/node/.n8n/.loan-engine-provisioned-v1` makes the operation idempotent on
restart. The credential is named `Postgres local (configure in n8n)` and uses
`postgres:5432`, database `loan_engine`, user `loan_engine`, the
`POSTGRES_PASSWORD` value, and SSL off. Workflow C calls Mailpit's local HTTP
API; it does not need an SMTP credential. The optional local model API uses
`LLM_API_BASE_URL` and `LLM_MODEL`; the default `LLM_MODE=stub` path uses only
`http://llm-stub:8090`. The incoming completion webhook uses the shared secret
and HMAC headers, not a committed credential, and rejects non-completed
ingestion events.

### Workflow A — public product discovery

Workflow A fetches three personal-loan pages when `LOAN_SOURCE_MODE=public`,
captures retrieval metadata and a content hash, and parses criteria. The compose
default is `fixture` for offline repeatability; its three fixture nodes connect
directly to extraction and do not wait for the public branch. Every product
retains a public provenance URL plus `source_mode=fixture` or `public`. Offline
tests use the LLM stub; `LLM_MODE=openai-compatible` uses
`LLM_API_BASE_URL/chat/completions` with `LLM_MODEL`. The LLM receives public
product text only and cannot invent an unrecognized matcher criterion silently.

### Workflow B — deterministic eligibility and ranking

After the signed ingestion callback, evaluate every user/product pair using
the known fields and criteria. Persist every deterministic match, including
`eligible`, `ineligible`, `may_qualify`, and legacy `unknown` decisions. SQL
provides only the candidate metric; the n8n code evaluates and stores every
user/product pair. Ranking is a presentation operation over the persisted
result: top-K changes which rows are shown, not which matches are evaluated or
stored.

### Workflow C — notification

Normalize recipients, group by user ID plus recipient, include eligible,
may-qualify, and unknown matches, create delivery records, and send locally
through the Mailpit HTTP API or through the controlled AWS SES branch. A
no-match run returns one summary without waiting on a provider branch. The AWS
demo branch fails closed unless the recipient is an explicitly configured
controlled alias. Mailpit demonstrates only a local delivery handoff; it is
not proof of SES configuration, identity verification, sandbox exit,
deliverability, or production quotas. Completion is returned to Workflow B as
one summary; the ingestion callback into B is signed.

The full node-level contract, credential table, and error handling are in
[docs/workflows.md](docs/workflows.md).

## Optimization semantics and actual metrics

The optimization boundary is deliberately narrow:

1. deterministic matching evaluates and stores **all** user/product pairs;
2. top-K is applied only after persistence, for ranking or notification display;
3. actual metrics remain calculated from ingestion outcomes and stored matches.

For ingestion, the invariant is:

```text
total_rows = inserted_rows + duplicate_rows + invalid_rows + failed_rows
accepted_rows = inserted_rows + duplicate_rows
```

Replaying an upload may increase `duplicate_rows`, but it cannot create a second
row for the same `(upload_batch_id, source_row_number)`. A top-K setting must
never be used to report a smaller actual match count.

## Demo script

1. Copy `.env.example` to `.env` and run `docker compose up --build` for the
   checked-in local services.
2. Wait for n8n health; its bootstrap imports A/B/C and creates the Postgres
   credential automatically. No UI import or credential secret is required.
3. Start the backend with the run command above, or use the Compose backend.
4. `POST /uploads` with `{"filename":"sample.csv"}` and save `upload_id` and
   `upload_url`.
5. `PUT` a CSV with the exact seven-column header to the presigned URL.
6. Follow the message through MinIO webhook, event-router, LocalStack SQS, and
   the ingestion worker.
7. Confirm upload status, then let Workflow A use a public page or its marked
   fixture fallback.
8. Run Workflow B and inspect that all pairs are persisted before applying
   top-K.
  9. Run Workflow C, inspect Mailpit for local mode, or inspect the AWS n8n
     execution and controlled SES delivery for the AWS demo.

### Verified local smoke run

The checked local smoke run used `tests/fixtures/smoke-users.csv` and verified:

```text
presigned upload -> MinIO -> event-router -> LocalStack SQS
-> ingestion worker -> PostgreSQL -> n8n B -> n8n C -> Mailpit
```

Observed result: one user row was persisted, three public-source products were
present in `loan_products`, three deterministic matches were stored, and one
`sent` Mailpit delivery was recorded for the controlled local recipient
`demo@example.test`. Workflow A was also executed against the real Discover,
NerdWallet, and Prosper pages; any failed fetch is explicitly marked and uses a
fixture fallback. The local LLM stub was used for deterministic offline tests.
This does **not** prove AWS SES delivery.

### Two-user notification-only demo

For a safe notification demonstration using the existing 10,000-row batch, use
the checked fixture `tests/fixtures/demo-notification-two-users.json`. It targets
only two users whose matches are already stored and maps delivery to:

```text
complexhat735+one@gmail.com
complexhat735+two@gmail.com
```

The users' original `users.email` values remain unchanged in PostgreSQL. The
demo webhook accepts only one or two explicitly named targets, requires the
local demo token, checks both aliases against `DEMO_RECIPIENTS`, and refuses to
use the source CSV address as a recipient. It does not re-ingest the CSV or
re-run matching. `AWS_SES_ENABLED=false`; delivery is Mailpit only.

Run it locally with:

```powershell
curl.exe -X POST http://localhost:5678/webhook/loan-demo-notification `
  -H "Content-Type: application/json" `
  --data-binary "@tests/fixtures/demo-notification-two-users.json"
```

Then verify two new messages at [Mailpit](http://localhost:8025), and verify
the two `eligible-loans-demo-v1` rows in `email_deliveries`. Do not edit or
delete the existing blocked-delivery rows.

For copy/paste commands and the intentionally invalid-row demonstration, see
[docs/local-runbook.md](docs/local-runbook.md).

## AWS sandbox deployment

The approved sandbox was deployed in `ap-south-1` as
`loan-eligibility-engine-sandbox`. The exact evidence path is in
[docs/aws-demo-runbook.md](docs/aws-demo-runbook.md). The following commands
must not be repeated casually because they can create or update billable
resources:

```bash
# Repeat only after explicit approval and a fresh change-set review:
npx serverless deploy --stage sandbox --region ap-south-1

# Repeat only after explicit approval and a fresh change-set review:
aws cloudformation deploy --template-file .serverless/cloudformation-template-update-stack.json --stack-name loan-eligibility-engine
```

The sandbox now enables the authenticated upload API, uses a private RDS
instance, keeps SES in sandbox mode, and runs the AWS n8n Docker Compose host
inside the same VPC. The AWS ingestion Lambda calls the private n8n webhook;
the AWS n8n workflows use the same RDS and the controlled SES aliases.

The repeatable demo deployment helper is:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\infra\deploy_aws_demo.ps1 `
  -Profile loan-engine -Region ap-south-1 -StackName loan-eligibility-engine-sandbox
```

It provisions the demo n8n EC2 host, private database/security-group access,
Secrets Manager references, the authenticated API route, and the AWS n8n
workflow bundle. It writes the API endpoint, controlled demo key, and n8n URL
to the ignored local file `aws-build/aws-demo-ui.env`; never commit or share
that file.
Before any expansion, review the existing-RDS network path, dependency layer
ARN, secret ARNs, API authentication/WAF plan, log/retention cost, S3 lifecycle,
and data retention.

## Collaborator checklist

- [ ] Keep backend, migrations, tests, n8n, web, local, LLM stub, Docker, and
      Makefile changes in their owners' paths.
- [ ] Keep the local PostgreSQL/MinIO/event-router/LocalStack/Mailpit/n8n stack
      reproducible without committing credentials or volumes.
- [ ] Confirm the event-router preserves the documented normalized local
      envelope; review the AWS SQS DLQ path separately.
- [ ] Configure n8n credentials in its local credential store and verify HMAC
      callback handling before any non-local endpoint is trusted.
- [ ] Keep Workflow A fixture fallbacks explicitly marked and keep the offline
      LLM stub enabled for tests.
- [ ] Verify all deterministic matches are stored before top-K ranking.
- [ ] Verify actual metrics obey the partition invariant after a replay.
- [ ] Keep SES disabled for local work; a Mailpit pass is not an SES test.
- [ ] Obtain explicit approval before any AWS deployment or billable-service
      experiment.

## Troubleshooting and limitations

Common failures and the local remedies are documented in
[docs/local-runbook.md](docs/local-runbook.md). The most important boundaries
are:

- public page content is mutable; fixture fallback is necessary for repeatable
  local demos and must be labeled;
- the LLM stub and an actual local model API are optional extraction aids, not
  deterministic eligibility authorities;
- SES is configured and has a controlled AWS test; Mailpit still cannot prove
  SES behavior;
- the AWS sandbox has been deployed and the one-row end-to-end path has been
  verified. Review its dependency layer, private network, existing RDS, secret
  ARNs, demo API key, and EC2 n8n security-group rule before repeating it;
- local service definitions are outside this owned slice, so their health,
  credentials, and network aliases must be checked with their owners.
